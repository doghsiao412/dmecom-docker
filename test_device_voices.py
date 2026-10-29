import copy,datetime as dt,json
from pathlib import Path
import struct,tempfile,unittest
from unittest.mock import patch
from device_voices import DeviceVoices,references,wav_from_ima
from dmecom import Client,ProtocolError,packet,voice_filename
from voice import Voices

RAW=(struct.pack('<hBB',23,0,0)+bytes(252))*4
class Recorder:
 def __init__(self):self.files={'unused.wav':RAW};self.refs={};self.calls=[];self.bad_upload=False;self.lost_delete=False
 def voices(self):return [dict(name=k,size=len(v)+60) for k,v in self.files.items()]
 def pickup_announcement(self,line):return dict(enabled=0,file=self.refs.get(line,''))
 def channel_parameter(self,line,key):return bytes(4172)
 def read_voice(self,name,size,expected=None):return self.files[name]
 def upload_voice_named(self,name,audio):
  self.calls.append(('upload',name));pos=audio.index(b'data');size=struct.unpack_from('<I',audio,pos+4)[0];self.files[name]=audio[pos+8:pos+8+size]
  if self.bad_upload:self.files[name]=bytes(len(self.files[name]))
 def delete_voice(self,name):
  self.calls.append(('delete',name));del self.files[name]
  if self.lost_delete:raise OSError('lost acknowledgement')

class DeviceVoiceTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.c=Recorder();self.v=Voices(self.temp.name,lambda f:f(self.c));self.d=DeviceVoices(self.temp.name,lambda f:f(self.c),self.v)
 def test_prepare_only_backs_up_and_explicit_apply_deletes_idempotently(self):
  plan=self.d.prepare('delete','unused.wav');self.assertFalse(self.c.calls);self.assertIn('unused.wav',self.c.files)
  result=self.d.apply(plan['token']);self.assertEqual(result['status'],'completed');self.assertNotIn('unused.wav',self.c.files)
  self.assertEqual((self.d._path(plan['backup'])/'stream.bin').read_bytes(),RAW);self.d.apply(plan['token']);self.assertEqual(len(self.c.calls),1)
 def test_disabled_pickup_reference_blocks_delete_and_rename(self):
  self.c.refs[0]='unused.wav';self.assertTrue(references(self.c)['unused.wav'])
  for operation in ['delete','rename']:
   with self.assertRaises(ValueError):self.d.prepare(operation,'unused.wav','new.wav')
  self.assertFalse(self.c.calls)
 def test_new_reference_or_changed_same_size_content_prevents_apply(self):
  plan=self.d.prepare('delete','unused.wav');self.c.refs[3]='unused.wav'
  with self.assertRaises(ValueError):self.d.apply(plan['token'])
  self.c.refs.clear();self.c.files['unused.wav']=bytes(len(RAW))
  with self.assertRaises(ValueError):self.d.apply(plan['token'])
  self.assertFalse(self.c.calls)
 def test_rename_copies_verifies_then_deletes_without_changing_refs(self):
  before=copy.deepcopy(self.c.refs);plan=self.d.prepare('rename','unused.wav','下班.wav');self.d.apply(plan['token'])
  self.assertEqual(self.c.files,{'下班.wav':RAW});self.assertEqual(self.c.refs,before);self.assertEqual(self.c.calls,[('upload','下班.wav'),('delete','unused.wav')])
  self.assertEqual(self.d.snapshot()['files'][0]['backup']['stream_size'],len(RAW))
 def test_real_filename_rename_keeps_friendly_name_and_restore_alias(self):
  from voice_names import VoiceNames
  names=VoiceNames(self.temp.name);names.set('unused.wav','下班答錄');self.d.names=names
  plan=self.d.prepare('rename','unused.wav','new.wav');self.d.apply(plan['token'])
  self.assertEqual(names.all()['new.wav'],'下班答錄');self.assertEqual(names.all()['unused.wav'],'下班答錄')
 def test_bad_upload_preserves_original_and_interruption_can_resume(self):
  plan=self.d.prepare('rename','unused.wav','new.wav');self.c.bad_upload=True
  with self.assertRaises(ProtocolError):self.d.apply(plan['token'])
  self.assertIn('unused.wav',self.c.files);self.assertNotIn(('delete','unused.wav'),self.c.calls)
  self.c.bad_upload=False;del self.c.files['new.wav'];self.d.apply(plan['token']);self.assertNotIn('unused.wav',self.c.files)
 def test_lost_delete_ack_can_resume_after_service_restart(self):
  plan=self.d.prepare('rename','unused.wav','new.wav');self.c.lost_delete=True
  with self.assertRaises(OSError):self.d.apply(plan['token'])
  self.d=DeviceVoices(self.temp.name,lambda f:f(self.c),self.v);self.d.apply(plan['token']);self.assertEqual(self.c.files,{'new.wav':RAW})
 def test_collision_expiry_and_corrupt_backup_never_write(self):
  self.c.files['NEW.wav']=RAW
  with self.assertRaises(ValueError):self.d.prepare('rename','unused.wav','new.wav')
  plan=self.d.prepare('delete','unused.wav');plan['prepared']=(dt.datetime.now()-dt.timedelta(minutes=11)).isoformat();self.d._plan_save(plan)
  with self.assertRaises(ValueError):self.d.apply(plan['token'])
  plan=self.d.prepare('delete','unused.wav');(self.d._path(plan['backup'])/'stream.bin').write_bytes(b'bad')
  with self.assertRaises(ProtocolError):self.d.apply(plan['token'])
  self.assertFalse(self.c.calls)
 def test_backup_reads_must_match_and_restore_does_not_overwrite(self):
  original=self.c.read_voice;count=0
  def different(*args):
   nonlocal count
   count+=1;return RAW if count==1 else bytes(len(RAW))
  self.c.read_voice=different
  with self.assertRaises(ProtocolError):self.d.fetch('unused.wav')
  self.c.read_voice=original;b=self.d.fetch('unused.wav')
  with self.assertRaises(ValueError):self.d.restore(b['key'])
  del self.c.files['unused.wav'];self.d.restore(b['key']);self.assertEqual(self.c.files['unused.wav'],RAW)
 def test_partial_ima_block_is_preserved(self):
  raw=RAW+struct.pack('<hBB',0,0,0)+bytes(20);audio=wav_from_ima(raw);p=audio.index(b'data');self.assertEqual(audio[p+8:],raw)
  with self.assertRaises(ProtocolError):wav_from_ima(RAW+b'x')
 def test_partial_ima_backup_preview_and_rename_preserve_encoded_tail(self):
  raw=RAW+struct.pack('<hBB',23,0,0)+bytes(216);self.c.files['unused.wav']=raw
  backup=self.d.fetch('unused.wav');self.assertTrue((self.d._path(backup['key'])/'preview.wav').is_file())
  plan=self.d.prepare('rename','unused.wav','partial.wav');self.d.apply(plan['token']);self.assertEqual(self.c.files['partial.wav'],raw)
 def test_delete_packet_is_filename_only_and_unknown_commands_remain_blocked(self):
  class Socket:
   def __init__(self):self.sent=[]
   def sendall(self,data):self.sent.append(data)
  c=Client({});c.sock=Socket();c.wait=lambda *args:packet(13,13,b'\1');c.delete_voice('unused.wav')
  self.assertEqual(c.sock.sent[0],packet(13,13,'unused.wav'.encode('utf-16le')+bytes(236)))
  for name in ['../file.wav','name.txt','bad\\name.wav',' x.wav','nul\0.wav']:
   with self.assertRaises(ValueError):voice_filename(name)
  for data in [bytes(256),b'../'+bytes(253),b'']:
   with self.assertRaises(ProtocolError):c.send(13,13,data)
 def test_read_stream_stops_and_closes_without_extra_eof_request(self):
  c=Client({});sent=[];c.send=lambda *args:sent.append(args);responses=iter([packet(13,14,b'\1'),packet(13,15,bytes(4096))]);c.wait=lambda *args:next(responses)
  self.assertEqual(len(c.read_voice('nas-test.wav',4190)),4096);self.assertEqual([p[1] for p in sent],[14,15,16])
  responses=iter([packet(13,14,b'\1'),packet(13,15,RAW)]);c.wait=lambda *args:next(responses);self.assertEqual(c.read_voice('other.wav',len(RAW)+60),RAW)

if __name__=='__main__':unittest.main()
