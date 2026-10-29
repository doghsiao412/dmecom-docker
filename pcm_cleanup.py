"""Verify/repair PCM copies before removing original/wav recording folders."""
import argparse
import datetime as dt
import json
from pathlib import Path
import shutil
from backup import Archive,pcm_info,convert_pcm
from dmecom import atomic_write

def cleanup(root):
    root=Path(root);archive=Archive(root);sources=[];recovered=[]
    folders=[root/'original',root/'wav',root/'migration-duplicates'/'original',root/'migration-duplicates'/'wav']
    for folder in folders:
        if folder.is_symlink():raise RuntimeError('拒絕清除連結目錄')
        if not folder.exists():continue
        kind=folder.name
        for source in sorted(folder.rglob('*.wav')):
            if '@eaDir' in source.parts:continue
            relative=source.relative_to(folder);name=relative.name
            if kind=='original' and name.endswith('.dar.wav'):relative=relative.with_name(name[:-8]+'.wav')
            counterpart=(root/'pcm' if folder.parent==root else root/'migration-duplicates'/'pcm')/relative
            sources.append((source,counterpart))
    # Readability is checked before any source directory is deleted.
    for source,counterpart in sources:
        if not counterpart.exists():
            info=convert_pcm(source.read_bytes(),counterpart);recovered.append(str(counterpart.relative_to(root)))
            print('Recovered PCM',len(recovered),flush=True)
    pcm_files=[];info_by_path={}
    for folder in [root/'pcm',root/'migration-duplicates'/'pcm']:
        if not folder.exists():continue
        for path in sorted(folder.rglob('*.wav')):
            if path.name.endswith('.partial.wav') or '@eaDir' in path.parts:continue
            info=pcm_info(path);relative=str(path.relative_to(root));info_by_path[relative]=info;pcm_files.append(dict(path=relative,**info))
            if len(pcm_files)%1000==0:print('Verified PCM',len(pcm_files),flush=True)
    for source,counterpart in sources:
        if str(counterpart.relative_to(root)) not in info_by_path:raise RuntimeError('來源錄音缺少可用的 PCM，停止清除')
    with archive.db() as db:
        rows=db.execute('SELECT day,key,pcm,version,saved_version FROM recordings').fetchall()
        for row in rows:
            if row['saved_version']==row['version'] and row['saved_version'] is not None:
                if row['pcm'] not in info_by_path:raise RuntimeError('已備份記錄缺少完整 PCM，停止清除')
            if row['pcm'] in info_by_path:
                info=info_by_path[row['pcm']];db.execute('UPDATE recordings SET pcm_hash=?,pcm_size=? WHERE day=? AND key=?',(info['sha256'],info['size'],row['day'],row['key']))
        db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('recording_storage',json.dumps('pcm_only')))
    removed_bytes=sum(path.stat().st_size for folder in folders if folder.exists() for path in folder.rglob('*') if path.is_file())
    audit=root/'migrations'/'pcm-only.json';report=dict(status='verified',checked=dt.datetime.now().isoformat(timespec='seconds'),files=pcm_files,recovered=recovered,removed_bytes=removed_bytes)
    atomic_write(audit,json.dumps(report,ensure_ascii=False).encode())
    for folder in folders:
        if folder.exists():shutil.rmtree(folder)
    report['status']='complete';atomic_write(audit,json.dumps(report,ensure_ascii=False).encode())
    print(json.dumps(dict(status='complete',pcm_files=len(pcm_files),recovered=len(recovered),removed_bytes=removed_bytes)),flush=True)
if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',required=True);args=parser.parse_args();cleanup(args.root)
