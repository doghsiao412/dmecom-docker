#!/usr/bin/env python3
"""Local NAS archive and greeting console shared through Tailscale Serve."""
import datetime as dt
import email.parser
import email.policy
import html
import hashlib
import json
import os
from pathlib import Path
import secrets
import select
import signal
import subprocess
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from dmecom import Client,ProtocolError,RecordingUnavailable
from discovery import DiscoveryError
from backup import Archive,Backup
from voice import Voices,ima_payload
from web_security import validate_form_source,SourceError
from device_clock import DeviceClock,local_now
from answer import AnswerSettings
from settings_ui import render_settings,parse_line_form
from connection_status import ConnectionStatus
from recording_filters import parse_filters,render_filters,render_pagination
from announcement import PickupAnnouncements
from announcement_ui import render_announcement,render_confirmation
from ui_layout import page_shell,icon
from device_voices import DeviceVoices
from device_voices_ui import render_device_voices,render_voice_confirmation
from voice_names import VoiceNames,label_for
from media import serve_wav
from recording_ui import recording_player
from live_monitor import LiveMonitor,LiveError
from live_ui import render_live
ROOT=Path(__file__).resolve().parent
os.umask(0o077)
CONFIG=json.loads(Path(os.environ.get('DMECOM_CONFIG',str(ROOT/'config.json'))).read_text())
DATA=Path(os.environ.get('DMECOM_DATA',str(ROOT/'data')))
LOCK=threading.Lock();SESSION=None;CSRF=secrets.token_hex(24)
LIVE=LiveMonitor()

def invalidate():
    global SESSION
    if SESSION:SESSION.__exit__(None,None,None);SESSION=None
    LIVE.disconnected()

def device(fn):
    global SESSION
    with LOCK:
        try:
            if SESSION is None:
                candidate=Client(CONFIG);candidate.on_frame=LIVE.handle;candidate.__enter__();SESSION=candidate
            result=fn(SESSION);CONNECTION.success(SESSION.address);return result
        except RecordingUnavailable:
            CONNECTION.success(SESSION.address if SESSION else None);raise
        except (OSError,ProtocolError,DiscoveryError) as e:
            CONNECTION.failure(e);invalidate();raise

def keepalive():
    last=0
    while True:
        time.sleep(.05 if LIVE.snapshot()['line'] is not None else .5)
        with LOCK:
            if SESSION:
                try:
                    if time.monotonic()-last>=5:SESSION.send(7,0,b'\0\0');last=time.monotonic()
                    LIVE.tick(SESSION)
                    until=time.monotonic()+.1
                    while time.monotonic()<until and select.select([SESSION.sock],[],[],0)[0]:
                        SESSION.receive();CONNECTION.success(SESSION.address)
                except (OSError,ProtocolError) as e:CONNECTION.failure(e);invalidate()

ARCHIVE=Archive(DATA)
CONNECTION=ConnectionStatus(DATA)
BACKUP=Backup(ARCHIVE,lambda day:device(lambda c:c.index(day)),lambda row:device(lambda c:c.audio(row)),CONFIG)
VOICES=Voices(DATA,device)
CLOCK=DeviceClock(DATA,device)
ANSWER=AnswerSettings(DATA,device)
PICKUP=PickupAnnouncements(DATA,device)
VOICE_NAMES=VoiceNames(DATA)
DEVICE_VOICES=DeviceVoices(DATA,device,VOICES,VOICE_NAMES)

def verify_pickup_voice(c,plan):
    for name in {plan['after'][str(n)]['file'] for n in plan['lines'] if plan['after'][str(n)]['enabled']}:
        local=next((m for m in VOICES.items() if m['name']==name),None)
        if local:
            audio=(VOICES.path(local['key'])/'device.wav').read_bytes()
            if hashlib.sha256(audio).hexdigest()!=local['sha256'] or c.voice_stream(name,len(ima_payload(audio)))!=ima_payload(audio):raise ProtocolError('宣告音檔校驗失敗')

