import datetime as dt
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
from dmecom import Client
from device_clock import DeviceClock,TAIPEI
class ClockTests(unittest.TestCase):
 def test_systemtime_codec_and_packet(self):
  c=Client({});value=dt.datetime(2026,10,29,0,10,30,120000)
  raw=struct.pack('<8H',2026,10,4,29,0,10,30,120);c.global_parameter=lambda k:raw
  self.assertEqual(c.clock(),value)
  sent=[];c.send=lambda *args:sent.append(args);c.set_clock(value)
  self.assertEqual(sent,[(8,1,b'\0\x19'+raw)])
 def test_sync_reads_verifies_and_audits(self):
  now=dt.datetime(2026,10,29,0,10,tzinfo=TAIPEI)
  class Recorder:
   time=now.replace(tzinfo=None)-dt.timedelta(hours=2)
   def clock(self):return self.time
   def set_clock(self,value):self.time=value
  with tempfile.TemporaryDirectory() as td:
   c=Recorder();service=DeviceClock(td,lambda fn:fn(c))
   with patch('device_clock.local_now',return_value=now):result=service.sync()
   self.assertEqual(result['difference_before_seconds'],-7200);self.assertEqual(result['difference_seconds'],0)
   self.assertTrue(list(Path(td).glob('clock-change-*.json')))
 def test_correct_clock_is_not_written(self):
  now=dt.datetime(2026,10,29,0,10,tzinfo=TAIPEI)
  class Recorder:
   def clock(self):return now.replace(tzinfo=None)
   def set_clock(self,v):raise AssertionError('unnecessary write')
  with tempfile.TemporaryDirectory() as td:
   service=DeviceClock(td,lambda fn:fn(Recorder()))
   with patch('device_clock.local_now',return_value=now):self.assertFalse(service.sync()['last_sync_changed'])
if __name__=='__main__':unittest.main()
