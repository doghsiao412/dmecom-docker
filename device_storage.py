"""Read-only recorder memory-card status: capacity, loop recording and reserved space."""
import json
from pathlib import Path
import threading
from dmecom import atomic_write
from device_clock import local_now,stamp

class DeviceStorage:
    def __init__(self,root,device):
        self.path=Path(root)/'device-storage.json';self.device=device;self.guard=threading.Lock()
    def snapshot(self):return json.loads(self.path.read_text()) if self.path.exists() else {}
    def inspect(self):
        with self.guard:
            info=self.device(lambda c:c.storage())
            last=info.pop('last_loop_delete')
            info['last_loop_delete']=last.isoformat(sep=' ',timespec='minutes') if last else None
            info['checked']=stamp(local_now())
            atomic_write(self.path,json.dumps(info,ensure_ascii=False).encode());return info

def gb(mb):return '%.2f GB'%(mb/1024)

def summary(info):
    """Display values and warnings; no device I/O."""
    total,free=info['total_mb'],info['free_mb'];used=total-free
    warnings=[]
    if not info['loop_recording']:warnings.append('循環錄音已關閉：記憶卡滿了以後答錄機會停止錄音。')
    if total and free*100<total*(info['reserve_percent']+5):
        warnings.append('剩餘空間接近保留比例，答錄機即將開始刪除最舊的錄音；請確認 NAS 沒有待重試的錄音。' if info['loop_recording'] else '剩餘空間接近保留比例，記憶卡快滿了。')
    return dict(total=gb(total),used=gb(used),free=gb(free),used_percent='%.1f%%'%(used*100/total) if total else '—',warnings=warnings)
