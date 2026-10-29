import base64,io,json,random,socket,struct,subprocess,tempfile,threading,unittest
from unittest.mock import Mock
from dmecom import Client,ProtocolError,packet
from live_monitor import LiveMonitor,LiveError,decode_ima
from live_ui import render_live

class Clock:
 def __init__(self):self.now=100
 def __call__(self):return self.now
class Device:
 def __init__(self):self.started=[];self.stopped=0;self.next=0;self.requested=0;self.sent=[]
 def live_start(self,n):self.started.append(n)
 def live_stop(self):self.stopped+=1
 def live_next(self):self.next+=1
 def live_status(self):self.requested+=1
 def send(self,*args):self.sent.append(args)

def block(seed=123):
 rng=random.Random(seed)
 return struct.pack('<hBB',-2100,30,201)+bytes(rng.randrange(256) for _ in range(252))
def state(line=0,code=8,phone='0912345678'):
 b=bytearray(92);b[:4]=bytes([line,code,17,0]);struct.pack_into('<I',b,4,48);b[28:28+len(phone)*2]=phone.encode('utf-16le')
 return struct.pack('<HB',93,10)+b

class LiveTests(unittest.TestCase):
 def setUp(self):self.clock=Clock();self.live=LiveMonitor(self.clock);self.c=Device()
 def test_all_four_states_full_legacy_sparse_and_stale(self):
  for n in range(4):self.live.handle(self.c,state(n,6+n))
  self.assertEqual(self.live.snapshot()['lines'][0]['status'],'來電響鈴');self.assertEqual(self.live.snapshot()['lines'][0]['phone'],'0912345678')
  self.assertFalse(self.live.snapshot()['status_stale'])
  self.live.handle(self.c,struct.pack('<HB',11,11)+bytes([0])+struct.pack('<II',49,0)+bytes([3]))
  self.assertEqual(self.live.snapshot()['lines'][0]['status'],'待機');self.assertEqual(self.live.snapshot()['lines'][0]['phone'],'');self.assertEqual(self.live.snapshot()['lines'][0]['voltage'],49)
  full=struct.pack('<I',1)+bytes([12,17,7,0])+bytes(24)+bytes(64)
  self.live.handle(self.c,struct.pack('<HB',97,9)+full)
  self.assertEqual(self.live.snapshot()['lines'][1]['status'],'自動答錄')
  self.clock.now+=16;self.assertTrue(self.live.snapshot()['status_stale'])
  for p in [packet(10,0),state(9),packet(21,0,bytes(84))]:self.live.handle(self.c,p)
 def test_firmware_notification_73_preserves_full_state_and_voltage(self):
  self.live.handle(self.c,state(0,8));before=self.live.snapshot()['lines'][0].copy()
  self.clock.now+=1;self.live.handle(self.c,struct.pack('<HB',11,11)+bytes([0])+struct.pack('<II',123,0)+bytes([73]))
  self.assertEqual(self.live.snapshot()['lines'][0],before)
 def test_audio_arriving_before_start_ack_does_not_stop_new_session(self):
  def start(n):
   self.live.handle(self.c,packet(15,3,bytes([n])+block()))
   self.assertEqual(self.live.phase,'starting');self.assertFalse(self.c.sent)
  self.c.live_start=start
  result=self.live.start(self.c,2);self.assertIn(result['token'],self.live.listeners);self.assertEqual(self.live.phase,'listening');self.assertEqual(self.c.next,1)
 def test_shared_same_line_limits_and_stop_only_after_last_viewer(self):
  a=self.live.start(self.c,2);b=self.live.start(self.c,2)
  self.assertEqual(self.c.started,[1]);self.assertEqual(self.live.snapshot()['listeners'],2)
  with self.assertRaises(LiveError):self.live.start(self.c,1)
  for _ in range(6):self.live.start(self.c,2)
  with self.assertRaises(LiveError):self.live.start(self.c,2)
  for token in list(self.live.listeners):self.live.detach(token)
  self.live.tick(self.c);self.assertEqual(self.c.stopped,1);self.assertIsNone(self.live.snapshot()['line'])
 def test_fragmented_blocks_stream_next_and_bounded_slow_client(self):
  a=self.live.start(self.c,3);data=block()
  self.live.handle(self.c,packet(15,3,bytes([2])+data[:57]));self.assertEqual(self.live.sequence,0)
  self.live.handle(self.c,packet(15,3,bytes([2])+data[57:]));self.assertEqual(self.c.next,2)
  chunk=self.live.chunks(a['token'],0,0)['chunks'][0]
  self.assertEqual(base64.b64decode(chunk['pcm']),decode_ima(data))
  self.live.handle(self.c,packet(15,3,b'\0'+data));self.assertEqual(self.live.sequence,1)
  for _ in range(80):self.live.handle(self.c,packet(15,3,bytes([2])+data))
  self.assertEqual(len(self.live.audio),40);recent=self.live.chunks(a['token'],0,0)['chunks'];self.assertEqual(len(recent),4);self.assertEqual(recent[-1]['sequence'],81)
 def test_expiration_does_not_resurrect_token_and_works_inside_backup_receive(self):
  a=self.live.start(self.c,4);self.clock.now+=13
  with self.assertRaises(LiveError):self.live.chunks(a['token'],0,0)
  self.live.handle(self.c,packet(15,3,b'\3'+block()));self.assertEqual(self.c.sent,[(15,2,bytes(5))]);self.assertEqual(self.c.next,0)
  self.live.handle(self.c,packet(15,2,b'\1\3'));self.assertIsNone(self.live.line)
 def test_stop_ack_timeout_requests_session_cleanup(self):
  self.live.start(self.c,2);self.clock.now+=13;self.live.handle(self.c,packet(15,3,b'\1'+block()));self.clock.now+=5
  with self.assertRaises(ProtocolError):self.live.tick(self.c)
 def test_error_survives_cleanup_and_reaches_browser(self):
  a=self.live.start(self.c,2);self.live.handle(self.c,packet(15,5,b'\1\x20'+bytes(32)));self.live.tick(self.c)
  with self.assertRaisesRegex(LiveError,'另一種音訊格式'):self.live.chunks(a['token'],0,0)
 def test_decode_failure_isolated_and_unknown_format_stops(self):
  a=self.live.start(self.c,1)
  self.live.handle(self.c,packet(15,3,b'\0'+b'\0\0\xff\0'+bytes(252)))
  self.assertEqual(self.live.phase,'error');self.live.tick(self.c);self.assertEqual(self.c.stopped,1)
  self.live.start(self.c,1);self.live.handle(self.c,packet(15,5,b'\0\x20'+bytes(32)))
  self.assertEqual(self.live.phase,'error');self.assertEqual(self.c.next,0)
 def test_disconnect_wakes_readers_and_resets_without_reconnect_to_audio(self):
  a=self.live.start(self.c,1);self.live.disconnected()
  with self.assertRaises(LiveError):self.live.chunks(a['token'],0,0)
  self.assertFalse(self.live.audio);self.assertIsNone(self.live.line);self.assertEqual(self.live.snapshot()['phase'],'error')
 def test_status_poll_is_read_only_throttled_and_passive_when_page_closed(self):
  self.live.tick(self.c);self.assertEqual(self.c.requested,0)
  self.live.watch();self.live.tick(self.c);self.live.tick(self.c);self.assertEqual(self.c.requested,1)
  self.clock.now+=6;self.live.tick(self.c);self.assertEqual(self.c.requested,2)
  self.clock.now+=21;self.live.tick(self.c);self.assertEqual(self.c.requested,2)
 def test_ima_native_rounding_and_ffmpeg_duration_ignore_reserved_byte(self):
  data=block();fmt=struct.pack('<HHIIHHHH',17,1,8000,4055,256,4,2,505)
  clean=bytearray(data);clean[3]=0
  body=b'WAVEfmt '+struct.pack('<I',len(fmt))+fmt+b'data'+struct.pack('<I',len(clean))+clean
  result=subprocess.run(['ffmpeg','-v','error','-i','pipe:0','-f','s16le','pipe:1'],input=b'RIFF'+struct.pack('<I',len(body))+body,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True)
  self.assertEqual(len(result.stdout),1010)
  native=struct.unpack('<505h',decode_ima(data));reference=struct.unpack('<505h',result.stdout)
  # Native 0x54a548–0x54a572 sums separately truncated shifts. FFmpeg uses
  # multiplication, so a few least-significant bits differ for this fixture.
  self.assertEqual(native[:8],(-2100,-2181,-2138,-2177,-2189,-2288,-2222,-2210))
  self.assertLess(max(abs(a-b) for a,b in zip(native,reference)),16)
  self.assertEqual(decode_ima(data),decode_ima(bytes(clean)))
  with self.assertRaises(LiveError):decode_ima(bytes(255))
 def test_allowlist_and_callback_preserve_interleaved_backup_reply(self):
  a,b=socket.socketpair();self.addCleanup(a.close);self.addCleanup(b.close)
  client=Client({});client.sock=a;client.on_frame=self.live.handle
  started=self.live.start(self.c,1)
  payload=state()+packet(15,3,b'\0'+block())+packet(12,18,struct.pack('<I',4)+b'data')
  for part in (payload[:1],payload[1:9],payload[9:]):b.sendall(part)
  reply=client.wait(12,18);self.assertEqual(reply[8:],b'data');self.assertEqual(self.live.sequence,1);self.assertEqual(self.live.snapshot()['lines'][0]['status'],'來電通話')
  self.assertEqual(b.recv(100),packet(15,4,bytes(5)))
  for cmd,sub,body in [(15,1,b'\4'+bytes(5)),(15,2,b'\1'),(15,4,bytes(6)),(15,6,bytes(5)),(10,1,bytes(5)),(10,0,bytes(4))]:
   with self.assertRaises(ProtocolError):client.send(cmd,sub,body)
 def test_native_control_ack_and_source_ui(self):
  c=Client({});c.sock=Mock();c.wait=Mock(return_value=packet(15,1,b'\1\2'));c.live_start(2)
  self.assertEqual(c.sock.sendall.call_args[0][0],packet(15,1,b'\2'+bytes(5)))
  c.wait.return_value=packet(15,2,b'\1\2');c.live_stop()
  c.wait.return_value=packet(15,1,b'\0\2')
  with self.assertRaises(ProtocolError):c.live_start(2)
  with self.assertRaises(ValueError):c.live_start(True)
  page=render_live('test-token');self.assertEqual(page.count('data-listen='),4);self.assertIn('sendBeacon',page);self.assertIn('test-token',page)
