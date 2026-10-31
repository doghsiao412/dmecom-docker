"""Local recording query controls, including cross-day ranges."""
import datetime as dt
import html
import re
import urllib.parse
from recording_query import CALL_TYPES,SORTS,PERIODS

def esc(value):return html.escape(str(value),quote=True)
def parse_filters(query,today=None):
    today=today or dt.date.today()
    def value(key,default=''):return query.get(key,[default])[0]
    if 'start' not in query and 'end' not in query:
        day=dt.date.fromisoformat(value('date',today.isoformat()));start=day.isoformat()+'T00:00:00';end=day.isoformat()+'T23:59:59'
    else:start=value('start');end=value('end')
    def timestamp(text):
        if not text:return ''
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?',text):raise ValueError('請輸入有效的開始／結束日期時間')
        return dt.datetime.fromisoformat(text).isoformat(timespec='seconds')
    start=timestamp(start);end=timestamp(end)
    if start and end and start>end:raise ValueError('開始時間不可晚於結束時間')
    phone=value('phone').strip()
    if len(phone)>80:raise ValueError('電話查詢最多 80 個字元')
    direction=value('direction','ALL').upper()
    if direction not in ('ALL','IN','OUT','UNKNOWN'):raise ValueError('無效來去電條件')
    try:page=int(value('page','1'))
    except ValueError:raise ValueError('頁碼必須為整數') from None
    if not 1<=page<=1000000:raise ValueError('頁碼超出範圍')
    call_type=value('call_type','ALL').upper()
    if call_type not in CALL_TYPES:raise ValueError('無效通話類型')
    try:line=int(value('line','0') or 0)
    except ValueError:raise ValueError('無效線路') from None
    if line not in (0,1,2,3,4):raise ValueError('無效線路')
    def seconds(key):
        text=value(key).strip()
        if not text:return None
        if not text.isdigit() or int(text)>86400:raise ValueError('秒數須為 0～86400 的整數')
        return int(text)
    min_seconds,max_seconds=seconds('min_seconds'),seconds('max_seconds')
    if min_seconds is not None and max_seconds is not None and min_seconds>max_seconds:raise ValueError('最短秒數不可大於最長秒數')
    sort=value('sort','time')
    if sort not in SORTS:raise ValueError('無效排序方式')
    return dict(start=start,end=end,phone=phone,direction=direction,page=page,call_type=call_type,line=line,min_seconds=min_seconds,max_seconds=max_seconds,sort=sort)

KEYS=('start','end','phone','direction','page','call_type','line','min_seconds','max_seconds','sort')
def query_items(filters,**overrides):
    data={k:v for k,v in filters.items() if k in KEYS};data.update(overrides)
    # Keep URLs short: drop defaults.
    defaults=dict(call_type='ALL',line=0,min_seconds=None,max_seconds=None,sort='time')
    return {k:('' if v is None else v) for k,v in data.items() if not (k in defaults and v==defaults[k])}

def link(filters,**overrides):
    return '/?'+urllib.parse.urlencode(query_items(filters,**overrides))
def export_link(filters):return '/recordings/export.xlsx?'+urllib.parse.urlencode(query_items(filters,page=1))
def stats_link(filters,period='month'):
    # Statistics default to all dates; the stats page has its own range form.
    return '/stats?'+urllib.parse.urlencode(dict(query_items(filters,start='',end='',page=1),period=period))

def select(name,choices,current):
    return '<select name="'+name+'">'+''.join('<option value="'+esc(v)+'"'+(' selected' if v==current else '')+'>'+esc(label)+'</option>' for v,label in choices)+'</select>'
def number(value):return '' if value is None else str(value)

def render_filters(filters,result):
    choices=[('ALL','全部'),('IN','撥入 IN'),('OUT','撥出 OUT'),('UNKNOWN','未知')]
    body='<form method="get" action="/" id="recording-filters"><div class="filter-grid"><label>開始時間 <input type="datetime-local" name="start" step="1" value="'+esc(filters['start'])+'"></label><label>結束時間 <input type="datetime-local" name="end" step="1" value="'+esc(filters['end'])+'"></label><label>電話 <input type="search" name="phone" maxlength="80" placeholder="完整或部分號碼" value="'+esc(filters['phone'])+'"></label><label>來去電 <select name="direction">'+''.join('<option value="'+value+'"'+(' selected' if value==filters['direction'] else '')+'>'+label+'</option>' for value,label in choices)+'</select></label><label>通話類型 '+select('call_type',[(k,v[0]) for k,v in CALL_TYPES.items()],filters['call_type'])+'</label><label>線路 '+select('line',[('0','全部')]+[(str(n),'線路 '+str(n)) for n in range(1,5)],str(filters['line']))+'</label><label>秒數 <span class="seconds-range"><input type="number" name="min_seconds" min="0" max="86400" inputmode="numeric" placeholder="最短" aria-label="最短秒數" value="'+number(filters['min_seconds'])+'"><span>～</span><input type="number" name="max_seconds" min="0" max="86400" inputmode="numeric" placeholder="最長" aria-label="最長秒數" value="'+number(filters['max_seconds'])+'"></span></label><label>排序 '+select('sort',[(k,v[0]) for k,v in SORTS.items()],filters['sort'])+'</label></div><div class="filter-actions"><button>篩選錄音</button><a href="/">重設為今天</a><a href="'+esc(link(filters,start='',end='',page=1))+'">不限日期</a><a href="'+esc(stats_link(filters))+'">統計</a><a href="'+esc(export_link(filters))+'">匯出 Excel</a></div></form><small>時間以台北當地時間為準，包含起迄秒數；電話支援部分號碼，所有條件會同時套用。秒數指錄音長度，未接來電為 0。</small>'
    body+='<p>符合 '+str(result['total'])+' 筆記錄 · '+str(result['backed_up'])+' 筆已備份'
    if result['total']:body+=' · 第 '+str(result['page'])+'／'+str(result['pages'])+' 頁'
    return body+'</p>'

def render_pagination(filters,result):
    if result['pages']<=1:return ''
    links=[]
    if result['page']>1:links.append('<a href="'+esc(link(filters,page=result['page']-1))+'">上一頁</a>')
    if result['page']<result['pages']:links.append('<a href="'+esc(link(filters,page=result['page']+1))+'">下一頁</a>')
    return '<nav aria-label="錄音分頁">'+''.join(links)+'</nav>'
