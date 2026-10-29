"""NAS-only friendly names; recorder filenames and audio remain unchanged."""
import json
from pathlib import Path
import threading
from dmecom import atomic_write,voice_filename

def label_for(names,filename,fallback=None):return names.get(filename.casefold()) or fallback or filename

class VoiceNames:
    def __init__(self,root):self.path=Path(root)/'voice-display-names.json';self.guard=threading.RLock()
    def all(self):
        with self.guard:
            if not self.path.exists():return {}
            result=json.loads(self.path.read_text())
            if not isinstance(result,dict) or any(not isinstance(k,str) or not isinstance(v,str) for k,v in result.items()):raise ValueError('招呼語顯示名稱資料格式無效')
            return result
    def set(self,filename,label):
        voice_filename(filename);label=label.strip()
        if len(label)>60 or any(ord(c)<32 or ord(c)==127 for c in label):raise ValueError('顯示名稱請使用 60 字以內的單行文字')
        with self.guard:
            values=self.all();key=filename.casefold()
            if label:values[key]=label
            else:values.pop(key,None)
            atomic_write(self.path,json.dumps(values,ensure_ascii=False).encode())
    def copy(self,old,new):
        with self.guard:
            values=self.all();label=values.get(old.casefold())
            if label and not values.get(new.casefold()):self.set(new,label)
