import datetime as dt
import io
import json
from pathlib import Path
import sqlite3
import struct
import tempfile
import unittest
import wave
from unittest.mock import patch
from backup import Archive,Backup,identity,RecordingConversionError
from dmecom import Client,ProtocolError,packet,standard_wav
from voice import Voices,ima_payload

def sample(day):
    with tempfile.TemporaryDirectory() as td:
        p=Path(td)/'i.db'
        with sqlite3.connect(p) as db:
            db.execute('CREATE TABLE CdrTable(AutoID INT,RecDate REAL,RecType INT,RecChannel INT,FileSize INT,PhoneNumber TEXT,RecFilePath TEXT,RecTime INT)')
            ole=(dt.date.fromisoformat(day)-dt.date(1899,12,30)).days
            db.execute('INSERT INTO CdrTable VALUES(1,?,2,0,2044,?, ?,1)',(ole,'123','\\Hard Disk\\Record\\'+day+'-I-1.wav'))
        return p.read_bytes()

def wav():
    b=io.BytesIO()
    with wave.open(b,'wb') as f:f.setnchannels(1);f.setsampwidth(2);f.setframerate(8000);f.writeframes(bytes(2000))
    return b.getvalue()

class BackupTests(unittest.TestCase):
    @patch('backup.shutil.disk_usage',return_value=__import__('shutil')._ntuple_diskusage(2**40,0,2**40))
    def test_dedup_version_and_local_download_after_device_disappears(self,_space):
        with tempfile.TemporaryDirectory() as td:
            a=Archive(td);day='2026-10-01';rows=a.ingest(day,sample(day));calls=[]
            p,new=a.save(day,rows[0],lambda r:(calls.append(1) or wav()))
            self.assertTrue(new);self.assertEqual(calls,[1]);self.assertFalse((Path(td)/'original').exists());self.assertFalse((Path(td)/'wav').exists())
            p2,new=a.save(day,rows[0],lambda r:self.fail('must not fetch twice'))
            self.assertFalse(new);self.assertEqual(p,p2);self.assertTrue(a.cached(day,identity(rows[0])))
            # Reopening archive preserves manifest; a changed device size is pending.
            a=Archive(td);self.assertEqual(a.stats()['saved'],1)
            row=rows[0];row['FileSize']+=1
            with a.db() as db:db.execute('UPDATE recordings SET version=?',('changed',))
            self.assertIsNone(a.cached(day,identity(row)))
    def test_historical_scan_crosses_empty_gaps_and_resumes(self):
        with tempfile.TemporaryDirectory() as td:
            a=Archive(td);today=dt.date.today();old=(today-dt.timedelta(days=3)).isoformat()
            a.set('history_cursor',(today-dt.timedelta(days=2)).isoformat());seen=[]
            b=Backup(a,lambda day:(seen.append(day) or b''),lambda row:self.fail(),{'history_start':old})
            original=b.update
            def update(**kw):
                original(**kw)
                if kw.get('history_done'):b.stop.set()
            b.update=update;b.run()
            self.assertIn(old,seen);self.assertNotIn((today-dt.timedelta(days=1)).isoformat(),seen[2:])
            self.assertTrue(b.status()['history_done']);self.assertEqual(a.get('history_cursor'),(today-dt.timedelta(days=4)).isoformat())
    @patch('backup.shutil.disk_usage',return_value=__import__('shutil')._ntuple_diskusage(2**40,0,2**40))
    def test_invalid_audio_is_not_saved_and_failure_is_isolated(self,_space):
        with tempfile.TemporaryDirectory() as td:
            a=Archive(td);day='2026-10-01';row=a.ingest(day,sample(day))[0]
            with self.assertRaises(RecordingConversionError):a.save(day,row,lambda r:b'bad audio')
            self.assertFalse((Path(td)/'original').exists());self.assertFalse((Path(td)/'wav').exists());self.assertIsNone(a.cached(day,identity(row)))
            with patch.object(a,'save',side_effect=RecordingConversionError('bad format')):
                b=Backup(a,lambda d:sample(d),lambda r:None);b.sync_day(day)
            self.assertEqual(a.stats()['failed'],1)
    def test_failure_keeps_history_cursor_for_retry(self):
        with tempfile.TemporaryDirectory() as td:
            a=Archive(td);today=dt.date.today().isoformat();a.set('history_cursor',today)
            b=Backup(a,lambda day:b'',lambda row:None,{'history_start':today})
            def fail(day):b.stop.set();raise OSError('offline')
            b.read_index=fail;b.run()
            self.assertEqual(a.get('history_cursor'),today);self.assertIn('offline',b.status()['error']);self.assertFalse(b.status().get('history_done',False))

