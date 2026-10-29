"""Device-local greeting catalog, verified backups and explicit file operations."""
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
import threading
import uuid
from answer import parse_weekly
from dmecom import ProtocolError,atomic_write,voice_filename
from voice import ima_payload

def stamp():return dt.datetime.now().isoformat(timespec='seconds')
def digest(raw):return hashlib.sha256(raw).hexdigest()
def wav_from_ima(raw):
    if len(raw)<4:raise ProtocolError('設備語音資料太短')
    full,tail=divmod(len(raw),256)
    if tail and tail<4:raise ProtocolError('設備語音區塊不完整')
    if any(raw[i+2]>88 for i in range(0,len(raw),256)):raise ProtocolError('設備語音不是已支援的 IMA ADPCM 格式')
    samples=full*505+(1+(tail-4)*2 if tail else 0)
    fmt=struct.pack('<HHIIHHHH',17,1,8000,4055,256,4,2,505)
    body=b'WAVEfmt '+struct.pack('<I',20)+fmt+b'fact'+struct.pack('<II',4,samples)+b'data'+struct.pack('<I',len(raw))+raw
    if len(raw)%2:body+=b'\0'
    return b'RIFF'+struct.pack('<I',len(body))+body

def references(c):
    refs={}
    def add(name,label):
        if name:refs.setdefault(name.casefold(),[]).append(label)
    for line in range(4):
        pickup=c.pickup_announcement(line)
        add(pickup['file'],'線路 '+str(line+1)+' 接聽宣告'+('' if pickup['enabled'] else '（目前停用）'))
        for day in parse_weekly(c.channel_parameter(line,0x3a)):
            for i,slot in enumerate(day['slots']):
                if slot['file']:add(slot['file'],'線路 '+str(line+1)+' '+day['day']+' 時段 '+str(i+1)+('' if slot['enabled'] or (day['all_day'] and i==0) else '（目前停用）'))
    return {name:list(dict.fromkeys(labels)) for name,labels in refs.items()}

