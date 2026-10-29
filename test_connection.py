import datetime as dt
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from connection_status import ConnectionStatus
from backup import Archive,Backup
from discovery import DiscoveryError
class ConnectionTests(unittest.TestCase):
 def test_failure_and_restart_keep_last_confirmed_success(self):
  with tempfile.TemporaryDirectory() as td:
   c=ConnectionStatus(td);c.success('192.0.2.86');last=c.snapshot()['last_success'];c.failure(DiscoveryError('not found'))
   self.assertEqual(c.snapshot()['status'],'unavailable');self.assertEqual(c.snapshot()['last_success'],last)
   restarted=ConnectionStatus(td);self.assertEqual(restarted.snapshot()['status'],'unknown');self.assertEqual(restarted.snapshot()['last_success'],last)
 def test_existing_settings_snapshot_seeds_last_read_time(self):
  with tempfile.TemporaryDirectory() as td:
   folder=Path(td)/'answer-settings';folder.mkdir();(folder/'snapshot.json').write_text(json.dumps({'checked':'2026-10-29T03:16:00'}))
   c=ConnectionStatus(td);self.assertEqual(c.snapshot()['last_success'],'2026-10-29T03:16:00')
 def test_status_save_error_does_not_interrupt_device_work(self):
  with tempfile.TemporaryDirectory() as td:
   with patch('connection_status.atomic_write',side_effect=OSError('unavailable status file')):
    c=ConnectionStatus(td);c.success();c.failure(OSError('offline'))
   self.assertEqual(c.snapshot()['status'],'unavailable')
 def test_network_failure_retries_soon_without_changing_hourly_setting(self):
  with tempfile.TemporaryDirectory() as td:
   a=Archive(td);b=Backup(a,None,None)
   def fail(day):b.stop.set();raise DiscoveryError('offline')
   b.read_index=fail;b.run()
   self.assertEqual(b.interval,3600);self.assertLessEqual(a.get('next_backup_epoch')-time.time(),61)
   self.assertIn('自動重試',b.status()['phase'])
 def test_recovery_resumes_and_restores_normal_interval(self):
  with tempfile.TemporaryDirectory() as td:
   a=Archive(td);b=Backup(a,None,None,{'history_start':dt.date.today().isoformat()});calls=[];original=b.update
   def read(day):
    calls.append(day)
    if len(calls)==1:raise OSError('offline')
    return b''
   def update(**kw):
    original(**kw)
    if kw.get('error'):b.wake.set()
    if kw.get('history_done'):b.stop.set()
   b.read_index=read;b.update=update;b.run()
   self.assertGreater(len(calls),1);self.assertIsNone(b.status()['error']);self.assertTrue(b.status()['history_done']);self.assertGreater(a.get('next_backup_epoch')-time.time(),3590)
if __name__=='__main__':unittest.main()
