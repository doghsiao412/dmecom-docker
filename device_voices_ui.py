"""Device greeting management forms; all mutations require an explicit confirmation."""
import html
import urllib.parse
from voice_names import label_for

def esc(v):return html.escape(str(v),quote=True)
def hidden(k,v):return '<input type="hidden" name="'+esc(k)+'" value="'+esc(v)+'">'
def form(action,body,csrf,css=''):return '<form method="post" action="'+action+'"'+(' class="'+esc(css)+'"' if css else '')+'>'+hidden('csrf',csrf)+body+'</form>'
def render_device_voices(catalog,backups,pending,csrf,display_names=None):
    display_names=display_names or {}
    files=catalog['files'];names={f['name'].casefold() for f in files}
    body='<section id="device-files"><div class="section-heading"><div><h2>答錄機內的招呼語</h2><p>管理實際存放於答錄機的音檔。先準備預聽與下載，改名或刪除前會另存還原副本並列出確認內容。</p></div>'+form('/voice/refresh','<button>重新讀取設備音檔與使用位置</button>',csrf)+'</div>'
    if catalog.get('checked'):body+='<p class="muted">讀取時間：'+esc(catalog['checked'].replace('T',' '))+' · '+str(len(files))+' 個音檔</p>'
    else:body+='<p class="muted">下方為上次音檔清單；按「重新讀取」取得線路使用位置。</p>'
    body+='<p class="muted">目前被接聽宣告或任何答錄時段引用的音檔（含停用時段），請先到<a href="/announcement">接聽宣告</a>或<a href="/settings">設備設定</a>切換檔案，再改名／刪除。</p>'
    if not files:body+='<p>尚無設備音檔清單，請先重新讀取。</p>'
    for item in files:
        name=item['name'];backup=item.get('backup');usage=item.get('usage');blocked=bool(usage)
        body+='<article class="device-voice"><div class="section-heading"><div><h3>'+esc(label_for(display_names,name))+'</h3><small class="device-filename">設備檔名：<code>'+esc(name)+'</code> · '+str(item['size'])+' bytes</small></div><span class="direction-tag '+('incoming' if blocked else '')+'">'+('有設定引用' if blocked else '使用位置待讀取' if usage is None else '未被接聽設定引用')+'</span></div>'
        body+='<details class="voice-label-editor"><summary>編輯顯示名稱</summary>'+form('/voice/device/label',hidden('name',name)+'<label><span>顯示名稱</span><input name="label" maxlength="60" value="'+esc(display_names.get(name.casefold(),''))+'" placeholder="例如：上班宣告、下班答錄"></label><button class="secondary">儲存名稱</button>',csrf,'voice-label-form')+'<small>僅管理台顯示；留空可恢復使用實際檔名。</small></details>'
        if usage:body+='<details><summary>查看 '+str(len(usage))+' 處引用設定</summary><ul>'+''.join('<li>'+esc(label)+'</li>' for label in usage)+'</ul></details>'
        if backup:
            key=backup['key'];body+='<audio controls preload="none" src="/voice/device/preview?key='+key+'"></audio><p><a href="/voice/device/download?key='+key+'">下載 WAV</a> · <a href="/announcement?file='+urllib.parse.quote(name)+'">選作接聽宣告</a></p>'
        else:body+='<p><a href="/announcement?file='+urllib.parse.quote(name)+'">選作接聽宣告</a></p>'
        body+=form('/voice/device/fetch',hidden('name',name)+'<button class="secondary">'+('重新讀取預聽與下載副本' if backup else '準備預聽與下載')+'</button>',csrf)
        disabled=' disabled' if blocked else ''
        body+='<div class="voice-file-actions">'+form('/voice/device/prepare',hidden('name',name)+hidden('operation','rename')+'<label><span>新檔名</span><input name="new_name" placeholder="新名稱.wav" maxlength="64" required'+disabled+'></label><button'+disabled+'>改名…</button>',csrf,'voice-rename-form')+form('/voice/device/prepare',hidden('name',name)+hidden('operation','delete')+'<button class="danger"'+disabled+'>刪除…</button>',csrf,'voice-delete-form')+'</div></article>'
    body+='</section>'
    if pending:
        body+='<section><h2>待確認的音檔操作</h2><p>上次操作中斷，原檔或新檔可能已存在；請檢視後繼續，系統會核對音檔內容。</p><ul>'
        for plan in pending:body+='<li>'+esc(plan['name'])+' · '+esc(plan.get('error') or '處理中')+' · <a href="/voice/device/confirm?token='+plan['token']+'">檢視並繼續</a></li>'
        body+='</ul></section>'
    if backups:
        body+='<section><details><summary>設備音檔還原副本（最近 10 筆）</summary><p class="muted">保存在 NAS；由設備讀回的 IMA 音訊不重新編碼，下載與還原時重建 WAV 檔頭。還原不會更改任何線路設定，也不覆寫設備同名檔案。</p>'
        for backup in backups[:10]:
            key=backup['key'];body+='<div class="voice-backup"><b>'+esc(label_for(display_names,backup['name']))+'</b><small> · '+esc(backup['name'])+'</small><small> · '+esc(backup['checked'].replace('T',' '))+'</small><p><a href="/voice/device/download?key='+key+'">下載副本</a></p>'
            if backup['name'].casefold() not in names and len(backup['name'].encode('utf-16le'))<=254:body+=form('/voice/device/restore',hidden('key',key)+'<button class="secondary">以原檔名還原到答錄機</button>',csrf)
            body+='</div>'
        body+='</details></section>'
    return body

def render_voice_confirmation(plan,csrf,display_names=None):
    display_names=display_names or {}
    operation='改名' if plan['operation']=='rename' else '刪除'
    body='<section><h2>確認'+operation+'設備音檔</h2><p>顯示名稱：'+esc(label_for(display_names,plan['name']))+'</p><p>原檔名：<code>'+esc(plan['name'])+'</code></p>'
    if plan['operation']=='rename':body+='<p>新檔名：<code>'+esc(plan['new_name'])+'</code></p><p>會以新名稱寫入相同音訊，讀回一致後才移除原檔。線路設定不變。</p>'
    else:body+='<p>會刪除答錄機內的這一個語音檔；NAS 還原副本會保留。線路設定不變。</p>'
    body+='<p class="muted">已確認目前未被接聽宣告或答錄時段引用；套用前會再次檢查使用位置與內容。確認有效期 10 分鐘。</p><audio controls preload="none" src="/voice/device/preview?key='+plan['backup']+'"></audio><p><a href="/voice/device/download?key='+plan['backup']+'">下載變更前副本</a></p>'
    if plan['status']=='completed':body+='<p class="ok">此操作已完成。</p>'
    else:
        if plan.get('error'):body+='<p class="error">'+esc(plan['error'])+'</p>'
        body+=form('/voice/device/apply',hidden('token',plan['token'])+'<button'+(' class="danger"' if operation=='刪除' else '')+'>確認'+operation+'</button>',csrf)
    return body+'<p><a href="/voices">返回招呼語</a></p></section>'
