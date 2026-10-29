"""Forms for the human-pickup recording notice."""
import html
from voice_names import label_for

def esc(v):return html.escape(str(v),quote=True)
def hidden(k,v):return '<input type="hidden" name="'+esc(k)+'" value="'+esc(v)+'">'
def render_filename(name,display_names):
    if not name:return '未指定'
    label=display_names.get(name.casefold())
    return ('<span>'+esc(label)+'</span><small class="device-filename"><code>'+esc(name)+'</code></small>') if label else '<code>'+esc(name)+'</code>'

def render_switch(row,csrf,pending=False):
    line=row['line'];enabled=bool(row['enabled']);has_file=bool(row.get('file'));ready=bool(row.get('revision')) and not pending
    disabled=not ready or (not enabled and not has_file)
    reason='先還原未完成變更' if pending else '請先讀取設定' if not ready else '請先選擇宣告音檔' if not has_file and not enabled else ''
    body='<form class="announcement-toggle-form" method="post" action="/announcement/toggle">'+hidden('csrf',csrf)+hidden('line',line)+hidden('revision',row.get('revision',''))+hidden('enabled',0 if enabled else 1)
    body+='<button type="submit" class="announcement-switch" role="switch" aria-checked="'+('true' if enabled else 'false')+'" aria-label="線路 '+str(line)+' 接聽宣告"'+(' disabled aria-describedby="switch-hint-'+str(line)+'"' if disabled else '')+'><span class="switch-track" aria-hidden="true"><span class="switch-thumb"></span></span><span class="switch-state">'+('開啟' if enabled else '關閉')+'</span></button></form>'
    if disabled:body+='<small id="switch-hint-'+str(line)+'" class="switch-hint">'+esc(reason)+'</small>'
    return body

def render_announcement(snapshot,files,items,csrf,selected='',display_names=None):
    display_names=display_names or {}
    nonce=hidden('csrf',csrf);rows=snapshot.get('lines',[]);labels={m['name']:m['label'] for m in items}
    body='<section id="announcement"><h2>接聽宣告</h2><p>例如「您好，本通話將進行錄音」。人員接起來電後播放一次，接著繼續通話。此設定儲存在答錄機內。</p><p><a href="/voices">上傳新音檔、預聽並傳送到錄音機</a></p><form method="post" action="/announcement/read">'+nonce+'<button>讀取目前宣告與設備音檔</button></form>'
    if snapshot.get('checked'):body+='<small>讀取時間：'+esc(snapshot['checked'])+'</small>'
    if rows:
        body+='<p class="muted">點選各線路開關會立即儲存到答錄機，保留原宣告音檔。</p><div class="scroll"><table class="announcement-lines"><tr><th>線路</th><th>宣告開關</th><th>目前音檔</th></tr>'
        for row in rows:body+='<tr><td>'+str(row['line'])+'</td><td>'+render_switch(row,csrf,bool(snapshot.get('pending')))+'</td><td>'+render_filename(row['file'],display_names)+'</td></tr>'
        body+='</table></div>'
    if snapshot.get('pending'):body+='<p class="error">上次宣告寫入未完成，請先還原。</p>'
    if snapshot.get('pending') or snapshot.get('active'):body+='<form method="post" action="/announcement/restore">'+nonce+'<button>還原最近一次宣告變更</button></form>'
    body+='</section>'
    if len(rows)==4 and not snapshot.get('pending'):
        names=sorted({f['name'] for f in files if len(f['name'].encode('utf-16le'))<=62})
        body+='<section><h2>替換接聽宣告</h2><form method="post" action="/announcement/prepare">'+nonce+'<fieldset><legend>要修改的線路</legend>'
        for row in rows:
            line=row['line'];body+='<label><input type="checkbox" name="line'+str(line)+'" value="1">線路 '+str(line)+'</label>'+hidden('rev'+str(line),row['revision'])
        body+='</fieldset><label>新宣告音檔 <select name="file"><option value="">保留各線路目前音檔</option>'+''.join('<option value="'+esc(name)+'"'+(' selected' if name==selected else '')+'>'+esc(label_for(display_names,name,labels.get(name,name)))+' — '+esc(name)+'</option>' for name in names)+'</select></label><label>播放宣告 <select name="enabled"><option value="KEEP">保留各線路目前開關</option><option value="1">啟用</option><option value="0">停用</option></select></label><p class="muted">先確認新宣告的內容，再選擇線路。下一步會列出變更前後；確認後立即生效。</p><button>檢視變更內容</button></form></section>'
    elif not snapshot.get('pending'):body+='<section><p>先讀取目前宣告與設備音檔，再選擇要替換的線路。</p></section>'
    body+='<script>document.querySelectorAll(".announcement-toggle-form").forEach(form=>form.addEventListener("submit",()=>{const button=form.querySelector("button");button.disabled=true;button.setAttribute("aria-busy","true");button.querySelector(".switch-state").textContent="儲存中…"}))</script>'
    return body

def render_confirmation(plan,csrf,display_names=None):
    display_names=display_names or {}
    body='<section><h2>確認接聽宣告</h2><p>人員接起來電後播放一次，接著繼續通話。按下確認後立即生效。</p><div class="scroll"><table><tr><th>線路</th><th>原設定</th><th>新設定</th></tr>'
    for line in plan['lines']:
        before=plan['before'][str(line)];after=plan['after'][str(line)]
        def text(row):return ('開啟' if row['enabled'] else '關閉')+' · '+esc((label_for(display_names,row['file'])+' — '+row['file']) if row['file'] and display_names.get(row['file'].casefold()) else (row['file'] or '未指定'))
        body+='<tr><td>'+str(line)+'</td><td>'+text(before)+'</td><td>'+text(after)+'</td></tr>'
    return body+'</table></div><form method="post" action="/announcement/apply">'+hidden('csrf',csrf)+hidden('token',plan['token'])+'<button>確認套用接聽宣告</button></form><p><a href="/announcement">返回修改</a></p></section>'
