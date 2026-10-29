"""Bounded, session-only native line monitoring; no recordings are written."""
import base64
import collections
import datetime as dt
import secrets
import struct
import threading
import time

STATES={1:'狀態未知',2:'線路斷開',3:'待機',4:'撥號中',5:'去電通話',6:'來電響鈴',7:'來電響鈴',8:'來電通話',9:'靜音',10:'觸發錄音',11:'未接來電',12:'自動答錄'}
ACTIVE_STATES={4,5,6,7,8,9,10,12}
STEPS=(7,8,9,10,11,12,13,14,16,17,19,21,23,25,28,31,34,37,41,45,50,55,60,66,73,80,88,97,107,118,130,143,157,173,190,209,230,253,279,307,337,371,408,449,494,544,598,658,724,796,876,963,1060,1166,1282,1411,1552,1707,1878,2066,2272,2499,2749,3024,3327,3660,4026,4428,4871,5358,5894,6484,7132,7845,8630,9493,10442,11487,12635,13899,15289,16818,18500,20350,22385,24623,27086,29794,32767)
INDEX=(-1,-1,-1,-1,2,4,6,8)

class LiveError(ValueError):pass

def decode_ima(block):
    """Native monitor subcommand 3 uses independent 256-byte IMA WAV blocks."""
    if len(block)!=256:raise LiveError('監聽音訊區塊長度不符')
    predictor,index=struct.unpack_from('<hB',block)
    if index>88:raise LiveError('監聽音訊索引無效')
    out=[predictor]
    for byte in block[4:]:
        for nibble in (byte&15,byte>>4):
            step=STEPS[index];delta=step>>3
            if nibble&4:delta+=step
            if nibble&2:delta+=step>>1
            if nibble&1:delta+=step>>2
            predictor=max(-32768,min(32767,predictor-delta if nibble&8 else predictor+delta))
            out.append(predictor);index=max(0,min(88,index+INDEX[nibble&7]))
    return struct.pack('<505h',*out)

