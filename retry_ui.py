"""Retry management stays local: no deletion or recorder configuration writes."""
import html
import json
import urllib.parse

def esc(value):return html.escape(str(value if value is not None else ''),quote=True)
def hidden(name,value):return '<input type="hidden" name="'+esc(name)+'" value="'+esc(value)+'">'

def render_retries(result,stats,csrf,message=''):
    view=result['view'];skipped=view=='skipped'
    def url(**values):return '/backup/retries?'+urllib.parse.urlencode(dict(view=view,**values))
    out='<p class="ok">'+esc(message)+'</p>' if message else ''
    out+='<section><div class="section-heading"><div><h2>重試錄音管理</h2><p>略過後停止自動重試，保留紀錄；可隨時恢復。</p></div><a class="retry-back" href="/">返回錄音與備份</a></div>'
    out+='<nav class="retry-tabs" aria-label="重試狀態">'
    for name,label,count in [('pending','待重試',stats['failed']),('skipped','已略過',stats['skipped'])]:
        out+='<a href="/backup/retries?view='+name+'"'+(' aria-current="page"' if name==view else '')+'>'+label+' '+str(count)+'</a>'
    out+='</nav><p class="retry-note">略過不會刪除 NAS 或答錄機內的音檔。若該筆下載已開始，仍可能完成本次下載。設備中的同一筆錄音內容更新後，會重新納入備份。</p>'
    if not result['rows']:
        return out+'<p class="retry-empty">'+('目前沒有已略過的錄音。' if skipped else '目前沒有待重試的錄音。')+'</p></section>'
    out+='<form method="post" action="/backup/retries/action" id="retry-batch" class="retry-toolbar">'+hidden('csrf',csrf)+hidden('view',view)
    out+='<label><input type="checkbox" id="retry-select-all"> 全選本頁</label><div class="retry-bulk-actions"><span id="retry-selection-count" class="muted">已選 0 筆</span>'
    if not skipped:out+='<button type="submit" name="operation" value="skip" class="secondary" data-retry-bulk disabled>略過所選</button>'
    out+='<button type="submit" name="operation" value="retry" data-retry-bulk disabled>'+('恢復並重試所選' if skipped else '重試所選')+'</button></div></form>'
    out+='<p class="retry-note">共 '+str(result['total'])+' 筆 · 第 '+str(result['page'])+'／'+str(result['pages'])+' 頁</p><div class="scroll"><table class="retry-table"><thead><tr><th>選取</th><th>錄音</th><th>電話</th><th>失敗原因</th><th>操作</th></tr></thead><tbody>'
    for row in result['rows']:
        value=json.dumps([row['day'],row['key'],row['version']],ensure_ascii=False,separators=(',',':'))
        title=row['timestamp'].replace('T',' ')
        out+='<tr><td><input type="checkbox" name="item" form="retry-batch" value="'+esc(value)+'" class="retry-select" aria-label="選取 '+esc(title)+'"></td><td><time>'+esc(title)+'</time><div class="retry-meta">線路 '+esc(row['RecChannel']+1)+' · '+esc(row['direction'])+' · '+esc(row['RecTime'])+' 秒</div></td><td class="phone-number">'+esc(row['PhoneNumber'] or '未知電話')+'</td><td class="retry-error">'+esc(row['error'] or '尚未完成備份')
        if skipped:out+='<div class="retry-meta">略過時間：'+esc((row['skipped_at'] or '').replace('T',' '))+'</div>'
        out+='</td><td><form method="post" action="/backup/retries/action" class="retry-action">'+hidden('csrf',csrf)+hidden('view',view)+hidden('item',value)
        if not skipped:out+='<button type="submit" name="operation" value="skip" class="secondary">略過</button>'
        out+='<button type="submit" name="operation" value="retry">'+('恢復重試' if skipped else '重試')+'</button></form></td></tr>'
    out+='</tbody></table></div><nav class="retry-pages" aria-label="重試錄音分頁">'
    if result['page']>1:out+='<a href="'+esc(url(page=result['page']-1))+'">上一頁</a>'
    if result['page']<result['pages']:out+='<a href="'+esc(url(page=result['page']+1))+'">下一頁</a>'
    out+='</nav></section>'
    return out+r'''<script>(()=>{const boxes=[...document.querySelectorAll('.retry-select')],all=document.getElementById('retry-select-all'),count=document.getElementById('retry-selection-count'),buttons=document.querySelectorAll('[data-retry-bulk]');function update(){const n=boxes.filter(b=>b.checked).length;count.textContent='已選 '+n+' 筆';all.checked=n===boxes.length;all.indeterminate=n>0&&n<boxes.length;buttons.forEach(b=>b.disabled=n===0)}all.addEventListener('change',()=>{boxes.forEach(b=>b.checked=all.checked);update()});boxes.forEach(b=>b.addEventListener('change',update));update()})();</script>'''
