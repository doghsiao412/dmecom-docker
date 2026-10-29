"""Persist device reachability separately from the local archive service."""
import contextlib
import datetime as dt
import json
from pathlib import Path
import threading
import time
from dmecom import atomic_write

class ConnectionStatus:
    def __init__(self,root):
        self.root=Path(root);self.path=self.root/'connection-status.json';self.guard=threading.Lock();self.flushed=0
        try:self.state=json.loads(self.path.read_text())
        except (OSError,ValueError):self.state={}
        if not self.state.get('last_success'):
            try:self.state['last_success']=json.loads((self.root/'answer-settings/snapshot.json').read_text()).get('checked')
            except (OSError,ValueError):pass
        self.state.update(status='unknown',error=None)
    def snapshot(self):
        with self.guard:return dict(self.state)
    def _save(self):
        with contextlib.suppress(OSError):
            atomic_write(self.path,json.dumps(self.state,ensure_ascii=False).encode());self.flushed=time.monotonic()
    def success(self,address=None):
        with self.guard:
            changed=self.state.get('status')!='online'
            self.state.update(status='online',last_success=dt.datetime.now().isoformat(timespec='seconds'),error=None)
            if address:self.state['address']=address
            if changed or time.monotonic()-self.flushed>=30:self._save()
    def failure(self,error):
        with self.guard:
            self.state.update(status='unavailable',last_failure=dt.datetime.now().isoformat(timespec='seconds'),error=str(error));self._save()