def esc(value):return html.escape(str(value if value is not None else ''),quote=True)
def hidden(name,value):return '<input type="hidden" name="'+esc(name)+'" value="'+esc(value)+'">'
def form(action,content):return '<form method="post" action="'+action+'">'+hidden('csrf',CSRF)+content+'</form>'
def connection_banner():
    state=CONNECTION.snapshot();backup=BACKUP.status();status=state.get('status','unknown')
    label={'online':'已連線','unavailable':'暫時無法連線','unknown':'等待確認'}.get(status,'等待確認')
    description=state.get('error') or ('設備已回應，可讀取及套用設定。' if status=='online' else '設備設定顯示上次讀取的內容，恢復連線後即可套用變更。')
    next_retry=backup.get('next_backup') if backup.get('error') and not backup.get('running') else '背景工作執行中' if backup.get('running') else '依備份排程'
    body='<details id="connection-panel" class="connection-panel" data-state="'+esc(status)+'"'+(' open' if status!='online' else '')+'><summary><div class="connection-summary"><span class="connection-heading"><span class="status-dot"></span>答錄機：<span id="connection-label">'+esc(label)+'</span></span><span class="connection-last">最近成功連線 <span id="connection-last">'+esc((state.get('last_success') or '尚無記錄').replace('T',' '))+'</span></span><span class="connection-toggle">連線詳情'+icon('chevron')+'</span></div></summary><div class="connection-body"><p id="connection-description">'+esc(description)+'</p><p>下次重試：<span id="connection-next">'+esc((next_retry or '即將重試').replace('T',' '))+'</span></p><p>設備離線時，NAS 已備份錄音仍可查詢與下載。連線失敗約 1 分鐘後會自動重試，恢復後接續備份。</p>'+form('/connection/retry','<button>重新尋找並測試連線</button>')+'</div></details>'
    body+='''<script>async function refreshConnection(){try{const r=await(await fetch('/api/connection')).json(),s=r.connection,b=r.backup,p=document.getElementById('connection-panel'),previous=p.dataset.state;p.dataset.state=s.status;if(s.status==='unavailable'&&previous!==s.status)p.open=true;document.getElementById('connection-label').textContent=({online:'已連線',unavailable:'暫時無法連線',unknown:'等待確認'})[s.status]||'等待確認';document.getElementById('connection-description').textContent=s.error||(s.status==='online'?'設備已回應，可讀取及套用設定。':'設備設定顯示上次讀取的內容，恢復連線後即可套用變更。');document.getElementById('connection-last').textContent=(s.last_success||'尚無記錄').replace('T',' ');document.getElementById('connection-next').textContent=(b.running?'背景工作執行中':b.error?(b.next_backup||'即將重試'):'依備份排程').replace('T',' ');const sidebar=document.getElementById('sidebar-connection');if(sidebar){sidebar.dataset.state=s.status;document.getElementById('sidebar-connection-label').textContent=({online:'答錄機已連線',unavailable:'答錄機暫時離線',unknown:'等待連線確認'})[s.status]||'等待連線確認'}}catch(e){}}setInterval(refreshConnection,10000)</script>'''
    return body

def shell(title,content):
    return page_shell(title,content,connection_banner(),CONNECTION.snapshot())