class ProtocolTests(unittest.TestCase):
    def test_adpcm_reserved_counter_may_restart_and_raw_stays_unchanged(self):
        fmt=struct.pack('<HHIIHHHH',17,1,8000,4055,256,4,2,505)
        blocks=b''.join(struct.pack('<hBB',0,0,n)+bytes(252) for n in [0,1,0,127,255])
        body=b'WAVEfmt '+struct.pack('<I',len(fmt))+fmt+b'data'+struct.pack('<I',len(blocks))+blocks
        raw=b'RIFF'+struct.pack('<I',len(body))+body
        normalized=standard_wav(raw)
        start=normalized.index(b'data')+8
        self.assertEqual([normalized[start+i*256+3] for i in range(5)],[0]*5)
        self.assertEqual([raw[start+i*256+3] for i in range(5)],[0,1,0,127,255])
    def test_upload_metadata_chunking_and_no_firmware_or_delete(self):
        c=Client({});c.voices=lambda:[];sent=[];c.send=lambda cmd,sub,data:sent.append((cmd,sub,bytes(data)))
        replies=iter([packet(13,8,struct.pack('<I',128)),packet(13,9,b'\1')]);c.wait=lambda *args:next(replies)
        c.upload_voice('nas-0123456789abcdef.wav',bytes(100))
        self.assertEqual([(x[0],x[1]) for x in sent],[(13,7),(13,8),(13,9)])
        self.assertEqual(len(sent[0][2]),276);self.assertEqual(struct.unpack_from('<I',sent[0][2],256)[0],100)
        c=Client({})
        for pair in [(13,0),(13,13),(8,1)]:
            with self.assertRaises(ProtocolError):c.send(*pair,b'')
    def test_voice_stream_exact_block_ends_without_waiting_for_nonexistent_eof(self):
        c=Client({});sent=[];c.send=lambda *args:sent.append(args)
        responses=iter([packet(13,14,b'\1\0'),packet(13,15,bytes(4096))])
        c.wait=lambda *args:next(responses)
        self.assertEqual(c.voice_stream('nas-example.wav',4096),bytes(4096))
        self.assertEqual([(r[0],r[1]) for r in sent],[(13,14),(13,15),(13,16)])
    def test_missing_daily_index_does_not_stop_backup_or_accept_unknown_errors(self):
        c=Client({});sent=[];c.send=lambda *args:sent.append(args)
        c.wait=lambda *args:packet(12,20,b'\0\0')
        self.assertEqual(c.index('2026-10-29'),b'')
        self.assertEqual([(r[0],r[1]) for r in sent],[(12,17)])
        c.wait=lambda *args:packet(12,20,b'\1\0')
        with self.assertRaises(ProtocolError):c.index('2026-10-29')
    def test_old_apply_field_is_blocked(self):
        c=Client({})
        with self.assertRaises(ProtocolError):c.set_greeting(2,'nas-example.wav')
        with self.assertRaises(ProtocolError):c.send(8,1,b'\1\x2f\2'+bytes(73))
    def test_upload_refuses_existing_name(self):
        c=Client({});c.voices=lambda:[dict(name='nas-example.wav')]
        with self.assertRaises(ProtocolError):c.upload_voice('nas-example.wav',bytes(100))

class VoiceTests(unittest.TestCase):
    def test_conversion_and_staging_do_not_write_device(self):
        with tempfile.TemporaryDirectory() as td:
            v=Voices(td,lambda f:self.fail('staging must stay local'));m=v.stage(wav(),'test.wav')
            data=(v.path(m['key'])/'device.wav').read_bytes()
            self.assertEqual(struct.unpack_from('<HHI',data,20),(17,1,8000));self.assertFalse(m['uploaded']);self.assertGreater(m['duration'],0)
            with self.assertRaises(ValueError):v.prepare(m['key'],1)
            with self.assertRaises(ValueError):v.path('../config')

if __name__=='__main__':unittest.main()
