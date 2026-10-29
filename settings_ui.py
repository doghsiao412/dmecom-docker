"""HTML forms for verified device settings; no device I/O while rendering."""
import html
from voice_names import label_for

def esc(v):return html.escape(str(v),quote=True)
def hidden(name,value):return '<input type="hidden" name="'+esc(name)+'" value="'+esc(value)+'">'
def check(name,value,label=''):
    return '<label><input type="checkbox" name="'+esc(name)+'" value="1"'+(' checked' if value else '')+'>'+esc(label)+'</label>'
def options(files,current,display_names=None):
    display_names=display_names or {}
    names=sorted(set(files)|({current} if current else set()))
    return '<option value="">未指定</option>'+''.join('<option value="'+esc(n)+'"'+(' selected' if n==current else '')+'>'+esc(label_for(display_names,n)+((' — '+n) if display_names.get(n.casefold()) else ''))+'</option>' for n in names)
def render_settings(answer,files,interval_seconds,csrf,display_names=None):
    nonce=hidden('csrf',csrf);rows=answer.get('lines',[]);names=[f['name'] for f in files]
    body='<section id="line-settings"><h2>線路與答錄設定</h2><p>可編輯響鈴次數、接聽總時限、播放公告，以及每週七天的四個時段和語音檔。儲存後會立即生效，並讀回確認；排程由錄音機執行。</p><p>跨午夜可直接設定 20:00～11:00。全天接聽請勾選「全天」並指定第一時段語音。若要播放後結束，請讓完整公告填滿接聽時限，避免播完後繼續收留言。</p><form method="post" action="/settings/refresh">'+nonce+'<button>重新讀取設備設定與語音清單</button></form>'
    if answer.get('checked'):body+='<small>設備設定讀取時間：'+esc(answer['checked'])+'</small>'
    if answer.get('pending'):
        body+='<p class="error">上次寫入未完成，請先還原再編輯。</p><form method="post" action="/settings/restore">'+nonce+'<button>還原未完成的變更</button></form>'
    elif answer.get('active'):
        body+='<form method="post" action="/settings/restore">'+nonce+'<button>還原最近一次變更前的設定</button></form>'
    body+='</section>'
    for row in rows:
        line=row['line'];ready=bool(row.get('revision')) and not answer.get('pending')
        body+='<section><details'+(' open' if line==1 else '')+'><summary>線路 '+str(line)+' · 響鈴 '+str(row['rings'])+' 次 · 接聽總時限 '+str(row['message_limit'])+' 秒 · 公告 '+('開啟' if row['announcement'] else '關閉')+'</summary>'
        if not ready:body+='<p>請先按「重新讀取設備設定與語音清單」取得可編輯設定。</p></details></section>';continue
        body+='<form class="line-settings" method="post" action="/settings/save">'+nonce+hidden('line',line)
        for r in rows:body+=hidden('rev'+str(r['line']),r.get('revision',''))
        body+='<div class="grid"><label>響鈴次數 <input type="number" name="rings" min="2" max="10" value="'+str(row['rings'])+'" required></label><label>接聽總時限（秒） <input type="number" name="message_limit" min="30" max="1800" value="'+str(row['message_limit'])+'" required></label>'+check('announcement',row['announcement'],'接聽後播放公告')+'</div>'
        first=row['weekly'][0]['slots'][0]
        body+='<details><summary>快速設定全週同一時段</summary><div class="grid"><label>開始 <input type="time" name="quick_start" value="'+esc(first['start'])+'"></label><label>結束 <input type="time" name="quick_end" value="'+esc(first['end'])+'"></label><label>語音 <select name="quick_file">'+options(names,first['file'],display_names)+'</select></label></div><button type="button" onclick="fillWeek(this)">將此時段填入週一至週日</button><p class="muted">填表後仍須按下方儲存；其他三個時段會設為關閉。</p></details>'
        for d,day in enumerate(row['weekly']):
            body+='<details><summary>'+esc(day['day'])+'</summary>'+check('d'+str(d)+'_all_day',day['all_day'],'全天自動接聽')+'<div class="scroll"><table><tr><th>啟用</th><th>開始</th><th>結束</th><th>語音檔</th></tr>'
            for n,slot in enumerate(day['slots']):
                prefix='d'+str(d)+'_s'+str(n)+'_'
                body+='<tr><td>'+check(prefix+'enabled',slot['enabled'],'時段 '+str(n+1))+'</td><td><input type="time" name="'+prefix+'start" value="'+esc(slot['start'])+'" required></td><td><input type="time" name="'+prefix+'end" value="'+esc(slot['end'])+'" required></td><td><select name="'+prefix+'file">'+options(names,slot['file'],display_names)+'</select></td></tr>'
            body+='</table></div></details>'
        body+='<p>'+check('apply_all',False,'同時套用至全部 4 條線路（含響鈴、時限及整週時段）')+'</p><button>儲存'+('線路 '+str(line))+'設定</button></form></details></section>'
    body+='<section id="backup-settings"><h2>備份排程</h2><form method="post" action="/settings/backup">'+nonce+'<label>備份間隔（分鐘） <input type="number" name="minutes" min="1" max="10080" value="'+str(interval_seconds//60)+'" required></label><button>儲存備份間隔</button></form><p class="muted">正在進行的備份會繼續；設定保存在 NAS，容器重建仍保留。</p></section>'
    body+='''<script>function fillWeek(button){const form=button.closest('form'),e=form.elements;for(let d=0;d<7;d++){e['d'+d+'_all_day'].checked=false;for(let n=0;n<4;n++){const p='d'+d+'_s'+n+'_';e[p+'enabled'].checked=n===0;if(n===0){e[p+'start'].value=e.quick_start.value;e[p+'end'].value=e.quick_end.value;e[p+'file'].value=e.quick_file.value;}}}}</script>'''
    return body

def parse_line_form(args):
    result=dict(rings=int(args['rings']),message_limit=int(args['message_limit']),announcement=int(args.get('announcement','0')),weekly=[])
    for d in range(7):
        day=dict(all_day=int(args.get('d'+str(d)+'_all_day','0')),slots=[])
        for n in range(4):
            prefix='d'+str(d)+'_s'+str(n)+'_'
            day['slots'].append(dict(enabled=int(args.get(prefix+'enabled','0')),start=args[prefix+'start'],end=args[prefix+'end'],file=args.get(prefix+'file','')))
        result['weekly'].append(day)
    return result
