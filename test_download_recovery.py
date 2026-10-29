"""Reproduce native download termination without reconnecting or mixing files."""
import datetime as dt
import socket,sqlite3,struct,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch
from dmecom import Client,RecordingUnavailable,ProtocolError,LoginBusy,packet
from backup import Archive,Backup,identity
from test_features import wav

STAMP=struct.pack('<8H',2026,10,4,29,21,0,0,0)
def clock_reply():return packet(8,0,b'\0\x19'+STAMP)
def read_packet(s):
 def exact(n):
  b=bytearray()
  while len(b)<n:
   chunk=s.recv(n-len(b))
   if not chunk:raise EOFError('peer closed')
   b.extend(chunk)
  return bytes(b)
 h=exact(2);return h+exact(struct.unpack('<H',h)[0])

class DownloadRecoveryTests(unittest.TestCase):
 def setUp(self):
  self.a,self.b=socket.socketpair();self.a.settimeout(2);self.b.settimeout(2);self.c=Client({});self.c.sock=self.a;self.c.download_end_grace=.02;self.peer_errors=[];self.threads=[]
 def tearDown(self):
  self.a.close();self.b.close()
  for thread in self.threads:thread.join(2)
  if self.peer_errors:raise self.peer_errors[0]
 def peer(self,fn):
  def run():
   try:fn()
   except BaseException as e:self.peer_errors.append(e)
  t=threading.Thread(target=run,daemon=True);self.threads.append(t);t.start()
 def expect(self,cmd,sub,payload=None):
  p=read_packet(self.b);self.assertEqual(p[2:4],bytes([cmd,sub]))
  if payload is not None:self.assertEqual(p[4:],payload)
  return p
 def test_late_close_reply_does_not_reject_a_valid_new_size(self):
  received=[];self.c.on_frame=lambda c,p:received.append(p[2:4])
  self.b.sendall(packet(12,9,b'\0\0')+packet(7,0,b'\0\0')+packet(12,10,struct.pack('<I',2044)))
  self.assertEqual(struct.unpack_from('<I',self.c.wait_audio(10),4)[0],2044)
  self.assertEqual(received,[bytes([12,9]),bytes([7,0]),bytes([12,10])])
 def test_reported_abort_is_drained_and_next_record_remains_correct(self):
  audio=wav()
  def device():
   self.b.sendall(packet(12,9,b'\0\0')+packet(12,8,b'\0\0'))
   self.expect(12,8,b'\0\0');self.expect(8,0,b'\0\x19')
   # An old size reply must be consumed by the clock barrier, not the next file.
   self.b.sendall(packet(12,10,struct.pack('<I',999999))+clock_reply())
   self.expect(12,6);self.b.sendall(packet(12,10,struct.pack('<I',len(audio))))
   self.expect(12,7,struct.pack('<I',len(audio)));self.b.sendall(packet(12,7,struct.pack('<I',len(audio))+audio))
   self.expect(12,8,b'\0\0');self.b.sendall(packet(12,9,b'\0\0')+packet(12,8,b'\0\0'))
   self.expect(8,0,b'\0\x19');self.b.sendall(clock_reply())
  self.peer(device)
  with self.assertRaisesRegex(RecordingUnavailable,'後續會重試'):self.c.wait_audio(10)
  result=self.c.audio(dict(RecFilePath='\\Record\\next.wav',FileSize=len(audio)))
  self.assertEqual(result,audio);self.assertEqual(self.c.clock(),dt.datetime(2026,10,29,21))
 def test_mid_download_abort_never_returns_partial_audio_or_double_cancels(self):
  def device():
   self.expect(12,7,struct.pack('<I',100));self.b.sendall(packet(12,9,b'\0\0'))
   self.expect(12,8,b'\0\0');self.expect(8,0,b'\0\x19');self.b.sendall(clock_reply())
  self.peer(device);self.c.audio_cancelled=False
  with self.assertRaises(RecordingUnavailable):self.c.transfer(100,7,8,1024)
  self.b.settimeout(.05)
  with self.assertRaises(socket.timeout):self.b.recv(1)
 def test_cancel_barrier_failure_stays_a_connection_error(self):
  def device():
   self.b.sendall(packet(12,9,b'\0\0'));self.expect(12,8);self.expect(8,0,b'\0\x19');self.b.sendall(packet(8,0,b'\0\x19'+bytes(16)))
  self.peer(device)
  with self.assertRaises(ProtocolError) as error:self.c.wait_audio(10)
  self.assertNotIsInstance(error.exception,RecordingUnavailable)
 def test_unknown_termination_payload_is_not_treated_as_unavailable_recording(self):
  self.b.sendall(packet(12,9,b'\1\0'))
  with self.assertRaises(ProtocolError) as error:self.c.wait_audio(10)
  self.assertNotIsInstance(error.exception,RecordingUnavailable)
 def test_busy_login_is_distinct_and_does_not_change_password(self):
  self.c.config=dict(username='test',password='test')
  def device():
   self.expect(1,0);self.b.sendall(packet(1,5,b'\0\0'));self.b.close()
  self.peer(device)
  with patch('dmecom.resolve_device',return_value='192.0.2.1'),patch('dmecom.socket.create_connection',return_value=self.a):
   with self.assertRaisesRegex(LoginBusy,'等待釋放'):self.c.__enter__()
  self.assertEqual(self.c.config['password'],'test')

class BackupUnavailableTests(unittest.TestCase):
 def test_one_unreadable_record_does_not_stop_the_day_or_history_cursor(self):
  with tempfile.TemporaryDirectory() as td:
   today=dt.date.today();day=today.isoformat();dbfile=Path(td)/'index.sqlite'
   with sqlite3.connect(dbfile) as db:
    db.execute('CREATE TABLE CdrTable(AutoID INT,RecDate REAL,RecType INT,RecChannel INT,FileSize INT,PhoneNumber TEXT,RecFilePath TEXT,RecTime INT)')
    ole=(today-dt.date(1899,12,30)).days
    for n in (1,2):db.execute('INSERT INTO CdrTable VALUES(?,?,?,?,?,?,?,?)',(n,ole+n/86400,2,0,2044,'123','\\Record\\%d.wav'%n,1))
   raw=dbfile.read_bytes();archive=Archive(Path(td)/'data');called=[]
   def fetch(row):
    called.append(row['AutoID'])
    if row['AutoID']==1:raise RecordingUnavailable('設備結束此筆下載，後續重試')
    return wav()
   backup=Backup(archive,lambda d:raw if d==day else b'',fetch,dict(history_start=day));original=backup.update
   def update(**fields):
    original(**fields)
    if fields.get('history_scanned'):backup.stop.set()
   backup.update=update;backup.run()
   self.assertIn(2,called);self.assertEqual(archive.stats()['saved'],1);self.assertEqual(archive.stats()['failed'],1)
   self.assertEqual(archive.get('history_cursor'),(today-dt.timedelta(days=1)).isoformat());self.assertIsNone(backup.status()['error'])
   rows=archive.rows(day);self.assertFalse(rows[0]['backed_up']);self.assertTrue(rows[1]['backed_up']);self.assertIn('重試',rows[0]['error'])
