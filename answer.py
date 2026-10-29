"""Native weekly answering with a persistent before/after journal."""
import datetime as dt
import json
import hashlib
import re
from pathlib import Path
import struct
import threading
import time
import uuid
from dmecom import ProtocolError,atomic_write
DAYS=['週一','週二','週三','週四','週五','週六','週日']
KEYS=(0x2b,0x2c,0x2d,0x3a)
WRITE_KEYS=(0x2b,0x2c,0x2d,0x3a)

def parse_weekly(raw):
    if len(raw)!=4172:raise ProtocolError('未知的每週答錄設定長度：'+str(len(raw)))
    result=[]
    for day in range(7):
        b=raw[day*596:(day+1)*596];slots=[]
        all_day=struct.unpack_from('<I',b,0)[0]
        if all_day not in (0,1):raise ProtocolError('未知的全天答錄開關')
        for n in range(4):
            enabled=struct.unpack_from('<I',b,4+n*4)[0]
            h1,m1,h2,m2=struct.unpack_from('<4I',b,20+n*16)
            if enabled not in (0,1) or h1>23 or h2>23 or m1>59 or m2>59:raise ProtocolError('未知的時段格式')
            try:name=b[84+n*128:84+(n+1)*128].decode('utf-16le').split('\0')[0]
            except UnicodeDecodeError as e:raise ProtocolError('未知的時段檔名格式') from e
            slots.append(dict(enabled=enabled,start='%02d:%02d'%(h1,m1),end='%02d:%02d'%(h2,m2),file=name))
        result.append(dict(day=DAYS[day],all_day=all_day,slots=slots))
    return result

def night_profile(raw,name):
    parse_weekly(raw)
    encoded=name.encode('utf-16le')
    if not name or len(encoded)>126 or any(c in name for c in '/\\\0'):raise ValueError('招呼語檔名不合法')
    result=bytearray(raw)
    for day in range(7):
        offset=day*596
        struct.pack_into('<5I',result,offset,0,1,0,0,0)
        struct.pack_into('<4I',result,offset+20,20,0,11,0)
        result[offset+84:offset+212]=encoded+bytes(128-len(encoded))
    parse_weekly(result)
    return bytes(result)

