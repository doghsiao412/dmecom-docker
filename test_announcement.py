import copy
import datetime as dt
from pathlib import Path
import struct
import tempfile
import unittest
from announcement import PickupAnnouncements,revision
from announcement_ui import render_announcement,render_confirmation
from dmecom import Client,ProtocolError
class Recorder:
 def __init__(self):
  self.values={str(n):dict(enabled=1,file='old.wav') for n in range(1,5)};self.writes=[];self.fail_at=None;self.night=dict(start='20:00',end='11:00',limit=40)
 def pickup_announcement(self,line):return dict(self.values[str(line+1)])
 def voices(self):return [dict(name='old.wav'),dict(name='new.wav')]
 def set_pickup_announcement(self,line,field,value):
  self.writes.append((line,field,value))
  if self.fail_at==len(self.writes):raise ProtocolError('interrupted')
  self.values[str(line+1)][field]=value
class AnnouncementTests(unittest.TestCase):
 def setup_service(self,root):
  c=Recorder();s=PickupAnnouncements(root,lambda f:f(c));s.inspect();return c,s
 def prepare(self,s,lines=[1],file='new.wav',enabled=None):
  return s.prepare(lines,file,enabled,{str(r['line']):r['revision'] for r in s.snapshot()['lines']})
 def test_select_lines_replace_only_notice_and_restore(self):
  with tempfile.TemporaryDirectory() as td:
   c,s=self.setup_service(td);before=copy.deepcopy(c.values);night=dict(c.night);plan=self.prepare(s,[1,3]);self.assertFalse(c.writes)
   s.apply(plan['token']);self.assertEqual(c.values['1']['file'],'new.wav');self.assertEqual(c.values['3']['file'],'new.wav')
   self.assertEqual(c.values['2'],before['2']);self.assertEqual(c.values['4'],before['4']);self.assertEqual(c.night,night)
   count=len(c.writes);s.apply(plan['token']);self.assertEqual(len(c.writes),count)
   s.restore();self.assertEqual(c.values,before)
 def test_stale_and_expired_confirmation_write_nothing(self):
  with tempfile.TemporaryDirectory() as td:
   c,s=self.setup_service(td);plan=self.prepare(s);c.values['1']['enabled']=0
   with self.assertRaises(ValueError):s.apply(plan['token'])
   self.assertFalse(c.writes);c.values['1']['enabled']=1;plan['prepared']=(dt.datetime.now()-dt.timedelta(hours=1)).isoformat();s.save(plan)
   with self.assertRaises(ValueError):s.apply(plan['token'])
   self.assertFalse(c.writes)
 def test_partial_write_can_be_restored_after_restart(self):
  with tempfile.TemporaryDirectory() as td:
   c,s=self.setup_service(td);c.values['1']['enabled']=0;s.inspect();before=copy.deepcopy(c.values);plan=self.prepare(s,enabled=1);c.fail_at=2
   with self.assertRaises(ProtocolError):s.apply(plan['token'])
   restarted=PickupAnnouncements(td,lambda fn:fn(c));self.assertTrue(restarted.snapshot()['pending']);c.fail_at=None;restarted.restore();self.assertEqual(c.values,before)
 def test_disable_precedes_file_write_and_restore_does_not_overwrite_foreign_flag(self):
  with tempfile.TemporaryDirectory() as td:
   c,s=self.setup_service(td);plan=self.prepare(s,enabled=0);s.apply(plan['token']);self.assertEqual(c.writes[0][1],'enabled')
   s.restore();s.inspect();plan=self.prepare(s);s.apply(plan['token']);c.values['1']['enabled']=0;s.restore();self.assertEqual(c.values['1']['enabled'],0)
 def test_missing_file_and_unselected_lines_are_rejected(self):
  with tempfile.TemporaryDirectory() as td:
   c,s=self.setup_service(td)
   with self.assertRaises(ValueError):self.prepare(s,[],file='new.wav')
   with self.assertRaises(ValueError):self.prepare(s,file='missing.wav')
   self.assertFalse(c.writes)
 def test_switch_changes_only_selected_flag_and_stale_request_writes_nothing(self):
  with tempfile.TemporaryDirectory() as td:
   c,s=self.setup_service(td);before=copy.deepcopy(c.values);rev=s.snapshot()['lines'][1]['revision']
   plan=s.prepare([2],None,0,{'2':rev});s.apply(plan['token'])
   self.assertEqual(c.writes,[(1,'enabled',0)]);self.assertEqual(c.values['2']['file'],before['2']['file'])
   for line in ['1','3','4']:self.assertEqual(c.values[line],before[line])
   with self.assertRaises(ValueError):s.prepare([2],None,1,{'2':rev})
   self.assertEqual(len(c.writes),1);s.restore();self.assertEqual(c.values,before)
 def test_switch_requires_file_for_enable_but_can_disable_without_one(self):
  with tempfile.TemporaryDirectory() as td:
   c,s=self.setup_service(td);c.values['4']=dict(enabled=0,file='');s.inspect();rev=s.snapshot()['lines'][3]['revision']
   with self.assertRaises(ValueError):s.prepare([4],None,1,{'4':rev})
   plan=s.prepare([4],None,0,{'4':rev});self.assertEqual(s.apply(plan['token'])['status'],'no_change');self.assertFalse(c.writes)
 def test_native_packet_allowlist_and_decoder(self):
  class Socket:
   sent=[]
   def sendall(self,data):self.sent.append(data)
  c=Client({});c.sock=Socket();c.set_pickup_announcement(2,'file','new.wav');frame=c.sock.sent[-1]
  self.assertEqual(len(frame),80);self.assertEqual(frame[2:7],b'\x08\x01\x01\x11\x02');self.assertEqual(frame[-9:],bytes(9))
  c.set_pickup_announcement(0,'enabled',1);self.assertEqual(c.sock.sent[-1][4:],b'\x01\x06\0'+struct.pack('<I',1))
  with self.assertRaises(ProtocolError):c.send(8,1,b'\x01\x06\0'+struct.pack('<I',2))
  with self.assertRaises(ValueError):c.set_pickup_announcement(0,'file','../bad.wav')
  c.channel_parameter=lambda line,key:struct.pack('<I',1) if key==6 else 'old.wav'.encode('utf-16le')+bytes(50)
  self.assertEqual(c.pickup_announcement(0),dict(enabled=1,file='old.wav'))
  c.channel_parameter=lambda line,key:struct.pack('<I',1) if key==6 else 'old.wav'.encode('utf-16le')+bytes(59)
  self.assertEqual(c.pickup_announcement(0),dict(enabled=1,file='old.wav'))
 def test_no_change_confirm_and_html(self):
  with tempfile.TemporaryDirectory() as td:
   c,s=self.setup_service(td);plan=self.prepare(s,file=None);self.assertEqual(s.apply(plan['token'])['status'],'no_change');self.assertFalse(c.writes)
   body=render_announcement(s.snapshot(),c.voices(),[],'nonce');self.assertIn('<h2>接聽宣告</h2>',body);self.assertEqual(body.count('type="checkbox"'),4)
   self.assertIn('確認套用接聽宣告',render_confirmation(plan,'nonce'))
if __name__=='__main__':unittest.main()
