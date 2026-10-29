"""Local recording query controls, including cross-day ranges."""
import datetime as dt
import html
import re
import urllib.parse

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
    return dict(start=start,end=end,phone=phone,direction=direction,page=page)

def link(filters,**overrides):
    data={k:v for k,v in filters.items() if k in ('start','end','phone','direction','page')};data.update(overrides)
    return '/?'+urllib.parse.urlencode(data)

def render_filters(filters,result):
    choices=[('ALL','全部'),('IN','撥入 IN'),('OUT','撥出 OUT'),('UNKNOWN','未知')]
    body='<form method="get" action="/" id="recording-filters"><div class="filter-grid"><label>開始時間 <input type="datetime-local" name="start" step="1" value="'+esc(filters['start'])+'"></label><label>結束時間 <input type="datetime-local" name="end" step="1" value="'+esc(filters['end'])+'"></label><label>電話 <input type="search" name="phone" maxlength="80" placeholder="完整或部分號碼" value="'+esc(filters['phone'])+'"></label><label>來去電 <select name="direction">'+''.join('<option value="'+value+'"'+(' selected' if value==filters['direction'] else '')+'>'+label+'</option>' for value,label in choices)+'</select></label></div><div class="filter-actions"><button>篩選錄音</button><a href="/">重設為今天</a><a href="'+esc(link(filters,start='',end='',page=1))+'">不限日期</a></div></form><small>時間以台北當地時間為準，包含起迄秒數；電話支援部分號碼，所有條件會同時套用。</small>'
    body+='<p>符合 '+str(result['total'])+' 筆記錄 · '+str(result['backed_up'])+' 筆已備份'
    if result['total']:body+=' · 第 '+str(result['page'])+'／'+str(result['pages'])+' 頁'
    return body+'</p>'

def render_pagination(filters,result):
    if result['pages']<=1:return ''
    links=[]
    if result['page']>1:links.append('<a href="'+esc(link(filters,page=result['page']-1))+'">上一頁</a>')
    if result['page']<result['pages']:links.append('<a href="'+esc(link(filters,page=result['page']+1))+'">下一頁</a>')
    return '<nav aria-label="錄音分頁">'+''.join(links)+'</nav>'
