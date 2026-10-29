#!/usr/bin/env python3
"""DAR-A4100LS archive and narrowly scoped announcement client."""
import argparse, contextlib, datetime, json, os, select, socket, sqlite3, struct, tempfile, time
from pathlib import Path
from discovery import resolve_device, DiscoveryError

class ProtocolError(RuntimeError): pass
class RecordingUnavailable(ProtocolError): pass

def voice_filename(name,max_units=127):
    if not isinstance(name,str) or not name or len(name.encode('utf-16le'))>max_units*2 or not name.lower().endswith('.wav') or name!=name.strip() or any(c in name for c in '/\\:*?"<>|') or any(ord(c)<32 for c in name) or name in ('.','..'):
        raise ValueError('請使用有效的 WAV 檔名（不含路徑或特殊字元）')
    return name

def packet(cmd, sub, data=b''):
    return struct.pack('<HBB',len(data)+2,cmd,sub)+data

class Client:
    def __init__(self, config): self.config=config; self.sock=None
    def __enter__(self):
        try:
            self.address=resolve_device(self.config)
            self.sock=socket.create_connection((self.address,self.config.get('port',6066)),5)
            self.sock.settimeout(10)
            body=bytearray(318)
            for offset,key in [(0,'username'),(32,'password')]:
                encoded=self.config[key].encode('utf-16le')
                if len(encoded)>30: raise ValueError(key+' exceeds 15 UTF-16 code units')
                body[offset:offset+len(encoded)]=encoded
            self.send(1,0,body)
            result=self.wait(1)
            if result[3]!=2: raise ProtocolError('Login rejected (status %d)'%result[3])
            return self
        except Exception:
            self.__exit__(None,None,None);raise
    def __exit__(self,*args):
        if self.sock:
            with contextlib.suppress(OSError):
                self.sock.shutdown(socket.SHUT_WR)
                self.sock.settimeout(1)
                until=time.monotonic()+1
                while time.monotonic()<until and self.sock.recv(65536):pass
            self.sock.close();self.sock=None
    def send(self,cmd,sub,data=b''):
        # Explicit operations only: no firmware, erase, or generic settings API.
        allowed={(1,0),(7,0),(12,17),(12,18),(12,19),(12,6),(12,7),(12,8),
                 (13,12),(13,14),(13,15),(13,16),(13,7),(13,8),(13,9),(13,10)}
        if (cmd,sub)==(13,13) and len(data)==256:
            try:
                name=data.decode('utf-16le').split('\0')[0];encoded=voice_filename(name).encode('utf-16le')
                if data==encoded+bytes(256-len(encoded)):allowed.add((13,13))
            except (ValueError,UnicodeDecodeError):pass
        if (cmd,sub)==(8,0) and data in (b'\0\x19',b'\0\x7b'):allowed.add((8,0))
        if (cmd,sub)==(8,1) and len(data)==18 and data[:2]==b'\0\x19':allowed.add((8,1))
        if (cmd,sub)==(8,1) and len(data)==7 and data[0]==1 and data[1] in {6,0x2b,0x2c,0x2d} and data[2]<4:
            value=struct.unpack_from('<I',data,3)[0]
            if (data[1]==6 and value in {0,1}) or (data[1]==0x2b and 30<=value<=1800) or (data[1]==0x2c and value in {0,1}) or (data[1]==0x2d and 2<=value<=10):allowed.add((8,1))
        if (cmd,sub)==(8,1) and len(data)==76 and data[:2]==b'\1\x11' and data[2]<4 and data[67:]==bytes(9):
            try:
                name=data[3:67].decode('utf-16le').split('\0')[0]
                if len(name.encode('utf-16le'))<=62 and not any(c in name for c in '/\\\0') and not any(ord(c)<32 for c in name):allowed.add((8,1))
            except UnicodeDecodeError:pass
        if (cmd,sub)==(8,1) and len(data)==4175 and data[:2]==b'\1\x3a' and data[2]<4:
            from answer import parse_weekly
            parse_weekly(data[3:]);allowed.add((8,1))
        if (cmd,sub)==(8,0) and len(data)==3 and data[0]==1 and data[1] in {6,0x11,0x2b,0x2c,0x2d,0x2e,0x2f,0x36,0x37,0x38,0x39,0x3a} and data[2]<4:allowed.add((8,0))
        if (cmd,sub) not in allowed: raise ProtocolError('Command is not in the verified allowlist')
        self.sock.sendall(packet(cmd,sub,data))
    def exact(self,n):
        out=bytearray()
        while len(out)<n:
            b=self.sock.recv(n-len(out))
            if not b: raise ProtocolError('Device closed connection')
            out.extend(b)
        return bytes(out)
    def receive(self):
        h=self.exact(2);n=struct.unpack('<H',h)[0]
        if n<2: raise ProtocolError('Invalid frame length')
        frame=h+self.exact(n)
        if frame[2]!=21:self.events=(getattr(self,'events',[])+['%d/%d:%d'%(frame[2],frame[3],len(frame))])[-12:]
        return frame
    def wait(self,cmd,sub=None):
        deadline=time.monotonic()+20;seen=[]
        while time.monotonic()<deadline:
            p=self.receive()
            if p[2]!=21:seen.append('%d/%d:%d'%(p[2],p[3],len(p)))
            if p[2]==cmd and (sub is None or p[3]==sub): return p
        raise ProtocolError('未收到設備回應 %d/%s（其他封包 %s）'%(cmd,sub if sub is not None else '*',','.join(getattr(self,'events',seen)[-8:])))
    def transfer(self,expected,sub,stop,max_bytes):
        if expected>max_bytes: raise ProtocolError('Transfer exceeds configured size limit')
        out=bytearray()
        try:
            while len(out)<expected:
                self.send(12,sub,struct.pack('<I',min(32768,expected-len(out))))
                p=self.wait(12,sub)
                if len(p)<8: raise ProtocolError('Short transfer frame')
                declared=struct.unpack_from('<I',p,4)[0];chunk=p[8:]
                if declared!=len(chunk) or not chunk: raise ProtocolError('Invalid transfer chunk')
                out.extend(chunk)
                if len(out)>expected: raise ProtocolError('Transfer length mismatch')
            return bytes(out)
        finally:
            with contextlib.suppress(OSError,ProtocolError): self.send(12,stop,b'\0\0')
    def index(self,date):
        d=datetime.date.fromisoformat(date)
        body=struct.pack('<3H',d.year,d.month,d.day)+bytes(58)
        self.send(12,17,body)
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            p=self.wait(12)
            if p[3]==20:
                # Native handler 0x4f7dba advances to the next date for this response.
                if p[4:]!=b'\0\0':raise ProtocolError('Unknown unavailable-index response')
                return b''
            if p[3]==21:break
        else:raise ProtocolError('未收到日期索引回應')
        if len(p)!=72: raise ProtocolError('Invalid index response')
        size=struct.unpack_from('<I',p,4)[0]
        if struct.unpack_from('<3H',p,8)!=(d.year,d.month,d.day): raise ProtocolError('Index date mismatch')
        if size==0:
            self.send(12,19,b'\0\0');return b''
        data=self.transfer(size,18,19,64*1024*1024)
        if not data.startswith(b'SQLite format 3\0'): raise ProtocolError('Index is not SQLite')
        return data
    def audio(self,row):
        path=row['RecFilePath']
        if not path.lower().endswith('.wav') or row['FileSize']<=0: raise ProtocolError('Record has no completed WAV file')
        body=bytearray(1008);encoded=path.encode('utf-16le')
        if len(encoded)>518: raise ProtocolError('Remote file path too long')
        body[384:384+len(encoded)]=encoded
        self.send(12,6,body);p=self.wait(12,10)
        if len(p)!=8: raise ProtocolError('Invalid audio size response')
        size=struct.unpack_from('<I',p,4)[0]
        if size<12:
            self.send(12,8,b'\0\0');raise RecordingUnavailable('設備上的原始音檔已無法取得')
        data=self.transfer(size,7,8,512*1024*1024)
        if data[:4]!=b'RIFF' or data[8:12]!=b'WAVE': raise ProtocolError('Downloaded file is not WAV')
        return data

    def global_parameter(self,key):
        self.send(8,0,bytes([0,key]))
        until=time.monotonic()+20
        while time.monotonic()<until:
            p=self.wait(8,0)
            if p[4:6]==bytes([0,key]):return p[6:]
        raise ProtocolError('未收到設備全域參數')
    def clock(self):
        payload=self.global_parameter(0x19)
        if len(payload)!=16:raise ProtocolError('不支援的設備時間格式')
        year,month,weekday,day,hour,minute,second,millis=struct.unpack('<8H',payload)
        try:return datetime.datetime(year,month,day,hour,minute,second,millis*1000)
        except ValueError as e:raise ProtocolError('設備時間資料無效') from e
    def set_clock(self,stamp):
        data=struct.pack('<8H',stamp.year,stamp.month,(stamp.weekday()+1)%7,stamp.day,stamp.hour,stamp.minute,stamp.second,stamp.microsecond//1000)
        self.send(8,1,b'\0\x19'+data)
    def channel_parameter(self,line,key):
        if not 0<=line<4:raise ValueError('線路必須為 1–4')
        self.send(8,0,bytes([1,key,line]))
        until=time.monotonic()+20
        while time.monotonic()<until:
            p=self.wait(8,0)
            if p[4:7]==bytes([1,key,line]):return p[7:]
        raise ProtocolError('未收到線路設定')
    def set_answer_parameter(self,line,key,data):
        if not 0<=line<4 or key not in {0x2b,0x2c,0x2d,0x3a}:raise ValueError('不支援的接聽設定')
        self.send(8,1,bytes([1,key,line])+data)
    def pickup_announcement(self,line):
        enabled=self.channel_parameter(line,6);name=self.channel_parameter(line,0x11)
        if len(enabled)!=4 or len(name) not in (64,73):raise ProtocolError('未知的人員接聽宣告格式：%d/%d'%(len(enabled),len(name)))
        flag=struct.unpack('<I',enabled)[0]
        if flag not in (0,1):raise ProtocolError('未知的人員接聽宣告開關')
        try:filename=name[:64].decode('utf-16le').split('\0')[0]
        except UnicodeDecodeError as e:raise ProtocolError('未知的宣告檔名格式') from e
        return dict(enabled=flag,file=filename)
    def set_pickup_announcement(self,line,field,value):
        if not 0<=line<4:raise ValueError('無效線路')
        if field=='enabled':
            if type(value) is not int or value not in (0,1):raise ValueError('無效宣告開關')
            self.send(8,1,bytes([1,6,line])+struct.pack('<I',value))
        elif field=='file':
            encoded=value.encode('utf-16le')
            if len(encoded)>62 or any(c in value for c in '/\\\0') or any(ord(c)<32 for c in value):raise ValueError('宣告檔名請使用 31 個 UTF-16 字元以內的檔名')
            self.send(8,1,bytes([1,0x11,line])+encoded+bytes(64-len(encoded))+bytes(9))
        else:raise ValueError('不支援的宣告欄位')
    def greeting(self,line):
        raise ProtocolError('此韌體不回應舊式招呼語欄位，請讀取每週答錄設定')
    def set_greeting(self,line,name):
        raise ProtocolError('已停用舊套用欄位，請使用經驗證的每週答錄設定')
    def voices(self):
        self.send(13,12,b'\0\0');out=[];deadline=time.monotonic()+8;quiet=time.monotonic()+2
        while time.monotonic()<deadline:
            remaining=quiet-time.monotonic()
            if remaining<=0:break
            if not select.select([self.sock],[],[],min(.2,remaining))[0]:continue
            p=self.receive()
            if p[2:4]!=bytes([13,12]):continue
            if len(p)!=564:raise ProtocolError('未知的語音清單格式')
            b=p[4:];name=b[40:560].decode('utf-16le').split('\0')[0]
            if not name:continue
            size=(struct.unpack_from('<I',b,28)[0]<<32)|struct.unpack_from('<I',b,32)[0]
            out.append(dict(name=name,size=size));quiet=time.monotonic()+1.2
            if len(out)>512:raise ProtocolError('語音檔數量超過限制')
        return out
    def voice_stream(self,name,expected):
        encoded=name.encode('utf-16le')
        if len(encoded)>254 or any(x in name for x in '/\\') or not 0<expected<=0x12c000:raise ValueError('不支援的語音檔')
        self.send(13,14,encoded+bytes(256-len(encoded)))
        p=self.wait(13,14)
        if len(p)<5 or p[4]!=1:raise ProtocolError('設備無法讀取該語音檔')
        out=bytearray()
        try:
            while len(out)<expected:
                self.send(13,15,struct.pack('<I',4096));p=self.wait(13,15)
                if not p[4:]:raise ProtocolError('語音串流提早結束')
                out.extend(p[4:])
                if len(out)>expected:raise ProtocolError('語音串流大小不一致')
            return bytes(out)
        finally:
            # Windows uses 13/16 to stop local voice playback / close its stream.
            with contextlib.suppress(Exception):self.send(13,16,struct.pack('<I',4096))
    def delete_voice(self,name):
        encoded=voice_filename(name).encode('utf-16le')
        self.send(13,13,encoded+bytes(256-len(encoded)))
        p=self.wait(13,13)
        if len(p)<5 or p[4]!=1:raise ProtocolError('設備拒絕刪除語音檔，可能正在使用中')

    def read_voice(self,name,size,expected=None):
        encoded=voice_filename(name).encode('utf-16le')
        if not 44<size<=0x12c000:raise ValueError('設備音檔大小不在支援範圍')
        self.send(13,14,encoded+bytes(256-len(encoded)))
        p=self.wait(13,14)
        if len(p)<5 or p[4]!=1:raise ProtocolError('設備無法讀取該語音檔')
        out=bytearray()
        try:
            while True:
                self.send(13,15,struct.pack('<I',4096));p=self.wait(13,15);chunk=p[4:]
                if not 0<len(chunk)<=4096:raise ProtocolError('設備語音串流長度無效')
                out.extend(chunk)
                if len(out)>size:raise ProtocolError('設備語音串流超過檔案大小')
                if len(chunk)<4096:break
                if expected is not None and len(out)==expected:break
                # Exact-block streams do not send an EOF. Only infer completion for
                # the canonical 94-byte header produced by this console's encoder.
                if expected is None and name.startswith('nas-') and size-len(out)==94:break
                if expected is None and size-len(out)<=128:raise ProtocolError('未知的整塊語音檔頭，無法確認完整性；保留設備原檔')
            if expected is not None and len(out)!=expected:raise ProtocolError('設備語音內容長度改變')
            if not 0<size-len(out)<=4096:raise ProtocolError('無法確認設備語音串流完整性')
            return bytes(out)
        finally:
            with contextlib.suppress(OSError,ProtocolError):self.send(13,16,struct.pack('<I',4096))

    def upload_voice_named(self,name,audio):
        voice_filename(name)
        return self._upload_voice(name,audio)

    def upload_voice(self,name,audio):
        if not name.startswith('nas-') or len(name.encode('utf-16le'))>62:raise ValueError('只接受 NAS 產生的唯一語音檔名')
        return self._upload_voice(name,audio)

    def _upload_voice(self,name,audio):
        voice_filename(name)
        if any(r['name'].casefold()==name.casefold() for r in self.voices()):raise ProtocolError('同名語音檔已存在，拒絕覆寫')
        if not 44<len(audio)<=0x12c000:raise ValueError('語音檔大小不合規格')
        d=datetime.datetime.now();stamp=struct.pack('<8H',d.year,d.month,(d.weekday()+1)%7,d.day,d.hour,d.minute,d.second,0)
        encoded=name.encode('utf-16le');self.send(13,7,encoded+bytes(256-len(encoded))+struct.pack('<I',len(audio))+stamp)
        offset=0;deadline=time.monotonic()+120;ended=False
        try:
            while time.monotonic()<deadline:
                p=self.wait(13)
                if p[3]==10:raise ProtocolError('設備回報語音檔上傳失敗')
                if p[3]==9:
                    if offset!=len(audio) or not ended:raise ProtocolError('設備提早回報上傳完成')
                    return
                if p[3]!=8:continue
                if len(p)!=8:raise ProtocolError('不支援的上傳區塊請求')
                n=struct.unpack_from('<I',p,4)[0]
                if not 0<n<=60000:raise ProtocolError('不支援的上傳區塊大小')
                if ended:continue
                part=audio[offset:offset+n]
                self.send(13,8,part);offset+=len(part)
                if offset==len(audio):self.send(13,9,b'\0\0');ended=True
            raise ProtocolError('語音上傳逾時')
        except Exception:
            with contextlib.suppress(Exception):self.send(13,10,b'\0\0')
            raise

def rows_from_index(data):
    if not data: return []
    with tempfile.TemporaryDirectory() as td:
        p=Path(td)/'index.sqlite';p.write_bytes(data)
        with sqlite3.connect('file:'+str(p)+'?mode=ro',uri=True) as db:
            if db.execute('PRAGMA quick_check').fetchone()[0]!='ok': raise ProtocolError('SQLite integrity check failed')
            db.row_factory=sqlite3.Row
            rows=[dict(r) for r in db.execute('SELECT * FROM CdrTable ORDER BY AutoID')]
    for r in rows:
        stamp=datetime.datetime(1899,12,30)+datetime.timedelta(days=r['RecDate'],microseconds=500000)
        r['timestamp']=stamp.replace(microsecond=0).isoformat(timespec='seconds')
        r['has_audio']=bool(r['FileSize']>0 and r['RecFilePath'].lower().endswith('.wav'))
        r['direction']=recording_direction(r)
    return rows

def recording_direction(row):
    name=str(row.get('RecFilePath') or '').replace('\\','/').rsplit('/',1)[-1]
    parts=name.split('-')
    if len(parts)>1 and parts[1] in ('I','O'):
        return {'I':'撥入','O':'撥出'}[parts[1]]
    return {1:'撥出',2:'撥入',4:'撥入'}.get(row.get('RecType'),'未知')

def atomic_write(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix='.'+path.name,dir=str(path.parent))
    try:
        with os.fdopen(fd,'wb') as f:f.write(data)
        os.replace(tmp,path)
    finally:
        with contextlib.suppress(FileNotFoundError):os.unlink(tmp)

def normalize_phone(value):
    phone=str(value or '').strip()
    # Keep missing caller ID unknown; preserve +country codes and nonnumeric labels.
    if phone and all('0'<=c<='9' for c in phone) and not phone.startswith('0'):phone='0'+phone
    return phone

def normalize_record(row):
    row=dict(row)
    raw=row.get('PhoneNumberRaw',row.get('PhoneNumber') or '')
    row['PhoneNumberRaw']=raw;row['PhoneNumber']=normalize_phone(raw)
    return row

def recording_filename(row):
    stamp=datetime.datetime.fromisoformat(row['timestamp']).strftime('%Y%m%d-%H%M%S')
    phone=normalize_phone(row.get('PhoneNumberRaw',row.get('PhoneNumber'))) or '未知電話'
    phone=''.join(c for c in phone if ord(c)>=32 and ord(c)!=127)
    for char in '/\\:\"<>|?*':phone=phone.replace(char,'_')
    phone=phone.strip(' .') or '未知電話'
    direction={'撥入':'IN','撥出':'OUT'}.get(recording_direction(row),'UNKNOWN')
    return stamp+'-'+phone+'-'+direction+'.wav'

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--config',default=str(Path(__file__).with_name('config.json')))
    ap.add_argument('action',choices=['list','download']);ap.add_argument('--date',default=datetime.date.today().isoformat());ap.add_argument('--id',type=int);ap.add_argument('--output',default='downloads')
    a=ap.parse_args();config=json.loads(Path(a.config).read_text())
    with Client(config) as c:
        data=c.index(a.date);rows=rows_from_index(data)
        if a.action=='list':print(json.dumps(rows,ensure_ascii=False,indent=2));return
        matches=[r for r in rows if r['AutoID']==a.id]
        if len(matches)!=1:ap.error('Specify a valid --id from the chosen date')
        audio=c.audio(matches[0]);dest=Path(a.output)/a.date/str(a.id)/recording_filename(matches[0]);atomic_write(dest.with_suffix('.dar.wav'),audio);atomic_write(dest,standard_wav(audio));print(dest)

def standard_wav(raw):
    """Keep original RIFF extent; DAR uses IMA reserved byte as block counter."""
    if raw[:4]!=b'RIFF' or raw[8:12]!=b'WAVE':raise ProtocolError('Not WAV')
    end=struct.unpack_from('<I',raw,4)[0]+8
    if not 12<=end<=len(raw):raise ProtocolError('Invalid RIFF size')
    out=bytearray(raw[:end]);pos=12;fmt=None;data=None
    while pos+8<=end:
        size=struct.unpack_from('<I',out,pos+4)[0];tag=bytes(out[pos:pos+4]);start=pos+8
        if start+size>end:raise ProtocolError('Invalid WAV chunk')
        if tag==b'fmt ':fmt=struct.unpack_from('<HHIIHH',out,start)
        if tag==b'data':data=(start,size)
        pos=start+size+(size%2)
    if fmt and fmt[0]==17 and fmt[1]==1 and data:
        start,size=data;align=fmt[4]
        if align<4 or size%align:raise ProtocolError('Unsupported IMA block layout')
        for n,i in enumerate(range(start,start+size,align)):
            if out[i+2]>88:raise ProtocolError('Invalid IMA step index')
            # This byte is reserved by IMA; DAR counters can restart mid-file.
            # It has no encoded audio information. Preserve it in the raw copy.
            out[i+3]=0
    return bytes(out)

if __name__=='__main__':main()
