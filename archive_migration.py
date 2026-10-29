"""Offline, resumable filename/phone migration. Stop the backup server first."""
import argparse
from collections import defaultdict
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from dmecom import atomic_write,normalize_record,recording_filename

def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for data in iter(lambda:stream.read(1024*1024),b''):h.update(data)
    return h.hexdigest()

def legacy_name(name):
    suffix='.dar.wav' if name.endswith('.dar.wav') else '.wav'
    base=name[:-len(suffix)]
    match=re.fullmatch(r'(\d{4})／(\d{2})／(\d{2}) (\d{2}):(\d{2}):(\d{2})-(.*)-(IN|OUT|UNKNOWN)',base)
    if not match:return None
    y,m,d,h,minute,second,phone,direction=match.groups()
    row=dict(timestamp=f'{y}-{m}-{d}T{h}:{minute}:{second}',PhoneNumber='' if phone=='未知電話' else phone,RecType={'IN':2,'OUT':1,'UNKNOWN':0}[direction])
    return recording_filename(row)[:-4]+suffix

def migrate(root):
    root=Path(root);folder=root/'migrations';folder.mkdir(exist_ok=True)
    manifest=folder/'filename-v2.json';database=root/'archive.sqlite'
    db=sqlite3.connect(database);db.row_factory=sqlite3.Row
    if db.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise RuntimeError('資料庫完整性檢查失敗')
    if manifest.exists():plan=json.loads(manifest.read_text())
    else:
        rows=list(db.execute('SELECT day,key,row_json,pcm,saved_version,version FROM recordings'));lookup=defaultdict(list);updates=[]
        for item in rows:
            row=normalize_record(json.loads(item['row_json']));lookup[(item['day'],str(row['AutoID']))].append(row)
            updates.append(dict(day=item['day'],key=item['key'],row_json=json.dumps(row,ensure_ascii=False),pcm=item['pcm']))
        renames=[];used={};unmatched=0
        for kind in ('original','wav','pcm'):
            for source in sorted((root/kind).rglob('*.wav')):
                if source.name.endswith('.partial.wav'):continue
                relative=source.relative_to(root);parts=relative.parts;candidates=lookup.get((parts[1],parts[2]),[]) if len(parts)>=4 else []
                if any(part in {'@eaDir','#recycle'} for part in parts):continue
                legacy_id=re.fullmatch(r'(\d{4}-\d{2}-\d{2})-(\d+)(?:\.dar)?\.wav',source.name)
                if not candidates and legacy_id:candidates=lookup.get(legacy_id.groups(),[])
                if len(candidates)>1:raise RuntimeError('同一日期／ID 有多筆記錄，停止遷移以避免名稱衝突')
                if candidates:
                    name=recording_filename(candidates[0]);name=name[:-4]+'.dar.wav' if kind=='original' else name
                else:
                    name=legacy_name(source.name)
                    if name is None:
                        # A previously migrated unindexed recording is already valid.
                        if re.fullmatch(r'\d{8}-\d{6}-.+-(?:IN|OUT|UNKNOWN)(?:\.dar)?\.wav',source.name):name=source.name
                        else:unmatched+=1;continue
                target=relative.with_name(name);sha=digest(source);size=source.stat().st_size
                # Keep duplicate/older versions in separate directories, with the requested basename.
                if (str(target) in used and used[str(target)]!=str(relative)) or ((root/target).exists() and target!=relative):
                    target=Path('migration-duplicates')/kind/Path(*relative.parts[1:-1])/sha[:12]/name
                    if str(target) in used:target=target.parent/hashlib.sha256(str(relative).encode()).hexdigest()[:8]/name
                used[str(target)]=str(relative)
                renames.append(dict(old=str(relative),new=str(target),sha256=sha,size=size))
                if len(renames)%1000==0:print('Hashed',len(renames),flush=True)
        if unmatched:raise RuntimeError(str(unmatched)+' 個舊錄音檔無法對應記錄或解析檔名，尚未更動任何檔案')
        mapping={entry['old']:entry['new'] for entry in renames}
        for item in updates:
            if item['pcm']:
                if item['pcm'] not in mapping:raise RuntimeError('資料庫的播放檔路徑沒有對應檔案，停止遷移')
                item['pcm']=mapping[item['pcm']]
        with sqlite3.connect(folder/'archive-before-filename-v2.sqlite') as backup:db.backup(backup)
        plan=dict(status='renaming',created=dt.datetime.now().isoformat(timespec='seconds'),files=renames,rows=updates)
        atomic_write(manifest,json.dumps(plan,ensure_ascii=False).encode())
    if plan['status']=='complete':
        print(json.dumps(dict(status='already_complete',files=len(plan['files']),rows=len(plan['rows']))));return
    count=0
    for entry in plan['files']:
        old=root/entry['old'];new=root/entry['new']
        if old==new:
            if old.stat().st_size!=entry['size']:raise RuntimeError('錄音檔大小與遷移計畫不一致')
        elif old.exists():
            if new.exists():raise RuntimeError('目標檔案已存在，停止避免覆寫')
            if old.stat().st_size!=entry['size']:raise RuntimeError('來源檔案已變更，停止遷移')
            new.parent.mkdir(parents=True,exist_ok=True);old.rename(new)
        else:
            if not new.exists() or new.stat().st_size!=entry['size'] or digest(new)!=entry['sha256']:raise RuntimeError('中斷續傳檔案校驗失敗')
        count+=1
        if count%1000==0:print('Renamed',count,flush=True)
    # Commit path and caller-ID changes together, only after all renames succeed.
    with db:
        for item in plan['rows']:db.execute('UPDATE recordings SET row_json=?,pcm=? WHERE day=? AND key=?',(item['row_json'],item['pcm'],item['day'],item['key']))
        db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('filename_schema',json.dumps(2)))
    if db.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise RuntimeError('遷移後資料庫完整性檢查失敗')
    for entry in plan['files']:
        path=root/entry['new']
        if not path.is_file() or path.stat().st_size!=entry['size']:raise RuntimeError('遷移後檔案清單不一致')
    plan.update(status='complete',completed=dt.datetime.now().isoformat(timespec='seconds'))
    atomic_write(manifest,json.dumps(plan,ensure_ascii=False).encode())
    print(json.dumps(dict(status='complete',files=len(plan['files']),rows=len(plan['rows']),bytes=sum(x['size'] for x in plan['files']))),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',required=True);args=parser.parse_args();migrate(args.root)
