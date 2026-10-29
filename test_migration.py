import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from archive_migration import migrate
from backup import Archive,Backup,identity
from dmecom import recording_filename,normalize_record,normalize_phone
from test_features import sample

class NamingTests(unittest.TestCase):
 def test_zero_prefix_and_exact_new_name_preserve_raw(self):
  row=dict(timestamp='2026-10-29T03:04:05',PhoneNumber='912345678',RecType=2)
  self.assertEqual(recording_filename(row),'20261029-030405-0912345678-IN.wav')
  normalized=normalize_record(row);self.assertEqual(normalized['PhoneNumberRaw'],'912345678');self.assertEqual(normalized['PhoneNumber'],'0912345678')
  self.assertEqual(normalize_record(normalized),normalized)
  for value in ['0912345678','+886912345678','',None]:self.assertEqual(normalize_phone(value),str(value or ''))
 def test_offline_rename_database_paths_hashes_and_idempotence(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);archive=Archive(td);day='2026-10-01';row=archive.ingest(day,sample(day))[0]
   old='2026／10／01 00:00:00-123-IN.wav';relative=Path(day)/'1'/old;raw=b'unchanged original';pcm=b'RIFF'+bytes(100)
   for kind,data in [('original',raw),('wav',pcm),('pcm',pcm)]:
    path=root/kind/relative
    if kind=='original':path=path.with_suffix('.dar.wav')
    path.parent.mkdir(parents=True);path.write_bytes(data)
   (root/'wav'/'2026-10-01-1.wav').write_bytes(pcm)
   with archive.db() as db:db.execute('UPDATE recordings SET pcm=?,saved_version=version',(str(Path('pcm')/relative),))
   migrate(root);new=archive.cached(day,identity(row));self.assertEqual(new.name,'20261001-000000-0123-IN.wav');self.assertEqual(new.read_bytes(),pcm)
   self.assertEqual(next((root/'original').rglob('*.wav')).read_bytes(),raw)
   with archive.db() as db:r=json.loads(db.execute('SELECT row_json FROM recordings').fetchone()[0])
   self.assertEqual(r['PhoneNumberRaw'],'123');self.assertEqual(r['PhoneNumber'],'0123')
   migrate(root);self.assertEqual(archive.stats()['saved'],1)
 def test_interrupted_renames_resume_before_database_commit(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);archive=Archive(td);day='2026-10-01';row=archive.ingest(day,sample(day))[0];old='2026／10／01 00:00:00-123-IN.wav'
   for kind in ['wav','pcm']:
    p=root/kind/day/'1'/old;p.parent.mkdir(parents=True);p.write_bytes(b'RIFF'+bytes(100))
   with archive.db() as db:db.execute('UPDATE recordings SET pcm=?,saved_version=version',(str(Path('pcm')/day/'1'/old),))
   rename=Path.rename;calls=[]
   def interrupted(p,target):
    calls.append(p)
    if len(calls)==2:raise OSError('simulated interruption')
    return rename(p,target)
   with patch.object(Path,'rename',interrupted):
    with self.assertRaises(OSError):migrate(root)
   with archive.db() as db:self.assertIn('／',db.execute('SELECT pcm FROM recordings').fetchone()[0])
   migrate(root);self.assertTrue(archive.cached(day,identity(row)))
 def test_backup_interval_survives_recreation(self):
  with tempfile.TemporaryDirectory() as td:
   a=Archive(td);b=Backup(a,lambda day:b'',lambda row:None);b.set_interval(90)
   self.assertEqual(Backup(Archive(td),None,None).interval,5400)
   with self.assertRaises(ValueError):b.set_interval(0)
if __name__=='__main__':unittest.main()