class DeviceVoices:
    def __init__(self,root,device,voices,names=None):
        self.root=Path(root)/'device-voices';self.root.mkdir(parents=True,exist_ok=True)
        self.device=device;self.voices=voices;self.names=names;self.guard=threading.Lock()
    def _save(self,path,value):atomic_write(path,json.dumps(value,ensure_ascii=False).encode())
    def _path(self,key):
        if not re.fullmatch('[a-f0-9]{32}',key):raise ValueError('無效語音管理編號')
        return self.root/key
    def _catalog(self,c):
        files=c.voices();usage=references(c)
        result=dict(files=[dict(**r,usage=usage.get(r['name'].casefold(),[])) for r in files],checked=stamp())
        self._save(self.root/'catalog.json',result)
        self._save(self.voices.root/'device.json',dict(files=files,lines=[],checked=result['checked']))
        return result
    def inspect(self):
        with self.guard:return self.device(self._catalog)
    def snapshot(self):
        p=self.root/'catalog.json'
        if p.exists():result=json.loads(p.read_text())
        else:result=dict(files=[dict(**f,usage=None) for f in self.voices.snapshot()['files']],checked=None)
        backups=self.backups()
        for item in result['files']:
            item['backup']=next((b for b in backups if b['name']==item['name'] and b['source_size']==item['size']),None)
        return result
    def backups(self):
        return sorted([json.loads(p.read_text()) for p in self.root.glob('*/backup.json')],key=lambda b:b['checked'],reverse=True)
    def backup(self,key):return json.loads((self._path(key)/'backup.json').read_text())
    def _expected(self,name,size):
        for item in self.voices.items():
            if item['name']==name and item['size']==size:
                raw=(self.voices.path(item['key'])/'device.wav').read_bytes()
                if digest(raw)!=item['sha256']:raise ProtocolError('NAS 原語音檔校驗失敗')
                return len(ima_payload(raw))
        for b in self.backups():
            if b['name']==name and b['source_size']==size:return b['stream_size']
        return None
    def _read(self,c,name,size):return c.read_voice(name,size,self._expected(name,size))
    def _backup(self,c,item):
        name=item['name'];size=item['size'];raw=self._read(c,name,size)
        # Two independent reads must agree before enabling destructive file operations.
        if self._read(c,name,size)!=raw:raise ProtocolError('兩次讀取音檔不一致，停止操作')
        audio=wav_from_ima(raw);key=uuid.uuid4().hex;folder=self._path(key);folder.mkdir()
        atomic_write(folder/'stream.bin',raw);atomic_write(folder/'device.wav',audio)
        try:
            preview_raw=bytearray(raw)
            for i in range(0,len(preview_raw),256):preview_raw[i+3]=0
            result=subprocess.run(['ffmpeg','-v','error','-nostdin','-y','-i','pipe:0','-ar','8000','-ac','1','-c:a','pcm_s16le',str(folder/'preview.wav')],input=wav_from_ima(bytes(preview_raw)),stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=30)
            if result.returncode:raise ProtocolError('設備語音無法產生預聽，不開放變更')
            meta=dict(key=key,name=name,source_size=size,stream_size=len(raw),sha256=digest(raw),wav_sha256=digest(audio),checked=stamp())
            self._save(folder/'backup.json',meta);return meta
        except Exception:
            # Keep diagnostic source, but never expose a failed conversion as a valid backup.
            raise
    def fetch(self,name):
        voice_filename(name)
        with self.guard:
            def perform(c):
                catalog=self._catalog(c);item=next((r for r in catalog['files'] if r['name']==name),None)
                if not item:raise ValueError('設備音檔已不存在，請重新讀取清單')
                return self._backup(c,item)
            return self.device(perform)
    def prepare(self,operation,name,new_name=''):
        if operation not in {'rename','delete'}:raise ValueError('未知語音管理操作')
        voice_filename(name)
        if operation=='rename':
            voice_filename(new_name,31)
            if new_name.casefold()==name.casefold():raise ValueError('新檔名需與原檔名不同')
        with self.guard:
            def perform(c):
                catalog=self._catalog(c);item=next((r for r in catalog['files'] if r['name']==name),None)
                if not item:raise ValueError('原音檔已不存在')
                if item['usage']:raise ValueError('此檔案仍被接聽宣告或答錄時段引用，請先切換使用檔案再改名／刪除')
                if operation=='rename' and any(r['name'].casefold()==new_name.casefold() for r in catalog['files']):raise ValueError('新檔名已存在，拒絕覆寫')
                backup=self._backup(c,item);token=uuid.uuid4().hex
                plan=dict(token=token,operation=operation,name=name,new_name=new_name,backup=backup['key'],sha256=backup['sha256'],source_size=item['size'],prepared=stamp(),status='prepared',stage='prepared')
                self._save(self.root/('plan-'+token+'.json'),plan);return plan
            return self.device(perform)
    def plan(self,token):
        self._path(token)
        return json.loads((self.root/('plan-'+token+'.json')).read_text())
    def _plan_save(self,plan):self._save(self.root/('plan-'+plan['token']+'.json'),plan)
    def apply(self,token):
        with self.guard:
            plan=self.plan(token)
            if plan['status']=='completed':return plan
            if plan['status'] not in {'prepared','interrupted','working'}:raise ValueError('此操作無法繼續')
            if plan['status']=='prepared' and not 0<=(dt.datetime.now()-dt.datetime.fromisoformat(plan['prepared'])).total_seconds()<=600:raise ValueError('確認頁已過期，請重新準備')
            backup=self.backup(plan['backup']);folder=self._path(backup['key']);raw=(folder/'stream.bin').read_bytes();audio=(folder/'device.wav').read_bytes()
            if digest(raw)!=plan['sha256'] or digest(audio)!=backup['wav_sha256']:raise ProtocolError('還原副本校驗失敗，停止變更')
            def perform(c):
                catalog=self._catalog(c);old=next((r for r in catalog['files'] if r['name']==plan['name']),None)
                target=next((r for r in catalog['files'] if r['name'].casefold()==plan['new_name'].casefold()),None) if plan['operation']=='rename' else None
                if old:
                    if old['usage']:raise ValueError('音檔已被線路設定引用，停止變更')
                    if old['size']!=plan['source_size'] or self._read(c,old['name'],old['size'])!=raw:raise ValueError('設備原音檔已改變，請重新準備')
                elif plan['stage']!='deleting':raise ValueError('原音檔已不存在，請重新讀取清單')
                if plan['operation']=='rename':
                    if target:
                        if plan['stage'] not in {'uploading','copied','deleting'} or target['name']!=plan['new_name'] or c.read_voice(target['name'],target['size'],len(raw))!=raw:raise ValueError('新檔名已存在且不屬於本次操作，拒絕覆寫')
                    elif not old:raise ValueError('新舊音檔皆不存在，請使用還原副本')
                    else:
                        plan.update(status='working',stage='uploading');self._plan_save(plan)
                        c.upload_voice_named(plan['new_name'],audio)
                        files=c.voices();target=next((r for r in files if r['name']==plan['new_name']),None)
                        if not target or c.read_voice(target['name'],target['size'],len(raw))!=raw:raise ProtocolError('新檔名音檔讀回不一致，保留原檔')
                        alias=dict(backup,name=target['name'],source_size=target['size'],checked=stamp(),key=uuid.uuid4().hex)
                        alias_folder=self._path(alias['key']);alias_folder.mkdir()
                        for file in ['stream.bin','device.wav','preview.wav']:atomic_write(alias_folder/file,(folder/file).read_bytes())
                        self._save(alias_folder/'backup.json',alias)
                        plan.update(stage='copied');self._plan_save(plan)
                if old:
                    if references(c).get(plan['name'].casefold()):raise ValueError('音檔剛被線路設定引用，保留原檔')
                    if self._read(c,old['name'],old['size'])!=raw:raise ValueError('刪除前原音檔內容改變，停止變更')
                    plan.update(status='working',stage='deleting');self._plan_save(plan)
                    c.delete_voice(plan['name'])
                final=self._catalog(c)
                if any(r['name']==plan['name'] for r in final['files']):raise ProtocolError('設備仍列出原音檔，請重新確認')
                if plan['operation']=='rename' and self.names:self.names.copy(plan['name'],plan['new_name'])
                plan.update(status='completed',completed=stamp(),error=None);self._plan_save(plan);return plan
            try:return self.device(perform)
            except Exception as e:
                plan.update(status='interrupted',error=str(e));self._plan_save(plan);raise
    def restore(self,key):
        with self.guard:
            meta=self.backup(key);folder=self._path(key);raw=(folder/'stream.bin').read_bytes();audio=(folder/'device.wav').read_bytes()
            if digest(raw)!=meta['sha256'] or digest(audio)!=meta['wav_sha256']:raise ProtocolError('還原副本校驗失敗')
            voice_filename(meta['name'])
            def perform(c):
                files=c.voices()
                if any(r['name'].casefold()==meta['name'].casefold() for r in files):raise ValueError('設備已有同名音檔，拒絕覆寫')
                c.upload_voice_named(meta['name'],audio)
                target=next((r for r in c.voices() if r['name']==meta['name']),None)
                if not target or c.read_voice(meta['name'],target['size'],len(raw))!=raw:raise ProtocolError('還原音檔讀回不一致')
                # The reconstructed WAV may use a different header length.
                meta.update(source_size=target['size'],checked=stamp());self._save(folder/'backup.json',meta)
                return self._catalog(c)
            return self.device(perform)
    def interrupted(self):
        return sorted([json.loads(p.read_text()) for p in self.root.glob('plan-*.json') if json.loads(p.read_text())['status'] in {'working','interrupted'}],key=lambda p:p['prepared'],reverse=True)
