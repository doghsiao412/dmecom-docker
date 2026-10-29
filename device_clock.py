"""Inspect and explicitly synchronize the recorder with NAS Taipei time."""
import datetime as dt
import json
from pathlib import Path
import threading
import time
from zoneinfo import ZoneInfo
from dmecom import ProtocolError,atomic_write
TAIPEI=ZoneInfo('Asia/Taipei')
def local_now():return dt.datetime.now(TAIPEI)
def stamp(value):return value.isoformat(timespec='seconds')

class DeviceClock:
    def __init__(self,root,device):
        self.path=Path(root)/'device-clock.json';self.device=device;self.guard=threading.Lock()
    def snapshot(self):return json.loads(self.path.read_text()) if self.path.exists() else {}
    def inspect(self):
        with self.guard:
            def read(c):
                clock=c.clock();now=local_now()
                return dict(device=stamp(clock),nas=stamp(now),difference_seconds=round((clock-now.replace(tzinfo=None)).total_seconds(),2),checked=stamp(now))
            result=self.device(read)
            old=self.snapshot()
            if old.get('last_sync'):result['last_sync']=old['last_sync']
            atomic_write(self.path,json.dumps(result,ensure_ascii=False).encode());return result
    def sync(self):
        with self.guard:
            def perform(c):
                before=c.clock();now=local_now();difference=(before-now.replace(tzinfo=None)).total_seconds()
                audit=dict(before=stamp(before),nas=stamp(now),difference_before_seconds=round(difference,2),started=stamp(now))
                # Preserve evidence before changing the clock.
                atomic_write(self.path.with_name('clock-change-'+now.strftime('%Y%m%d%H%M%S')+'.json'),json.dumps(audit).encode())
                changed=abs(difference)>2
                if changed:c.set_clock(local_now().replace(tzinfo=None))
                for attempt in range(4):
                    after=c.clock();verified=local_now();drift=(after-verified.replace(tzinfo=None)).total_seconds()
                    if abs(drift)<=3:break
                    time.sleep(.3)
                else:raise ProtocolError('時間校正讀回仍不一致，請檢查設備時鐘')
                return dict(device=stamp(after),nas=stamp(verified),difference_seconds=round(drift,2),checked=stamp(verified),last_sync=stamp(verified),last_sync_changed=changed,before=stamp(before),difference_before_seconds=round(difference,2))
            result=self.device(perform);atomic_write(self.path,json.dumps(result,ensure_ascii=False).encode());return result
