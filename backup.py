"""Durable local archive and one-worker hourly backup."""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import threading
import recording_query
import time
import wave
from discovery import DiscoveryError
from dmecom import ProtocolError, RecordingUnavailable, atomic_write, recording_filename, rows_from_index, standard_wav, normalize_record

class RecordingConversionError(ProtocolError): pass
class RecordingSkipped(Exception): pass

# One definition shared by the retry list, counters, and background worker.
UNSAVED = "json_extract(row_json,'$.has_audio')=1 AND (saved_version IS NULL OR saved_version!=version OR pcm IS NULL)"
NOT_SKIPPED = '(skipped_version IS NULL OR skipped_version!=version)'
FAILED = UNSAVED + ' AND error IS NOT NULL AND ' + NOT_SKIPPED
SKIPPED = UNSAVED + ' AND skipped_version=version'


def now(): return dt.datetime.now().isoformat(timespec='seconds')
def identity(row): return hashlib.sha256(row['RecFilePath'].encode()).hexdigest()[:24]
def version(row):
    return hashlib.sha256(json.dumps([row['RecFilePath'],row['FileSize'],row['RecDate']],ensure_ascii=False).encode()).hexdigest()

def pcm_info(path):
    path=Path(path)
    try:
        with wave.open(str(path),'rb') as audio:
            channels,width,rate,frames=audio.getnchannels(),audio.getsampwidth(),audio.getframerate(),audio.getnframes()
            if audio.getcomptype()!='NONE' or channels!=1 or width!=2 or rate!=8000 or frames<=0:raise ValueError('不是預期的 8 kHz 單聲道 PCM')
            tail=min(frames,4096);audio.setpos(frames-tail)
            if len(audio.readframes(tail))!=tail*channels*width:raise ValueError('PCM 音檔不完整')
    except (wave.Error,EOFError,ValueError) as e:raise RecordingConversionError('PCM 檢查失敗：'+str(e)) from e
    digest=hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda:source.read(1024*1024),b''):digest.update(chunk)
    return dict(size=path.stat().st_size,sha256=digest.hexdigest(),frames=frames,duration=frames/rate)

def convert_pcm(raw,path):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);temporary=Path(str(path)+'.partial.wav')
    try:
        try:normalized=standard_wav(raw)
        except ProtocolError as e:raise RecordingConversionError('下載音檔格式無法轉換：'+str(e)) from e
        result=subprocess.run(['ffmpeg','-v','error','-nostdin','-y','-i','pipe:0','-ar','8000','-ac','1','-c:a','pcm_s16le',str(temporary)],input=normalized,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=180)
        if result.returncode or not temporary.is_file():raise RecordingConversionError('PCM 轉換失敗，將於設備連線後重試')
        info=pcm_info(temporary);os.replace(temporary,path);return info
    except subprocess.TimeoutExpired as e:raise RecordingConversionError('PCM 轉換逾時') from e
    finally:temporary.unlink(missing_ok=True)