def line_revision(values):
    return hashlib.sha256(json.dumps(values,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def encode_weekly(original,days):
    current=parse_weekly(original)
    if not isinstance(days,list) or len(days)!=7:raise ValueError('每週設定必須包含七天')
    result=bytearray(original)
    for day,entry in enumerate(days):
        if entry.get('all_day') not in (0,1) or len(entry.get('slots',[]))!=4:raise ValueError('每天必須包含四個時段及全天開關')
        off=day*596;struct.pack_into('<I',result,off,entry['all_day'])
        for n,slot in enumerate(entry['slots']):
            if slot.get('enabled') not in (0,1):raise ValueError('時段開關無效')
            times=[]
            for key in ('start','end'):
                value=slot.get(key,'')
                if not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]',value):raise ValueError('時段必須使用 HH:MM')
                times.extend(map(int,value.split(':')))
            if slot['enabled'] and slot['start']==slot['end'] and not entry['all_day']:raise ValueError('啟用時段的起迄時間不可相同；全天接聽請勾選全天')
            name=slot.get('file','');encoded=name.encode('utf-16le')
            if len(encoded)>126 or any(c in name for c in '/\\\0') or any(ord(c)<32 for c in name):raise ValueError('語音檔名無效')
            struct.pack_into('<I',result,off+4+n*4,slot['enabled'])
            struct.pack_into('<4I',result,off+20+n*16,*times)
            if name!=current[day]['slots'][n]['file']:
                result[off+84+n*128:off+84+(n+1)*128]=encoded+bytes(128-len(encoded))
    parse_weekly(result);return bytes(result)

def validate_values(values):
    for key,low,high in [(0x2b,30,1800),(0x2c,0,1),(0x2d,2,10)]:
        raw=bytes.fromhex(values[str(key)])
        if len(raw)!=4 or not low<=struct.unpack('<I',raw)[0]<=high:raise ProtocolError('接聽數值超出已驗證範圍，停止變更')
    parse_weekly(bytes.fromhex(values[str(0x3a)]))

class AnswerSettings:
    def __init__(self,root,device):
        self.root=Path(root)/'answer-settings';self.root.mkdir(parents=True,exist_ok=True);self.device=device;self.guard=threading.Lock()
    def snapshot(self):
        path=self.root/'snapshot.json';result=json.loads(path.read_text()) if path.exists() else dict(lines=[])
        active=self.root/'active.json'
        if active.exists():result['active']=json.loads(active.read_text())
        result['pending']=any(json.loads(p.read_text()).get('status')=='writing' for p in self.root.glob('change-*/journal.json'))
        return result
    def _read(self,c):
        return {str(line+1):{str(key):c.channel_parameter(line,key).hex() for key in KEYS} for line in range(4)}
    def _snapshot(self,values):
        rows=[]
        for line,items in values.items():
            def integer(key):
                b=bytes.fromhex(items[str(key)])
                if len(b)!=4:raise ProtocolError('未知的接聽數值格式')
                return struct.unpack('<I',b)[0]
            weekly=parse_weekly(bytes.fromhex(items[str(0x3a)]))
            row=dict(line=int(line),message_limit=integer(0x2b),announcement=integer(0x2c),rings=integer(0x2d),weekly=weekly,revision=line_revision(items))
            for key,data in items.items():atomic_write(self.root/('line%s-param%02x.bin'%(line,int(key))),bytes.fromhex(data))
            rows.append(row)
        result=dict(lines=rows,checked=dt.datetime.now().isoformat(timespec='seconds'))
        atomic_write(self.root/'snapshot.json',json.dumps(result,ensure_ascii=False).encode());return result
    def inspect(self):
        with self.guard:return self._snapshot(self.device(self._read))
    def _journal(self,folder,record):atomic_write(folder/'journal.json',json.dumps(record,ensure_ascii=False).encode())
    def configure(self,name,verify_voice):
        with self.guard:
            if (self.root/'active.json').exists():raise ValueError('已有接聽方案，請先還原後再變更')
            unfinished=[p for p in self.root.glob('change-*/journal.json') if json.loads(p.read_text()).get('status')=='writing']
            if unfinished:raise ProtocolError('有未完成變更，請先檢查並還原：'+unfinished[-1].parent.name)
            folder=self.root/('change-'+dt.datetime.now().strftime('%Y%m%d%H%M%S')+'-'+uuid.uuid4().hex[:6]);folder.mkdir()
            def perform(c):
                verify_voice(c)
                before=self._read(c);self._snapshot(before)
                for values in before.values():
                    limit=struct.unpack('<I',bytes.fromhex(values[str(0x2b)]))[0]
                    announcement=struct.unpack('<I',bytes.fromhex(values[str(0x2c)]))[0]
                    if not 30<=limit<=1800 or announcement not in (0,1):raise ProtocolError('原設定超出已驗證範圍，無法保證還原，停止變更')
                expected=json.loads(json.dumps(before))
                for line,values in expected.items():
                    values[str(0x2b)]=struct.pack('<I',40).hex()
                    values[str(0x2c)]=struct.pack('<I',1).hex()
                    values[str(0x3a)]=night_profile(bytes.fromhex(values[str(0x3a)]),name).hex()
                record=dict(status='writing',before=before,expected=expected,file=name,lines=[1,2,3,4],start='20:00',end='11:00',limit=40,started=dt.datetime.now().isoformat(timespec='seconds'),verified_writes=[])
                self._journal(folder,record)
                try:
                    for line,values in expected.items():
                        for key in WRITE_KEYS:
                            if values[str(key)]==before[line][str(key)]:continue
                            c.set_answer_parameter(int(line)-1,key,bytes.fromhex(values[str(key)]))
                            time.sleep(.15)
                            actual=c.channel_parameter(int(line)-1,key).hex()
                            if actual!=values[str(key)]:raise ProtocolError('接聽設定寫入後讀回不一致，線路 '+line)
                            record['verified_writes'].append([int(line),key]);self._journal(folder,record)
                    actual=self._read(c)
                    if actual!=expected:raise ProtocolError('接聽設定最終讀回不一致')
                except Exception as e:
                    record['error']=str(e);self._journal(folder,record);raise
                record.update(status='active',completed=dt.datetime.now().isoformat(timespec='seconds'));self._journal(folder,record)
                active={k:record[k] for k in ['file','lines','start','end','limit','completed']};active['journal']=folder.name
                atomic_write(self.root/'active.json',json.dumps(active,ensure_ascii=False).encode());self._snapshot(actual)
                return active
            return self.device(perform)
    def edit_lines(self,edits,revisions):
        with self.guard:
            if self.snapshot().get('pending'):raise ProtocolError('有未完成變更，請先還原')
            if not edits or any(str(line) not in {'1','2','3','4'} for line in edits):raise ValueError('請指定有效線路')
            def perform(c):
                before=self._read(c);expected=json.loads(json.dumps(before));selected=[]
                for line,edit in edits.items():
                    line=str(line);selected.append(int(line));validate_values(before[line])
                    if revisions.get(line)!=line_revision(before[line]):raise ValueError('線路 '+line+' 的設定已變更，請重新讀取後再編輯')
                    for field,key,low,high in [('message_limit',0x2b,30,1800),('announcement',0x2c,0,1),('rings',0x2d,2,10)]:
                        if field in edit:
                            value=edit[field]
                            if type(value) is not int or not low<=value<=high:raise ValueError(field+' 的數值超出範圍')
                            expected[line][str(key)]=struct.pack('<I',value).hex()
                    if 'weekly' in edit:expected[line][str(0x3a)]=encode_weekly(bytes.fromhex(before[line][str(0x3a)]),edit['weekly']).hex()
                # Check the exact device filenames before sending any write.
                files={r['name'] for r in c.voices()}
                for line in selected:
                    for day in parse_weekly(bytes.fromhex(expected[str(line)][str(0x3a)])):
                        for n,slot in enumerate(day['slots']):
                            if (slot['enabled'] or (day['all_day'] and n==0)) and slot['file'] not in files:raise ValueError('線路 '+str(line)+' 的啟用時段必須指定設備已有的語音檔')
                changes=[(str(line),key) for line in selected for key in WRITE_KEYS if before[str(line)][str(key)]!=expected[str(line)][str(key)]]
                if not changes:self._snapshot(before);return dict(changed=False)
                folder=self.root/('change-'+dt.datetime.now().strftime('%Y%m%d%H%M%S')+'-'+uuid.uuid4().hex[:6]);folder.mkdir()
                record=dict(status='writing',kind='edit',before=before,expected=expected,lines=sorted(selected),started=dt.datetime.now().isoformat(timespec='seconds'),verified_writes=[],previous_active=self.snapshot().get('active'))
                self._journal(folder,record)
                try:
                    for line,key in changes:
                        c.set_answer_parameter(int(line)-1,key,bytes.fromhex(expected[line][str(key)]));time.sleep(.15)
                        if c.channel_parameter(int(line)-1,key).hex()!=expected[line][str(key)]:raise ProtocolError('線路 '+line+' 寫入後讀回不一致')
                        record['verified_writes'].append([int(line),key]);self._journal(folder,record)
                    actual=self._read(c)
                    if actual!=expected:raise ProtocolError('最終讀回發現設定不一致，請先還原')
                except Exception as e:
                    record['error']=str(e);self._journal(folder,record);raise
                record.update(status='active',completed=dt.datetime.now().isoformat(timespec='seconds'));self._journal(folder,record)
                active=dict(kind='custom',lines=sorted(selected),journal=folder.name,completed=record['completed'])
                atomic_write(self.root/'active.json',json.dumps(active,ensure_ascii=False).encode());self._snapshot(actual)
                return dict(changed=True,**active)
            return self.device(perform)
    def restore(self,journal=None):
        with self.guard:
            active=self.snapshot().get('active')
            if journal is None:
                if active:journal=active['journal']
                else:
                    pending=[p.parent.name for p in self.root.glob('change-*/journal.json') if json.loads(p.read_text()).get('status')=='writing']
                    if not pending:raise ValueError('沒有可還原的接聽方案')
                    journal=sorted(pending)[-1]
            if '/' in journal or '\\' in journal or not journal.startswith('change-'):raise ValueError('無效變更編號')
            folder=self.root/journal;record=json.loads((folder/'journal.json').read_text())
            def perform(c):
                current=self._read(c)
                changes=[(line,key) for line in record['before'] for key in WRITE_KEYS if record['before'][line][str(key)]!=record['expected'][line][str(key)]]
                # Restore only fields owned by this change; check every one before writing.
                for line,key in changes:
                    if current[line][str(key)] not in (record['before'][line][str(key)],record['expected'][line][str(key)]):raise ProtocolError('設備設定已有其他變動，停止自動還原')
                    validate_values(record['before'][line])
                for line,key in changes:
                    original=record['before'][line][str(key)]
                    if current[line][str(key)]!=original:
                        c.set_answer_parameter(int(line)-1,key,bytes.fromhex(original));time.sleep(.15)
                        if c.channel_parameter(int(line)-1,key).hex()!=original:raise ProtocolError('還原後讀回不一致')
                actual=self._read(c)
                for line,key in changes:
                    if actual[line][str(key)]!=record['before'][line][str(key)]:raise ProtocolError('最終還原驗證失敗')
                record.update(status='restored',restored=dt.datetime.now().isoformat(timespec='seconds'));self._journal(folder,record)
                if record.get('previous_active'):atomic_write(self.root/'active.json',json.dumps(record['previous_active'],ensure_ascii=False).encode())
                else:(self.root/'active.json').unlink(missing_ok=True)
                return self._snapshot(actual)
            return self.device(perform)
