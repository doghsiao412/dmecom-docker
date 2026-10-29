"""Human-pickup recording notice, independent from native auto-answer settings."""
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import threading
import time
import uuid
from dmecom import ProtocolError,atomic_write

def revision(value):return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()
def validate_filename(name):
    if len(name.encode('utf-16le'))>62 or any(c in name for c in '/\\\0') or any(ord(c)<32 for c in name):raise ValueError('宣告檔名須在 31 個 UTF-16 字元內，且不含路徑')

def changes(plan):
    return [(line,field) for line in plan['lines'] for field in ('file','enabled') if plan['before'][str(line)][field]!=plan['after'][str(line)][field]]

class PickupAnnouncements:
    def __init__(self,root,device):
        self.root=Path(root)/'pickup-announcements';self.root.mkdir(parents=True,exist_ok=True);self.device=device;self.guard=threading.Lock()
    def path(self,token):
        if not re.fullmatch('[a-f0-9]{32}',token):raise ValueError('無效確認編號')
        return self.root/('plan-'+token+'.json')
    def save(self,plan):atomic_write(self.path(plan['token']),json.dumps(plan,ensure_ascii=False).encode())
    def snapshot(self):
        p=self.root/'snapshot.json';result=json.loads(p.read_text()) if p.exists() else dict(lines=[])
        active=self.root/'active.json'
        if active.exists():result['active']=json.loads(active.read_text())
        result['pending']=any(json.loads(p.read_text()).get('status')=='writing' for p in self.root.glob('plan-*.json'))
        return result
    def _read(self,c):return {str(line+1):c.pickup_announcement(line) for line in range(4)}
    def _snapshot(self,values):
        data=dict(lines=[dict(line=int(line),revision=revision(value),**value) for line,value in values.items()],checked=dt.datetime.now().isoformat(timespec='seconds'))
        atomic_write(self.root/'snapshot.json',json.dumps(data,ensure_ascii=False).encode());return data
    def inspect(self):
        with self.guard:return self._snapshot(self.device(self._read))
    def prepare(self,lines,filename,enabled,revisions):
        with self.guard:
            if self.snapshot().get('pending'):raise ProtocolError('有未完成的宣告變更，請先還原')
            lines=sorted(set(lines))
            if not lines or any(type(n) is not int or not 1<=n<=4 for n in lines):raise ValueError('請選擇至少一條有效線路')
            if enabled is not None and (type(enabled) is not int or enabled not in (0,1)):raise ValueError('無效宣告開關')
            if filename is not None:validate_filename(filename)
            def perform(c):
                before=self._read(c);after=json.loads(json.dumps(before));files={x['name'] for x in c.voices()}
                for line in lines:
                    key=str(line)
                    if revisions.get(key)!=revision(before[key]):raise ValueError('線路 '+key+' 的宣告已變更，請重新讀取')
                    validate_filename(before[key]['file'])
                    if filename is not None:after[key]['file']=filename
                    if enabled is not None:after[key]['enabled']=enabled
                    if (after[key]['enabled'] or filename) and after[key]['file'] not in files:raise ValueError('請先將宣告音檔傳送到錄音機，再選擇該檔')
                self._snapshot(before)
                plan=dict(token=uuid.uuid4().hex,status='prepared',lines=lines,before=before,after=after,prepared=dt.datetime.now().isoformat(timespec='seconds'))
                self.save(plan);return plan
            return self.device(perform)
    def _write(self,c,plan,target):
        fields=changes(plan)
        # Disable first when requested; otherwise install the file before enabling playback.
        for line in plan['lines']:
            order=('enabled','file') if target[str(line)]['enabled']==0 else ('file','enabled')
            for field in order:
                if (line,field) not in fields:continue
                if c.pickup_announcement(line-1)[field]!=target[str(line)][field]:
                    c.set_pickup_announcement(line-1,field,target[str(line)][field]);time.sleep(.15)
                    if c.pickup_announcement(line-1)[field]!=target[str(line)][field]:raise ProtocolError('線路 '+str(line)+' 的宣告寫入後讀回不一致')
        actual=self._read(c)
        for line,field in fields:
            if actual[str(line)][field]!=target[str(line)][field]:raise ProtocolError('宣告最終讀回驗證失敗')
        self._snapshot(actual)
    def apply(self,token,verify_voice=None):
        with self.guard:
            plan=json.loads(self.path(token).read_text())
            if plan['status']=='active':return plan
            if plan['status']!='prepared':raise ValueError('此確認已使用或未完成，請重新讀取設定')
            age=(dt.datetime.now()-dt.datetime.fromisoformat(plan['prepared'])).total_seconds()
            if not 0<=age<=600:raise ValueError('確認頁已逾時，請重新準備')
            if self.snapshot().get('pending'):raise ProtocolError('有未完成的宣告變更，請先還原')
            def perform(c):
                current=self._read(c);files={x['name'] for x in c.voices()}
                for line in plan['lines']:
                    key=str(line)
                    if current[key]!=plan['before'][key]:raise ValueError('設備宣告已變更，請重新讀取與確認')
                    if plan['after'][key]['enabled'] and plan['after'][key]['file'] not in files:raise ValueError('選定的宣告音檔已不在設備上')
                if verify_voice:verify_voice(c,plan)
                if not changes(plan):plan['status']='no_change';self.save(plan);return plan
                plan.update(status='writing',previous_active=self.snapshot().get('active'));self.save(plan)
                try:self._write(c,plan,plan['after'])
                except Exception as e:plan['error']=str(e);self.save(plan);raise
                plan.update(status='active',completed=dt.datetime.now().isoformat(timespec='seconds'));self.save(plan)
                atomic_write(self.root/'active.json',json.dumps(dict(token=token,completed=plan['completed']),ensure_ascii=False).encode());return plan
            return self.device(perform)
    def restore(self):
        with self.guard:
            pending=[p for p in self.root.glob('plan-*.json') if json.loads(p.read_text()).get('status')=='writing']
            if pending:plan=json.loads(sorted(pending)[-1].read_text())
            else:
                active=self.snapshot().get('active')
                if not active:raise ValueError('沒有可還原的宣告變更')
                plan=json.loads(self.path(active['token']).read_text())
            def perform(c):
                current=self._read(c);files={x['name'] for x in c.voices()}
                for line,field in changes(plan):
                    key=str(line)
                    if current[key][field] not in (plan['before'][key][field],plan['after'][key][field]):raise ValueError('宣告有其他變動，停止自動還原')
                for line in plan['lines']:
                    value=plan['before'][str(line)]
                    if value['enabled'] and value['file'] not in files:raise ValueError('原宣告檔已不在設備，請先還原該音檔')
                self._write(c,plan,plan['before']);plan.update(status='restored',restored=dt.datetime.now().isoformat(timespec='seconds'));self.save(plan)
                if plan.get('previous_active'):atomic_write(self.root/'active.json',json.dumps(plan['previous_active'],ensure_ascii=False).encode())
                else:(self.root/'active.json').unlink(missing_ok=True)
                return plan
            return self.device(perform)