class LiveMonitor:
    def __init__(self,clock=time.monotonic):
        self.clock=clock;self.guard=threading.Condition(threading.RLock())
        self.lines={n:dict(line=n+1,state=None,status='等待讀取',voltage=None,phone='',updated=None) for n in range(4)}
        self.diagnostics={};self.received={};self.state_watch=0;self.last_request=-100;self.refresh_pending=False;self.status_error=None
        self.line=None;self.phase='idle';self.error=None;self.listeners={};self.pending=bytearray();self.sequence=0
        self.failures={};self.stop_deadline=0
        self.audio=collections.deque(maxlen=40);self.frames=0;self.bytes=0;self.started=0;self.last_audio=None
    def watch(self):
        with self.guard:self.state_watch=self.clock()+20
    def request_status(self,c):
        with self.guard:
            if self.state_watch<self.clock() or self.clock()-self.last_request<5:return
            self.last_request=self.clock();self.refresh_pending=True
        c.live_status()
    def snapshot(self):
        with self.guard:
            now=self.clock()
            return dict(lines=[dict(v,stale=now-self.received.get(n,-100)>15) for n,v in self.lines.items()],status_stale=any(now-self.received.get(n,-100)>15 for n in self.lines),
                        status_error=self.status_error,line=self.line+1 if self.line is not None else None,phase=self.phase,
                        listeners=len(self.listeners),error=self.error,diagnostics=dict(self.diagnostics),frames=self.frames,audio_bytes=self.bytes,last_audio=self.last_audio)
    def _clear_listeners(self,error=None):
        if error:
            self.failures.update({token:(self.clock()+30,error) for token in self.listeners})
            self.failures=dict(list(self.failures.items())[-64:])
        self.listeners.clear()
    def disconnected(self):
        with self.guard:
            for row in self.lines.values():row.update(state=None,status='等待重新連線',voltage=None,phone='')
            self.received.clear();self.last_request=-100;self.refresh_pending=False
            if self.line is not None:self.phase='error';self.error='答錄機連線中斷，請重新開始監聽'
            self.line=None;self._clear_listeners(self.error);self.audio.clear();self.pending.clear();self.guard.notify_all()
    def _status(self,p):
        cmd=p[2];b=p[3:]
        if cmd==10 and len(b)==92:
            n,state,codec,_=struct.unpack_from('<4B',b);voltage=struct.unpack_from('<I',b,4)[0]
            phone=b[28:92].decode('utf-16le',errors='replace').split('\0',1)[0]
        elif cmd==9 and len(b)==96:
            n=struct.unpack_from('<I',b)[0];state,codec=struct.unpack_from('<2B',b,4);voltage=struct.unpack_from('<I',b,8)[0]
            phone=b[32:96].decode('utf-16le',errors='replace').split('\0',1)[0]
        elif cmd==11 and len(b)==10:
            n=b[0];state=b[9]
            # New firmware sends event 73 notifications here as well. Native
            # PhoneRec only accepts 2..12 as state transitions; other events
            # must not replace a full status snapshot or its line voltage.
            if not 2<=state<=12:return
            voltage=struct.unpack_from('<I',b,1)[0];phone=None
        else:return
        if n not in self.lines:return
        self.received[n]=self.clock()
        row=self.lines[n];row.update(state=state,status=STATES.get(state,'未知狀態 '+str(state)),voltage=voltage,
                                      updated=dt.datetime.now().isoformat(timespec='seconds'))
        if phone is not None:row['phone']=phone if state in ACTIVE_STATES else ''
        elif state not in ACTIVE_STATES:row['phone']=''
        self.refresh_pending=False;self.status_error=None
    def handle(self,c,p):
        with self.guard:
            if p[2] in (9,10,11,15):
                key=str(p[2])+'/'+str(p[3] if p[2]==15 else 0)
                item=self.diagnostics.setdefault(key,dict(count=0,size=0))
                item['count']+=1;item['size']=len(p)
                if p[2]==15 and p[3] in (1,2):item['ack']=p[4:].hex()
            if p[2] in (9,10,11):self._status(p)
            if p[2]!=15 or self.line is None:return
            sub=p[3]
            if sub==2 and self.phase=='stopping':
                if len(p)!=6 or p[4]!=1:
                    from dmecom import ProtocolError
                    raise ProtocolError('設備未確認停止監聽')
                self.line=None;self.listeners.clear();self.pending.clear();self.audio.clear();self.phase='idle';self.guard.notify_all();return
            if sub in (1,2):return  # ACK is validated by the requesting Client method.
            if sub not in (3,5):return
            if len(p)<5 or p[4]!=self.line:return
            if self.phase not in ('starting','listening'):return
            if self.phase=='listening' and not any(expiry>=self.clock() for expiry in self.listeners.values()):
                self.phase='stopping';self.stop_deadline=self.clock()+4;c.send(15,2,bytes(5));return
            if sub==5:
                self.error='此設備回傳另一種音訊格式，請停止後回報以供核對';self.phase='error';self.guard.notify_all();return
            self.pending.extend(p[5:])
            try:
                pcm=bytearray()
                while len(self.pending)>=256:
                    block=bytes(self.pending[:256]);del self.pending[:256];pcm.extend(decode_ima(block))
                if len(self.pending)>255:raise LiveError('監聽音訊緩衝異常')
                if pcm:
                    self.sequence+=1;self.audio.append((self.sequence,base64.b64encode(pcm).decode()))
                    self.frames+=1;self.bytes+=len(pcm);self.last_audio=dt.datetime.now().isoformat(timespec='seconds')
                    if self.phase!='starting':self.phase='listening'
                    self.guard.notify_all()
            except LiveError as e:self.phase='error';self.error=str(e);self.guard.notify_all();return
        c.live_next()
    def start(self,c,line):
        if type(line) is not int or not 1<=line<=4:raise LiveError('請選擇線路 1–4')
        with self.guard:
            if self.line is not None and self.phase in ('error','stopping'):raise LiveError('上次監聽尚未停止，請稍候再試')
            if self.line is not None and self.line!=line-1:raise LiveError('另一條線路正在被監聽，請先停止原監聽')
            if len(self.listeners)>=8:raise LiveError('同時監聽人數已達上限')
            if self.line is None:
                self.line=line-1;self.phase='starting';self.error=None;self.pending.clear();self.audio.clear();self.frames=0;self.bytes=0;self.last_audio=None;self.started=self.clock()
                try:
                    c.live_start(self.line)
                    if self.phase=='error':
                        from dmecom import ProtocolError
                        raise ProtocolError(self.error or '監聽音訊格式不符')
                except Exception:
                    # A lost ACK can leave native monitoring active; force session cleanup.
                    self.phase='error';self.error='未確認監聽啟動，已要求清理連線';raise
                self.phase='listening'
            token=secrets.token_hex(24);self.listeners[token]=self.clock()+12
            return dict(token=token,line=line,rate=8000,sequence=self.sequence)
    def detach(self,token):
        with self.guard:self.listeners.pop(token,None);self.guard.notify_all()
    def chunks(self,token,after,timeout=2):
        with self.guard:
            if token not in self.listeners or self.listeners[token]<self.clock():
                failure=self.failures.get(token)
                raise LiveError(failure[1] if failure and failure[0]>=self.clock() else '監聽已結束，請重新開始')
            if after>self.sequence:raise LiveError('音訊序號超出範圍')
            self.listeners[token]=self.clock()+12
            self.guard.wait_for(lambda:self.phase=='error' or token not in self.listeners or self.sequence>after,timeout)
            if token not in self.listeners:raise LiveError(self.failures.get(token,(0,'監聽已結束，請重新開始'))[1])
            if self.phase=='error':raise LiveError(self.error or '監聽已停止')
            # Slow clients jump to recent audio rather than building a large delay.
            chunks=[dict(sequence=n,pcm=b) for n,b in list(self.audio)[-4:] if n>after]
            return dict(chunks=chunks,sequence=chunks[-1]['sequence'] if chunks else after,line=self.line+1 if self.line is not None else None)
    def tick(self,c):
        with self.guard:
            now=self.clock()
            self.failures={token:value for token,value in self.failures.items() if value[0]>=now}
            if self.phase=='stopping' and now>self.stop_deadline:
                from dmecom import ProtocolError
                raise ProtocolError('停止監聽確認逾時，重新連線清理監聽')
            for token in list(self.listeners):
                if self.listeners[token]<now:del self.listeners[token]
            stop=self.line is not None and self.phase!='stopping' and (not self.listeners or self.phase=='error')
        if stop:
            c.live_stop()
            with self.guard:
                self.line=None;self._clear_listeners(self.error);self.pending.clear();self.audio.clear()
                if self.phase!='error':self.phase='idle'
                self.guard.notify_all()
        self.request_status(c)
