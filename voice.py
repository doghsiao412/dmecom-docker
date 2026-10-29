"""Stage and verify greetings; selection is controlled by weekly answer settings."""
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
import uuid
from dmecom import ProtocolError, atomic_write

def ima_payload(raw):
    if raw[:4]!=b'RIFF' or raw[8:12]!=b'WAVE':raise ValueError('不是 WAV')
    end=struct.unpack_from('<I',raw,4)[0]+8
    if end!=len(raw):raise ValueError('WAV 長度不一致')
    pos=12;fmt=None;audio=None
    while pos+8<=end:
        size=struct.unpack_from('<I',raw,pos+4)[0];start=pos+8
        if start+size>end:raise ValueError('WAV 區塊長度不一致')
        if raw[pos:pos+4]==b'fmt ':fmt=struct.unpack_from('<HHIIHH',raw,start)
        if raw[pos:pos+4]==b'data':audio=raw[start:start+size]
        pos=start+size+(size%2)
    if not fmt or fmt[:3]!=(17,1,8000) or fmt[4]!=256 or not audio or len(audio)%256:raise ValueError('不支援的 IMA 語音格式')
    return audio

class Voices:
    def __init__(self,root,device):
        self.root=Path(root)/'greetings';self.root.mkdir(parents=True,exist_ok=True);self.device=device
    def path(self,key):
        if not re.fullmatch('[0-9a-f]{16}',key):raise ValueError('無效語音編號')
        return self.root/key
    def metadata(self,key):return json.loads((self.path(key)/'meta.json').read_text())
    def items(self):
        return [json.loads(p.read_text()) for p in sorted(self.root.glob('*/meta.json'),reverse=True)]
    def stage(self,data,label):
        if not 0<len(data)<=20*1024*1024:raise ValueError('請選擇 20 MB 以內的音訊')
        key=uuid.uuid4().hex[:16];folder=self.path(key);folder.mkdir()
        source=folder/'source';atomic_write(source,data)
        try:
            target=folder/'device.wav';preview=folder/'preview.wav'
            result=subprocess.run(['ffmpeg','-v','error','-nostdin','-y','-i',str(source),'-map','0:a:0','-t','181','-ar','8000','-ac','1','-c:a','adpcm_ima_wav','-block_size','256',str(target)],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=60)
            if result.returncode:raise ValueError('無法解讀音訊，請使用 WAV、MP3 或 M4A')
            probe=subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','json',str(target)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=15,check=True)
            duration=float(json.loads(probe.stdout)['format']['duration'])
            if not 0<duration<=180:raise ValueError('招呼語必須為 180 秒以內')
            subprocess.run(['ffmpeg','-v','error','-nostdin','-y','-i',str(target),'-c:a','pcm_s16le',str(preview)],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=60,check=True)
            meta=dict(key=key,label=label[:120],name='nas-'+key+'.wav',duration=round(duration,2),size=target.stat().st_size,sha256=hashlib.sha256(target.read_bytes()).hexdigest(),created=dt.datetime.now().isoformat(timespec='seconds'),uploaded=False)
            atomic_write(folder/'meta.json',json.dumps(meta,ensure_ascii=False).encode());return meta
        finally:source.unlink(missing_ok=True)
    def upload(self,key):
        meta=self.metadata(key);audio=(self.path(key)/'device.wav').read_bytes();stream=ima_payload(audio)
        if hashlib.sha256(audio).hexdigest()!=meta['sha256']:raise ProtocolError('待上傳語音檔校驗失敗')
        def perform(c):
            existing=c.voices()
            # Retry after a lost acknowledgement by comparing stored size and encoded audio bytes first.
            match=next((r for r in existing if r['name']==meta['name']),None)
            if match:
                if match['size']!=len(audio) or c.voice_stream(meta['name'],len(stream))!=stream:raise ProtocolError('設備同名檔案不同，拒絕覆寫')
            else:c.upload_voice(meta['name'],audio)
            actual=c.voice_stream(meta['name'],len(stream))
            if actual!=stream:raise ProtocolError('設備語音檔讀回校驗失敗')
        self.device(perform)
        meta.update(uploaded=True,verified=dt.datetime.now().isoformat(timespec='seconds'))
        atomic_write(self.path(key)/'meta.json',json.dumps(meta,ensure_ascii=False).encode());return meta
    def inspect(self):
        def perform(c):
            return dict(files=c.voices(),lines=[],checked=dt.datetime.now().isoformat(timespec='seconds'))
        result=self.device(perform);atomic_write(self.root/'device.json',json.dumps(result,ensure_ascii=False).encode());return result
    def snapshot(self):
        p=self.root/'device.json';return json.loads(p.read_text()) if p.exists() else dict(files=[],lines=[])
    def prepare(self,key,line):
        raise ValueError('舊套用流程已停用')
    def apply(self,key,token):
        raise ValueError('舊套用流程已停用')
    def repeat(self,key,count=3,total_seconds=40):
        source=self.metadata(key)
        if count!=3 or total_seconds!=40:raise ValueError('只提供三次播放、40 秒版本')
        if not 0<source['duration']*count<=total_seconds:raise ValueError('此音檔無法在 40 秒內完整播放三次')
        # Encode from PCM again so repeats do not include codec block padding or headers.
        result=subprocess.run(['ffmpeg','-v','error','-nostdin','-stream_loop',str(count-1),'-i',str(self.path(key)/'preview.wav'),'-af','apad,atrim=duration='+str(total_seconds),'-ar','8000','-ac','1','-c:a','pcm_s16le','-f','wav','pipe:1'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=60,check=True)
        meta=self.stage(result.stdout,source['label']+'（重複 3 次／40 秒）')
        meta.update(source_key=key,repeat_count=count,answer_limit=total_seconds)
        atomic_write(self.path(meta['key'])/'meta.json',json.dumps(meta,ensure_ascii=False).encode());return meta