class Archive:
    def __init__(self, root):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
        self.database=self.root/'archive.sqlite'
        with self.db() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS recordings(day TEXT, key TEXT, row_json TEXT, version TEXT, saved_version TEXT, pcm TEXT, raw_hash TEXT, error TEXT, PRIMARY KEY(day,key));
            CREATE TABLE IF NOT EXISTS days(day TEXT PRIMARY KEY, checked TEXT, error TEXT);
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);''')
            columns={r[1] for r in db.execute('PRAGMA table_info(recordings)')}
            for name,kind in [('pcm_hash','TEXT'),('pcm_size','INTEGER'),('skipped_version','TEXT'),('skipped_at','TEXT')]:
                if name not in columns:db.execute('ALTER TABLE recordings ADD COLUMN '+name+' '+kind)
    def db(self):
        db=sqlite3.connect(self.database,timeout=30);db.row_factory=sqlite3.Row;return db
    def get(self,key,default=None):
        with self.db() as db: r=db.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
        return json.loads(r[0]) if r else default
    def set(self,key,value):
        with self.db() as db:db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(key,json.dumps(value,ensure_ascii=False)))
    def _decode(self,data):
        out=[]
        for r in data:
            row=normalize_record(json.loads(r['row_json']));row.update(day=r['day'],key=r['key'],backed_up=r['saved_version']==r['version'] and bool(r['pcm']) and (self.root/r['pcm']).is_file(),error=r['error'],version=r['version'],skipped=r['skipped_version']==r['version'],skipped_at=r['skipped_at'] if r['skipped_version']==r['version'] else None);out.append(row)
        return out
    def rows(self,day):
        dt.date.fromisoformat(day)
        with self.db() as db:data=db.execute('SELECT * FROM recordings WHERE day=? ORDER BY json_extract(row_json,\'$.RecDate\')',(day,)).fetchall()
        return self._decode(data)
    def search(self,start='',end='',phone='',direction='ALL',page=1,page_size=100,call_type='ALL',line=0,min_seconds=None,max_seconds=None,sort='time'):
        if page<1 or not 1<=page_size<=200:raise ValueError('無效分頁條件')
        if sort not in recording_query.SORTS:raise ValueError('無效排序方式')
        where,args=recording_query.where_clause(start,end,phone,direction,call_type,line,min_seconds,max_seconds)
        with self.db() as db:
            total,saved=db.execute('SELECT count(*),coalesce(sum(CASE WHEN saved_version=version AND pcm IS NOT NULL THEN 1 ELSE 0 END),0) FROM recordings'+where,args).fetchone()
            pages=max(1,(total+page_size-1)//page_size);page=min(page,pages)
            data=db.execute("SELECT * FROM recordings"+where+" ORDER BY "+recording_query.SORTS[sort][1]+" LIMIT ? OFFSET ?",args+[page_size,(page-1)*page_size]).fetchall()
        return dict(rows=self._decode(data),total=total,backed_up=saved,page=page,pages=pages,page_size=page_size)
    def export(self,limit=100000,sort='time',page=None,**filters):
        if sort not in recording_query.SORTS:raise ValueError('無效排序方式')
        where,args=recording_query.where_clause(**filters)
        with self.db() as db:
            total=db.execute('SELECT count(*) FROM recordings'+where,args).fetchone()[0]
            if total>limit:raise ValueError('符合 %d 筆，超過匯出上限 %d 筆，請縮小範圍'%(total,limit))
            data=db.execute("SELECT * FROM recordings"+where+" ORDER BY "+recording_query.SORTS[sort][1],args).fetchall()
        return self._decode(data)
    def statistics(self,period='day',sort=None,page=None,**filters):
        if period not in recording_query.PERIODS:raise ValueError('無效統計週期')
        where,args=recording_query.where_clause(**filters)
        with self.db() as db:return recording_query.statistics(db,where,args,period)
    def retry_queue(self,view='pending',page=1,page_size=100):
        if view not in ('pending','skipped'):raise ValueError('無效的重試清單')
        if not isinstance(page,int) or not 1<=page<=1000000 or not 1<=page_size<=100:raise ValueError('無效頁碼')
        where=FAILED if view=='pending' else SKIPPED
        with self.db() as db:
            total=db.execute('SELECT count(*) FROM recordings WHERE '+where).fetchone()[0]
            pages=max(1,(total+page_size-1)//page_size);page=min(page,pages)
            rows=db.execute("SELECT * FROM recordings WHERE "+where+" ORDER BY json_extract(row_json,'$.timestamp') DESC,day,key LIMIT ? OFFSET ?",(page_size,(page-1)*page_size)).fetchall()
        return dict(rows=self._decode(rows),total=total,page=page,pages=pages,view=view)
    def retry_action(self,items,operation):
        if operation not in ('skip','retry'):raise ValueError('無效的重試操作')
        if not isinstance(items,list) or not 1<=len(items)<=100:raise ValueError('請先勾選 1～100 筆錄音')
        selected=[]
        for item in items:
            if not isinstance(item,list) or len(item)!=3 or not all(isinstance(v,str) for v in item):raise ValueError('無效錄音識別資料')
            day,key,revision=item;dt.date.fromisoformat(day)
            if not key or len(key)>128 or not revision or len(revision)>128:raise ValueError('無效錄音識別資料')
            if item not in selected:selected.append(item)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            # Validate the entire batch before changing anything. A completed
            # download or refreshed index must never be overwritten by an old tab.
            for day,key,revision in selected:
                row=db.execute('SELECT version FROM recordings WHERE day=? AND key=? AND '+UNSAVED,(day,key)).fetchone()
                if not row or row['version']!=revision:raise ValueError('錄音狀態已變更，請重新整理清單後操作')
            for day,key,revision in selected:
                db.execute('UPDATE recordings SET skipped_version=?,skipped_at=? WHERE day=? AND key=?',
                           (revision if operation=='skip' else None,now() if operation=='skip' else None,day,key))
        return len(selected)
    def is_skipped(self,day,key):
        with self.db() as db:return db.execute('SELECT 1 FROM recordings WHERE day=? AND key=? AND skipped_version=version',(day,key)).fetchone() is not None
    def ingest(self,day,data):
        rows=[normalize_record(r) for r in rows_from_index(data)] # Keep the original caller ID alongside display normalization.
        if data:atomic_write(self.root/'indexes'/(day+'.sqlite'),data)
        with self.db() as db:
            for row in rows:
                db.execute('''INSERT INTO recordings(day,key,row_json,version) VALUES(?,?,?,?) ON CONFLICT(day,key) DO UPDATE SET row_json=excluded.row_json,version=excluded.version''',(day,identity(row),json.dumps(row,ensure_ascii=False),version(row)))
        return rows
    def cached(self,day,key):
        with self.db() as db:r=db.execute('SELECT * FROM recordings WHERE day=? AND key=?',(day,key)).fetchone()
        if r and r['saved_version']==r['version'] and r['pcm']:
            p=self.root/r['pcm']
            if p.is_file() and p.stat().st_size>44 and (r['pcm_size'] is None or p.stat().st_size==r['pcm_size']):return p
        return None
    def save(self,day,row,fetch):
        key=identity(row);cached=self.cached(day,key)
        if cached:return cached,False
        if self.is_skipped(day,key):raise RecordingSkipped('此筆錄音已略過')
        if shutil.disk_usage(self.root).free<512*1024*1024:raise OSError('NAS 可用空間少於 512 MB，備份暫停')
        relative=Path(day)/str(row['AutoID'])/recording_filename(row)
        original=(self.root/'original'/relative).with_suffix('.dar.wav')
        # Reuse a previous manual download only when its path and size agree.
        if original.exists() and original.stat().st_size==row['FileSize']:
            raw=original.read_bytes()
        else:
            raw=fetch(row)
        pcm=self.root/'pcm'/relative;info=convert_pcm(raw,pcm)
        with self.db() as db:
            db.execute('UPDATE recordings SET saved_version=?,pcm=?,raw_hash=?,pcm_hash=?,pcm_size=?,error=NULL WHERE day=? AND key=?',(version(row),str(pcm.relative_to(self.root)),hashlib.sha256(raw).hexdigest(),info['sha256'],info['size'],day,key))
        return pcm,True
    def record_error(self,day,key,message):
        with self.db() as db:db.execute('UPDATE recordings SET error=? WHERE day=? AND key=?',(message,day,key))
    def stats(self):
        with self.db() as db:
            saved=db.execute('SELECT count(*),coalesce(sum(json_extract(row_json,\'$.FileSize\')),0) FROM recordings WHERE saved_version=version AND pcm IS NOT NULL').fetchone()
            total=db.execute('SELECT count(*) FROM recordings WHERE json_extract(row_json,\'$.has_audio\')=1').fetchone()[0]
            failed=db.execute('SELECT count(*) FROM recordings WHERE '+FAILED).fetchone()[0]
            skipped=db.execute('SELECT count(*) FROM recordings WHERE '+SKIPPED).fetchone()[0]
            days=db.execute('SELECT count(*) FROM days').fetchone()[0]
            bounds=db.execute('SELECT min(day),max(day) FROM recordings').fetchone()
        return dict(saved=saved[0],original_bytes=saved[1],total=total,failed=failed,skipped=skipped,scanned_days=days,oldest=bounds[0],newest=bounds[1])

class Backup:
    def __init__(self,archive,read_index,read_audio,config=None):
        self.archive=archive;self.read_index=read_index;self.read_audio=read_audio
        self.interval=int(archive.get('backup_interval_seconds',(config or {}).get('backup_interval_seconds',3600)))
        if self.interval<60:raise ValueError('備份間隔至少 60 秒')
        # The daily database uses OLE dates; scan every positive OLE calendar day,
        # including empty gaps, instead of assuming an empty day ends history.
        self.floor=dt.date.fromisoformat((config or {}).get('history_start','1899-12-30'))
        self.wake=threading.Event();self.stop=threading.Event();self.guard=threading.Lock();self.running=False
        self.state=archive.get('backup_state',{})
        self.state.update(running=False,interval_seconds=self.interval)
    def update(self,**fields):
        with self.guard:
            self.state.update(fields);self.archive.set('backup_state',self.state)
    def status(self):
        with self.guard:s=dict(self.state)
        s.update(self.archive.stats());return s
    def trigger(self):
        with self.guard:busy=self.running
        if not busy:self.wake.set()
        return not busy
    def set_interval(self,minutes):
        minutes=int(minutes)
        if not 1<=minutes<=10080:raise ValueError('備份間隔請填 1～10080 分鐘')
        interval=minutes*60
        with self.guard:
            self.interval=interval;self.archive.set('backup_interval_seconds',interval)
            self.state['interval_seconds']=interval
            if not self.running:
                due=time.time()+(min(60,interval) if self.state.get('error') else interval);self.archive.set('next_backup_epoch',due)
                self.state['next_backup']=dt.datetime.fromtimestamp(due).isoformat(timespec='seconds')
            self.archive.set('backup_state',self.state)
        return interval
    def sync_day(self,day):
        self.update(current_date=day)
        try:
            data=self.read_index(day);rows=self.archive.ingest(day,data)
            # Include archived pending rows even if the recorder has rotated its index.
            for row in self.archive.rows(day):
                if self.stop.is_set():return
                if not row['has_audio'] or row['skipped'] or self.archive.cached(day,row['key']):continue
                self.update(current_record=row['timestamp'])
                try:self.archive.save(day,row,self.read_audio)
                except RecordingSkipped:continue
                except (RecordingUnavailable,RecordingConversionError) as e:
                    self.archive.record_error(day,row['key'],str(e))
                except Exception as e:
                    self.archive.record_error(day,row['key'],str(e));raise
                if self.stop.wait(.03):return
            with self.archive.db() as db:db.execute('INSERT OR REPLACE INTO days VALUES(?,?,NULL)',(day,now()))
        except Exception as e:
            with self.archive.db() as db:db.execute('INSERT OR REPLACE INTO days VALUES(?,?,?)',(day,now(),str(e)))
            raise
    def recent(self):
        today=dt.date.today()
        last=self.archive.get('last_recent_day',today.isoformat())
        first=min(dt.date.fromisoformat(last)-dt.timedelta(days=1),today-dt.timedelta(days=1))
        day=today
        while day>=first and not self.stop.is_set():self.sync_day(day.isoformat());day-=dt.timedelta(days=1)
        with self.archive.db() as db:
            retry=[r[0] for r in db.execute("SELECT DISTINCT day FROM recordings WHERE "+UNSAVED+" AND "+NOT_SKIPPED)]
            retry += [r[0] for r in db.execute('SELECT day FROM days WHERE error IS NOT NULL')]
        for day in sorted(set(retry),reverse=True):
            if self.stop.is_set():return
            self.sync_day(day)
        if not self.stop.is_set():self.archive.set('last_recent_day',today.isoformat())
    def run(self):
        # A restart resumes the durable historical cursor and catches missed days.
        self.wake.set()
        while not self.stop.is_set():
            wait=max(0,self.archive.get('next_backup_epoch',0)-time.time())
            self.wake.wait(min(wait,60))
            if self.stop.is_set():return
            if not self.wake.is_set() and time.time()<self.archive.get('next_backup_epoch',0):continue
            self.wake.clear()
            with self.guard:self.running=True
            self.update(running=True,started=now(),error=None,phase='同步新增錄音')
            retry_seconds=None
            try:
                self.recent();last_recent=time.monotonic()
                cursor=dt.date.fromisoformat(self.archive.get('history_cursor',dt.date.today().isoformat()))
                while cursor>=self.floor and not self.stop.is_set():
                    self.update(phase='補抓全部歷史錄音',history_floor=self.floor.isoformat())
                    self.sync_day(cursor.isoformat())
                    if self.stop.is_set():break
                    cursor-=dt.timedelta(days=1);self.archive.set('history_cursor',cursor.isoformat())
                    if time.monotonic()-last_recent>=self.interval:
                        self.recent();last_recent=time.monotonic()
                    self.stop.wait(.03)
                if not self.stop.is_set():
                    failed=self.archive.stats()['failed']
                    fields=dict(history_done=not failed,history_scanned=True,phase='等待重試失敗錄音' if failed else '等待下次備份',current_record=None)
                    if not failed:fields['last_success']=now()
                    self.update(**fields)
            except Exception as e:
                if isinstance(e,(OSError,ProtocolError,DiscoveryError)):retry_seconds=min(60,self.interval)
                self.update(error=str(e),phase='暫時無法同步，等待自動重試' if retry_seconds else '備份中斷，下一輪重試')
            finally:
                due=time.time()+(retry_seconds or self.interval);self.archive.set('next_backup_epoch',due)
                with self.guard:self.running=False
                self.update(running=False,next_backup=dt.datetime.fromtimestamp(due).isoformat(timespec='seconds'))
