import copy
import json
from pathlib import Path
import struct
import tempfile
import unittest
from answer import AnswerSettings,night_profile,parse_weekly,KEYS
from dmecom import Client,ProtocolError

def week():
 raw=bytearray(4172)
 for day in range(7):
  off=day*596;struct.pack_into('<5I',raw,off,0,day==0,0,0,0)
  struct.pack_into('<4I',raw,off+20,20,1,11,0)
  name='old.wav'.encode('utf-16le');raw[off+84:off+84+len(name)]=name
 return bytes(raw)
class Recorder:
 def __init__(self):
  self.values={(line,key):week() if key==0x3a else struct.pack('<I',{0x2b:60,0x2c:1,0x2d:8}[key]) for line in range(4) for key in KEYS};self.writes=[];self.fail_at=None
 def channel_parameter(self,line,key):return self.values[line,key]
 def set_answer_parameter(self,line,key,data):
  self.writes.append((line,key))
  if self.fail_at==len(self.writes):raise ProtocolError('simulated interruption')
  self.values[line,key]=data
class AnswerTests(unittest.TestCase):
 def test_profile_is_every_day_cross_midnight_and_preserves_unused_slots(self):
  old=week();new=night_profile(old,'nas-example.wav')
  for day in parse_weekly(new):
   self.assertEqual(day['all_day'],0);self.assertEqual([s['enabled'] for s in day['slots']],[1,0,0,0])
   self.assertEqual(day['slots'][0],dict(enabled=1,start='20:00',end='11:00',file='nas-example.wav'))
  for day in range(7):self.assertEqual(old[day*596+212:(day+1)*596],new[day*596+212:(day+1)*596])
 def test_activate_readback_and_exact_rollback_preserves_rings(self):
  c=Recorder();before=copy.deepcopy(c.values)
  with tempfile.TemporaryDirectory() as td:
   service=AnswerSettings(td,lambda f:f(c));result=service.configure('nas-example.wav',lambda c:None)
   self.assertEqual(result['lines'],[1,2,3,4]);self.assertEqual(result['limit'],40)
   self.assertTrue(service.snapshot()['active']);self.assertFalse(service.snapshot()['pending'])
   self.assertTrue(all(k!=0x2d for l,k in c.writes));service.restore()
   self.assertEqual(c.values,before);self.assertNotIn('active',service.snapshot())
 def test_interrupted_write_remains_recoverable_after_service_restart(self):
  c=Recorder();before=copy.deepcopy(c.values);c.fail_at=3
  with tempfile.TemporaryDirectory() as td:
   service=AnswerSettings(td,lambda f:f(c))
   with self.assertRaises(ProtocolError):service.configure('nas-example.wav',lambda c:None)
   restarted=AnswerSettings(td,lambda f:f(c));self.assertTrue(restarted.snapshot()['pending'])
   with self.assertRaises(ProtocolError):restarted.configure('nas-other.wav',lambda c:None)
   c.fail_at=None;restarted.restore();self.assertEqual(c.values,before)
 def test_external_changes_block_restore_before_any_write(self):
  c=Recorder()
  with tempfile.TemporaryDirectory() as td:
   service=AnswerSettings(td,lambda f:f(c));service.configure('nas-example.wav',lambda c:None)
   c.values[3,0x2b]=struct.pack('<I',90);count=len(c.writes)
   with self.assertRaises(ProtocolError):service.restore()
   self.assertEqual(len(c.writes),count)
 def test_bad_baseline_and_unverified_voice_prevent_writes(self):
  c=Recorder();c.values[2,0x2b]=bytes(4)
  with tempfile.TemporaryDirectory() as td:
   service=AnswerSettings(td,lambda f:f(c))
   with self.assertRaises(ProtocolError):service.configure('nas-example.wav',lambda c:None)
   self.assertFalse(c.writes)
 def test_allowlist_rejects_unknown_settings_and_bad_weekly(self):
  class Socket:
   def sendall(self,data):pass
  c=Client({});c.sock=Socket()
  for key,value in [(0x2b,40),(0x2c,1)]:c.set_answer_parameter(0,key,struct.pack('<I',value))
  c.set_answer_parameter(3,0x3a,night_profile(week(),'nas-example.wav'))
  for key,value in [(0x2b,0),(0x2b,29),(0x2c,2),(0x2f,1)]:
   with self.assertRaises((ProtocolError,ValueError)):c.set_answer_parameter(0,key,struct.pack('<I',value))
  bad=bytearray(week());struct.pack_into('<I',bad,20,25)
  with self.assertRaises(ProtocolError):c.set_answer_parameter(0,0x3a,bytes(bad))
if __name__=='__main__':unittest.main()