def main_page(filters,message=''):
    result=ARCHIVE.search(**filters);rows=result['rows'];s=BACKUP.status()
    content='<p class="ok">'+esc(message)+'</p>' if message else ''
    content+='<section class="backup-overview"><div class="section-heading"><div><h2>備份概況</h2><p>每 '+str(BACKUP.interval//60)+' 分鐘同步新錄音，歷史備份在背景持續執行。</p></div>'
    content+=form('/backup','<button id="backup-button"'+(' disabled' if s.get('running') else '')+'>'+('備份進行中' if s.get('running') else '立即備份')+'</button>')+'</div><div class="metrics">'
    for key,label in [('saved','已備份'),('total','已找到錄音'),('failed','待重試')]:
        content+='<div class="stat'+(' metric-warning' if key=='failed' and s[key] else '')+'"><span>'+label+'</span><strong id="'+key+'">'+str(s[key])+'</strong><small>筆錄音</small></div>'
    content+='</div><div class="backup-progress"><p id="progress"></p><p class="error" id="backup-error"></p><small id="timing"></small></div><details><summary>備份位置與說明</summary><p>儲存位置：<code>/volume1/電話錄音</code>，錄音保存在 pcm。系統會補抓設備可提供的歷史錄音；已刪除或覆寫的錄音無法補抓。</p></details></section>'
    content+='<section><h2>本地錄音</h2>'+render_filters(filters,result)
    if not rows:content+='<p class="muted">沒有符合條件的本地記錄；可調整篩選範圍。歷史備份仍在回溯時，較早日期會陸續出現。</p>'
    else:
        content+='<div class="scroll"><table><tr><th>時間</th><th>線路</th><th>來去電</th><th>電話</th><th>秒數</th><th>音檔</th></tr>'
        for r in rows:
            source='/recording/play?'+urllib.parse.urlencode(dict(date=r['day'],key=r['key']))
            title=r['timestamp'].replace('T',' ')+' · '+(r['PhoneNumber'] or '未知電話')+' · '+r['direction']
            action='<div class="recording-actions"><button type="button" class="recording-play" data-recording-src="'+esc(source)+'" data-recording-label="'+esc(title)+'" aria-label="播放 '+esc(title)+'" aria-pressed="false">'+icon('play')+'<span class="play-label">播放</span></button>'+form('/download',hidden('date',r['day'])+hidden('key',r['key'])+'<button>下載 WAV</button>')+'</div>' if r['backed_up'] else ('<span class="error">備份待重試</span>' if r['error'] else '等待備份' if r['has_audio'] else '無音檔')
            tag={'撥入':'incoming','撥出':'outgoing'}.get(r['direction'],'')
            content+='<tr><td><time>'+esc(r['timestamp'].replace('T',' '))+'</time></td><td>'+esc(r['RecChannel']+1)+'</td><td><span class="direction-tag '+tag+'">'+esc(r['direction'])+'</span></td><td><span class="phone-number">'+esc(r['PhoneNumber'])+'</span></td><td>'+esc(r['RecTime'])+'</td><td>'+action+'</td></tr>'
        content+='</table></div>'+render_pagination(filters,result)
    content+='</section><script>async function update(){try{const s=await(await fetch("/api/status")).json();for(const k of ["saved","total","failed"])document.getElementById(k).textContent=s[k];document.getElementById("failed").closest(".stat").classList.toggle("metric-warning",s.failed>0);document.getElementById("progress").textContent=(s.phase||"準備首次備份")+(s.current_date?" · 處理日期 "+s.current_date:"")+" · 已掃描 "+s.scanned_days+" 天";document.getElementById("backup-error").textContent=s.error||"";document.getElementById("timing").textContent="上次完整成功："+(s.last_success||"首次備份尚未完成").replace("T"," ")+"　下次執行："+(s.running?"本輪結束後依設定間隔":(s.next_backup||"即將開始").replace("T"," "));let b=document.getElementById("backup-button");b.disabled=s.running;b.textContent=s.running?"備份進行中":"立即備份";}catch(e){}}update();setInterval(update,6000)</script>'
    return shell('錄音與自動備份',content+recording_player())

def voices_page(message=''):
    content='<p class="ok">'+esc(message)+'</p>' if message else ''
    catalog=DEVICE_VOICES.snapshot();display_names=VOICE_NAMES.all()
    content+=render_device_voices(catalog,DEVICE_VOICES.backups(),DEVICE_VOICES.interrupted(),CSRF,display_names)
    content+='<section id="upload-voice"><h2>新增招呼語</h2><p>上傳音訊，轉檔預聽後再傳送到錄音機。完成後可選擇用於「接聽宣告」，或下班時段的自動答錄。</p><p class="muted">接聽宣告播放一次後繼續通話。下班自動答錄已測試的範例為每天 20:00～翌日 11:00、重複播放 3 次及 40 秒結束；可在「設備設定」調整。</p>'
    content+='<form method="post" action="/voice/stage" enctype="multipart/form-data">'+hidden('csrf',CSRF)+'<label>音訊檔 <input type="file" name="audio" required></label><button>上傳 NAS 並產生預聽</button></form><small>可選擇 WAV／MP3／M4A 等音訊檔；20 MB、180 秒以內，選檔後會檢查實際音訊內容。會轉為設備使用的 8 kHz 單聲道 IMA ADPCM；上傳 NAS 不會改動錄音機。</small></section>'
    names={row['name'] for row in catalog['files']}
    content+='<section><details><summary>NAS 上傳與預聽區</summary><p class="muted">尚未傳送或先前上傳的音檔版本。答錄機實際音檔請使用上方管理區。</p>'
    for item in VOICES.items():
        present=item['name'] in names
        key=item['key'];content+='<article class="device-voice"><h3>'+esc(label_for(display_names,item['name'],item['label']))+'</h3><p>'+esc(item['duration'])+' 秒 · 設備檔名 <code>'+esc(item['name'])+'</code></p><audio controls preload="none" src="/voice/preview?key='+key+'"></audio>'
        if not present:content+=form('/voice/upload',hidden('key',key)+'<p>以全新檔名傳送並讀回校驗，不會選用為招呼語。</p><button>傳送到錄音機（不套用）</button>')
        else:
            content+='<p class="ok">已傳送並完成讀回校驗</p><p><a href="/announcement?file='+urllib.parse.quote(item['name'])+'">選此檔替換接聽宣告</a></p>'
        if not item.get('source_key') and item['duration']*3<=40:
            content+=form('/voice/repeat',hidden('key',key)+'<button>製作重複 3 次／40 秒預聽</button>')
        applied=VOICES.path(key)/'applied.json'
        if applied.exists():
            a=json.loads(applied.read_text());content+='<p>上次套用：'+esc(a['applied'])+' · 線路 '+esc(a['line'])+' · 原檔名 '+esc(a['before'])+'</p>'
        content+='</article>'
    content+='</details></section>'
    return shell('招呼語',content)

def settings_page(message=''):
    content='<p class="ok">'+esc(message)+'</p>' if message else ''
    content+='<nav class="settings-jump" aria-label="設備設定區塊"><a href="#line-settings">線路與答錄設定</a><a href="#device-clock">設備時間</a><a href="#backup-settings">備份排程</a></nav>'
    answer=ANSWER.snapshot();files=VOICES.snapshot()['files'];items=VOICES.items()
    content+=render_settings(answer,files,BACKUP.interval,CSRF,VOICE_NAMES.all())
    if not answer.get('active') and not answer.get('pending'):
        candidates=[item for item in items if item.get('uploaded') and item.get('repeat_count')==3 and item.get('answer_limit')==40]
        if candidates:
            content+='<section><h2>夜間接聽快速方案</h2><p>選擇已傳送的重複 3 次／40 秒版本，套用至全部 4 條線路，每天 20:00～翌日 11:00；40 秒結束。會保存原設定供還原。</p>'
            for item in candidates:content+=form('/answer/enable',hidden('key',item['key'])+'<p>'+esc(item['label'])+'</p><button>套用夜間接聽方案</button>')
            content+='</section>'
    clock=CLOCK.snapshot()
    content+='<section id="device-clock"><h2>設備時間</h2><p>NAS 台北時間：'+esc(local_now().strftime('%Y-%m-%d %H:%M:%S'))+'</p>'
    if clock:content+='<p>答錄機時間：'+esc(clock.get('device'))+' · 相差 '+esc(clock.get('difference_seconds'))+' 秒</p><small>讀取時間：'+esc(clock.get('checked'))+'</small>'
    content+=form('/clock/read','<button>讀取設備時間</button>')+form('/clock/sync','<button>校正為 NAS 台北時間</button>')+'</section>'
    content+="<script>if(location.hash==='#announcement')location.replace('/announcement'+location.search)</script>"
    return shell('設備設定',content)

def announcement_page(message='',selected=''):
    content='<p class="ok">'+esc(message)+'</p>' if message else ''
    content+=render_announcement(PICKUP.snapshot(),VOICES.snapshot()['files'],VOICES.items(),CSRF,selected,VOICE_NAMES.all())
    return shell('接聽宣告',content)

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def reply(self,code,data,ctype='text/html; charset=utf-8',filename=None):
        if isinstance(data,str):data=data.encode()
        self.send_response(code)
        for name,value in [('Content-Type',ctype),('Cache-Control','no-store'),('Referrer-Policy','same-origin'),('X-Content-Type-Options','nosniff'),('X-Frame-Options','DENY'),('Content-Length',str(len(data)))]:self.send_header(name,value)
        if filename:self.send_header('Content-Disposition',"attachment; filename=\"recording.wav\"; filename*=UTF-8''"+urllib.parse.quote(filename,safe=''))
        self.end_headers()
        try:self.wfile.write(data)
        except (BrokenPipeError,ConnectionResetError):pass
    def redirect(self,url):
        self.send_response(303);self.send_header('Location',url);self.send_header('Content-Length','0');self.end_headers()
    def media_request(self,u,q,head=False):
        if u.path=='/recording/play':
            day=q['date'][0];dt.date.fromisoformat(day);path=ARCHIVE.cached(day,q['key'][0])
            if not path:
                self.send_response(404);self.send_header('Content-Length','0');self.send_header('Cache-Control','no-store');self.end_headers();return True
            serve_wav(self,path,head=head);return True
        if u.path in ('/voice/device/preview','/voice/device/download'):
            meta=DEVICE_VOICES.backup(q['key'][0]);folder=DEVICE_VOICES._path(meta['key']);download=u.path.endswith('/download')
            serve_wav(self,folder/('device.wav' if download else 'preview.wav'),meta['name'] if download else None,head);return True
        if u.path=='/voice/preview':serve_wav(self,VOICES.path(q['key'][0])/'preview.wav',head=head);return True
        return False
    def do_HEAD(self):
        u=urllib.parse.urlsplit(self.path);q=urllib.parse.parse_qs(u.query,keep_blank_values=True)
        try:
            if self.media_request(u,q,True):return
            self.send_response(404)
        except (ValueError,KeyError,FileNotFoundError):self.send_response(400)
        self.send_header('Content-Length','0');self.end_headers()
    def do_GET(self):
        u=urllib.parse.urlsplit(self.path);q=urllib.parse.parse_qs(u.query,keep_blank_values=True)
        try:
            if u.path=='/healthz':self.reply(200,'ok','text/plain');return
            if u.path=='/live':self.reply(200,shell('即時監聽',render_live(CSRF)));return
            if u.path=='/api/live/status':
                LIVE.watch();self.reply(200,json.dumps(dict(live=LIVE.snapshot(),connection=CONNECTION.snapshot()),ensure_ascii=False),'application/json');return
            if u.path=='/api/live/audio':
                try:
                    after=int(q.get('after',['0'])[0])
                    if after<0:raise LiveError('無效音訊序號')
                    result=LIVE.chunks(q.get('token',[''])[0],after)
                    self.reply(200,json.dumps(result),'application/json')
                except (ValueError,KeyError) as e:self.reply(409,json.dumps(dict(error=str(e)),ensure_ascii=False),'application/json')
                return
            if u.path=='/api/device-settings':self.reply(200,json.dumps(dict(clock=CLOCK.snapshot(),answer=ANSWER.snapshot()),ensure_ascii=False),'application/json');return
            if u.path=='/api/connection':self.reply(200,json.dumps(dict(connection=CONNECTION.snapshot(),backup=BACKUP.status()),ensure_ascii=False),'application/json');return
            if u.path=='/api/status':self.reply(200,json.dumps(BACKUP.status(),ensure_ascii=False),'application/json');return
            if u.path=='/':self.reply(200,main_page(parse_filters(q),q.get('message',[''])[0]));return
            if u.path=='/settings':
                if q.get('file'):
                    params={key:q[key][0] for key in ('message','file') if q.get(key)}
                    self.redirect('/announcement?'+urllib.parse.urlencode(params));return
                self.reply(200,settings_page(q.get('message',[''])[0]));return
            if u.path=='/announcement':self.reply(200,announcement_page(q.get('message',[''])[0],q.get('file',[''])[0]));return
            if u.path=='/api/announcement':self.reply(200,json.dumps(PICKUP.snapshot(),ensure_ascii=False),'application/json');return
            if u.path=='/voices':self.reply(200,voices_page(q.get('message',[''])[0]));return
            if u.path=='/voice/device/confirm':self.reply(200,shell('招呼語',render_voice_confirmation(DEVICE_VOICES.plan(q['token'][0]),CSRF,VOICE_NAMES.all())));return
            if self.media_request(u,q):return
            self.reply(404,'Not found','text/plain')
        except (ValueError,KeyError,FileNotFoundError) as e:self.reply(400,shell('無法顯示','<p class="error">'+esc(e)+'</p>'))
    def arguments(self):
        n=int(self.headers.get('Content-Length','0'))
        maximum=21*1024*1024 if self.path=='/voice/stage' else 65536
        if not 0<n<=maximum:raise ValueError('請求大小超出限制')
        content=self.rfile.read(n)
        if self.path=='/voice/stage':
            typ=self.headers.get('Content-Type','')
            if not typ.startswith('multipart/form-data;'):raise ValueError('請使用檔案上傳表單')
            message=email.parser.BytesParser(policy=email.policy.default).parsebytes(('Content-Type: '+typ+'\r\nMIME-Version: 1.0\r\n\r\n').encode()+content)
            args={}
            for part in message.iter_parts():
                name=part.get_param('name',header='content-disposition')
                if name=='audio':args['audio']=part.get_payload(decode=True);args['label']=part.get_filename() or '招呼語'
                elif name=='csrf':args['csrf']=part.get_payload(decode=True).decode()
        else:args={k:v[0] for k,v in urllib.parse.parse_qs(content.decode()).items()}
        try:validate_form_source(self.headers,args.get('csrf',''),CSRF)
        except SourceError:
            # Header values only: never log the uploaded data or the nonce.
            print(json.dumps(dict(event='form_source_rejected',origin=self.headers.get('Origin'),host=self.headers.get('Host'),fetch_site=self.headers.get('Sec-Fetch-Site')),ensure_ascii=False),flush=True)
            raise
        return args
    def do_POST(self):
        try:
            args=self.arguments()
            if self.path in ('/live/start','/live/stop'):
                try:
                    if self.path=='/live/start':result=device(lambda c:LIVE.start(c,int(args['line'])))
                    else:LIVE.detach(args.get('token',''));result=dict(stopped=True)
                    self.reply(200,json.dumps(result),'application/json')
                except (ValueError,KeyError,OSError,ProtocolError,DiscoveryError) as e:self.reply(409,json.dumps(dict(error=str(e)),ensure_ascii=False),'application/json')
                return
            if self.path in ('/voice/prepare','/voice/apply'):raise ValueError('接聽控制欄位重新核對中，舊套用入口已停用；上傳音檔不會更改線路設定')
            if self.path.startswith('/voice/device/'):
                if self.path=='/voice/device/label':VOICE_NAMES.set(args['name'],args.get('label',''));message='招呼語顯示名稱已儲存'
                elif self.path=='/voice/device/fetch':DEVICE_VOICES.fetch(args['name']);message='已從答錄機讀回音檔，預聽與下載已準備完成'
                elif self.path=='/voice/device/prepare':
                    plan=DEVICE_VOICES.prepare(args['operation'],args['name'],args.get('new_name',''))
                    self.reply(200,shell('招呼語',render_voice_confirmation(plan,CSRF,VOICE_NAMES.all())));return
                elif self.path=='/voice/device/apply':
                    plan=DEVICE_VOICES.apply(args['token']);message='設備音檔已'+('改名' if plan['operation']=='rename' else '刪除')+'，還原副本保留在 NAS'
                elif self.path=='/voice/device/restore':DEVICE_VOICES.restore(args['key']);message='音檔已還原到答錄機並讀回確認，線路設定未更動'
                else:raise ValueError('未知設備音檔操作')
                self.redirect('/voices?message='+urllib.parse.quote(message)+'#device-files');return
            if self.path.startswith('/announcement/'):
                if self.path=='/announcement/read':VOICES.inspect();PICKUP.inspect();message='已讀取接聽宣告'
                elif self.path=='/announcement/toggle':
                    line=int(args['line'])
                    if not 1<=line<=4 or args.get('enabled') not in {'0','1'}:raise ValueError('無效的線路或宣告開關')
                    plan=PICKUP.prepare([line],None,int(args['enabled']),{str(line):args.get('revision','')})
                    result=PICKUP.apply(plan['token'],verify_pickup_voice)
                    message='線路 '+str(line)+' 接聽宣告已'+('開啟' if int(args['enabled']) else '關閉')+'並讀回確認'
                elif self.path=='/announcement/prepare':
                    lines=[n for n in range(1,5) if args.get('line'+str(n))=='1']
                    enabled=None if args.get('enabled','KEEP')=='KEEP' else int(args['enabled'])
                    plan=PICKUP.prepare(lines,args.get('file') or None,enabled,{str(n):args.get('rev'+str(n),'') for n in lines})
                    self.reply(200,shell('確認接聽宣告',render_confirmation(plan,CSRF,VOICE_NAMES.all())));return
                elif self.path=='/announcement/apply':
                    result=PICKUP.apply(args['token'],verify_pickup_voice);message='設定與設備一致，無需寫入' if result['status']=='no_change' else '接聽宣告已套用並讀回確認'
                elif self.path=='/announcement/restore':PICKUP.restore();message='已還原上次宣告設定'
                else:raise ValueError('未知宣告操作')
                self.redirect('/announcement?message='+urllib.parse.quote(message));return
            if self.path=='/connection/retry':
                CLOCK.inspect();BACKUP.trigger();self.redirect('/settings?message='+urllib.parse.quote('已找到答錄機並讀取時間，備份工作已排入'));return
            if self.path.startswith('/settings/'):
                if self.path=='/settings/refresh':VOICES.inspect();ANSWER.inspect();message='已重新讀取設備設定'
                elif self.path=='/settings/save':
                    line=int(args['line'])
                    if not 1<=line<=4:raise ValueError('無效線路')
                    edit=parse_line_form(args);lines=[1,2,3,4] if args.get('apply_all')=='1' else [line]
                    result=ANSWER.edit_lines({str(n):edit for n in lines},{str(n):args.get('rev'+str(n),'') for n in lines})
                    message='設定已寫入並讀回確認' if result['changed'] else '設定與設備一致，無需寫入'
                elif self.path=='/settings/restore':ANSWER.restore();message='已還原最近一次變更前的設定'
                elif self.path=='/settings/backup':BACKUP.set_interval(int(args['minutes']));message='備份間隔已儲存'
                else:raise ValueError('未知設定操作')
                self.redirect('/settings?message='+urllib.parse.quote(message));return
            if self.path=='/backup':
                started=BACKUP.trigger();self.redirect('/?message='+urllib.parse.quote('已排入立即備份' if started else '備份已在進行中'));return
            if self.path=='/download':
                dt.date.fromisoformat(args['date']);p=ARCHIVE.cached(args['date'],args['key'])
                if not p:raise ValueError('此錄音尚未備份完成，請稍候或按立即備份')
                serve_wav(self,p,p.name);return
            if self.path=='/voice/stage':VOICES.stage(args['audio'],args['label']);message='預聽已完成，尚未傳送到錄音機'
            elif self.path=='/voice/refresh':DEVICE_VOICES.inspect();message='已讀取設備音檔與線路使用位置'
            elif self.path=='/voice/upload':VOICES.upload(args['key']);DEVICE_VOICES.inspect();message='語音已傳送並校驗，尚未套用'
            elif self.path=='/voice/repeat':VOICES.repeat(args['key']);message='已製作重複 3 次／40 秒版本，請預聽並傳送校驗'
            elif self.path=='/answer/enable':
                meta=VOICES.metadata(args['key'])
                if not meta.get('uploaded') or meta.get('repeat_count')!=3 or meta.get('answer_limit')!=40:raise ValueError('請先製作重複版本並傳送校驗')
                audio=(VOICES.path(args['key'])/'device.wav').read_bytes();stream=ima_payload(audio)
                if hashlib.sha256(audio).hexdigest()!=meta['sha256']:raise ProtocolError('NAS 語音校驗失敗')
                def verify(c):
                    if c.voice_stream(meta['name'],len(stream))!=stream:raise ProtocolError('設備語音校驗失敗')
                ANSWER.configure(meta['name'],verify);message='已套用夜間接聽方案並讀回全部設定；請實際來電確認播放與掛斷'
            elif self.path=='/answer/restore':ANSWER.restore();message='已還原啟用前的接聽設定並讀回確認'
            elif self.path=='/clock/read':CLOCK.inspect();message='已讀取設備時間'
            elif self.path=='/clock/sync':CLOCK.sync();message='設備時間已校正並讀回確認'
            elif self.path=='/answer/read':ANSWER.inspect();message='已讀取答錄設定'
            else:self.reply(404,'Not found','text/plain');return
            target='/settings' if self.path.startswith(('/clock/','/answer/')) else '/voices'
            anchor='#device-clock' if self.path.startswith('/clock/') else '#line-settings' if self.path.startswith('/answer/') else ''
            self.redirect(target+'?message='+urllib.parse.quote(message)+anchor)
        except (BrokenPipeError,ConnectionResetError):return
        except (OSError,ValueError,KeyError,ProtocolError,DiscoveryError,subprocess.SubprocessError) as e:
            self.reply(400,shell('操作未完成','<section><h2>操作未完成</h2><p class="error">'+esc(e)+'</p><a href="/">錄音與備份</a> · <a href="/voices">招呼語</a> · <a href="/settings">設備設定</a></section>'))

if __name__=='__main__':
    threading.Thread(target=keepalive,daemon=True).start()
    threading.Thread(target=BACKUP.run,daemon=True).start()
    server=ThreadingHTTPServer((CONFIG.get('listen_host','127.0.0.1'),8766),Handler)
    def stop(signum,frame):
        BACKUP.stop.set();BACKUP.wake.set()
        with LOCK:invalidate()
        server.server_close();raise SystemExit(0)
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    server.serve_forever()
