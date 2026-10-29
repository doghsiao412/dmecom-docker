"""Durable skip decisions, stale forms, worker isolation, and real HTTP routes."""
import importlib.util,json,os,sqlite3,tempfile,threading,unittest,urllib.error,urllib.parse,urllib.request
from pathlib import Path
from unittest.mock import patch,Mock
from http.server import ThreadingHTTPServer
from backup import Archive,Backup,identity,RecordingSkipped
from test_features import sample,wav
from retry_ui import render_retries

class RetryQueueTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.archive=Archive(self.temp.name);self.day='2026-10-01';self.row=self.archive.ingest(self.day,sample(self.day))[0];self.key=identity(self.row);self.archive.record_error(self.day,self.key,'設備結束此筆下載 <unsafe>')
 def tearDown(self):self.temp.cleanup()
 def item(self):
  r=self.archive.rows(self.day)[0];return [self.day,r['key'],r['version']]
 def test_skip_persists_across_reopen_and_ingest_without_losing_error(self):
  before=self.archive.rows(self.day)[0];self.archive.retry_action([self.item()],'skip');self.archive=Archive(self.temp.name);self.archive.ingest(self.day,sample(self.day))
  self.assertEqual(self.archive.stats()['failed'],0);self.assertEqual(self.archive.stats()['skipped'],1);self.assertEqual(self.archive.retry_queue()['total'],0)
  r=self.archive.retry_queue('skipped')['rows'][0];self.assertTrue(r['skipped']);self.assertEqual(r['error'],before['error']);self.assertEqual(r['RecFilePath'],before['RecFilePath']);self.assertTrue(r['skipped_at'])
  with self.assertRaises(RecordingSkipped):self.archive.save(self.day,self.row,lambda r:self.fail('skipped recording must never be fetched'))
 def test_restore_rejoins_queue_and_success_clears_error(self):
  item=self.item();self.archive.retry_action([item],'skip');self.archive.retry_action([item],'retry')
  self.assertEqual(self.archive.stats()['failed'],1);self.assertEqual(self.archive.stats()['skipped'],0)
  self.archive.save(self.day,self.row,lambda r:wav());self.assertEqual(self.archive.stats()['failed'],0);self.assertEqual(self.archive.retry_queue()['total'],0)
  with self.assertRaises(ValueError):self.archive.retry_action([item],'skip')
 def test_new_device_version_releases_skip_and_rejects_old_form(self):
  item=self.item();self.archive.retry_action([item],'skip')
  with self.archive.db() as db:db.execute("UPDATE recordings SET version='new-version'")
  self.assertFalse(self.archive.is_skipped(self.day,self.key));self.assertEqual(self.archive.stats()['failed'],1);self.assertEqual(self.archive.stats()['skipped'],0)
  with self.assertRaises(ValueError):self.archive.retry_action([item],'skip')
 def test_invalid_batch_rolls_back_all_changes(self):
  good=self.item()
  for items,op in [([], 'skip'),([good, ['2026-10-02','missing','v']], 'skip'),([good],'delete'),([[self.day]],'skip'),([['invalid','k','v']],'skip'),([good]*101,'skip')]:
   with self.assertRaises(ValueError):self.archive.retry_action(items,op)
   self.assertFalse(self.archive.is_skipped(self.day,self.key))
 def test_worker_never_fetches_skipped_on_index_refresh_and_can_resume(self):
  item=self.item();self.archive.retry_action([item],'skip');called=[]
  b=Backup(self.archive,lambda day:sample(day),lambda row:called.append(row) or wav());b.sync_day(self.day);self.assertEqual(called,[])
  self.archive.retry_action([item],'retry');b.sync_day(self.day);self.assertEqual(len(called),1);self.assertEqual(self.archive.stats()['saved'],1)
 def test_skipped_historical_day_is_excluded_from_recent_retry_scan(self):
  self.archive.retry_action([self.item()],'skip');called=[]
  b=Backup(self.archive,lambda day:called.append(day) or b'',lambda row:self.fail());b.recent();self.assertNotIn(self.day,called)
 def test_skip_after_worker_snapshot_is_respected_before_fetch(self):
  snapshot=self.archive.rows(self.day);self.archive.retry_action([self.item()],'skip')
  b=Backup(self.archive,lambda day:b'',lambda row:self.fail('stale worker snapshot fetched audio'))
  with patch.object(self.archive,'rows',return_value=snapshot):b.sync_day(self.day)
  self.assertEqual(self.archive.stats()['skipped'],1)
 def test_queue_pagination_validation_and_escaped_html(self):
  result=self.archive.retry_queue(page=99,page_size=1);self.assertEqual(result['page'],1)
  page=render_retries(result,self.archive.stats(),'nonce','<script>bad</script>')
  self.assertNotIn('<unsafe>',page);self.assertNotIn('<script>bad</script>',page);self.assertIn('全選本頁',page);self.assertIn('略過所選',page)
  for args in [('bad',1),('pending',0),('pending',1000001)]:
   with self.assertRaises(ValueError):self.archive.retry_queue(*args)
 def test_migrates_old_database_without_touching_existing_records(self):
  with tempfile.TemporaryDirectory() as td:
   with sqlite3.connect(Path(td)/'archive.sqlite') as db:
    db.execute('CREATE TABLE recordings(day TEXT,key TEXT,row_json TEXT,version TEXT,saved_version TEXT,pcm TEXT,raw_hash TEXT,error TEXT,PRIMARY KEY(day,key))');db.execute('INSERT INTO recordings VALUES(?,?,?,?,?,?,?,?)',(self.day,'old','{}','v',None,None,None,'original error'))
   a=Archive(td)
   with a.db() as db:
    row=db.execute('SELECT * FROM recordings').fetchone();self.assertEqual(row['error'],'original error');self.assertIsNone(row['skipped_version'])

class RetryHTTPTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.temp=tempfile.TemporaryDirectory();root=Path(cls.temp.name);cfg=root/'config.json';cfg.write_text(json.dumps(dict(username='test',password='test')))
  with patch.dict(os.environ,DMECOM_CONFIG=str(cfg),DMECOM_DATA=str(root/'data')):
   spec=importlib.util.spec_from_file_location('retry_test_server',Path(__file__).with_name('server.py'));cls.module=importlib.util.module_from_spec(spec);spec.loader.exec_module(cls.module)
  cls.http=ThreadingHTTPServer(('127.0.0.1',0),cls.module.Handler);cls.thread=threading.Thread(target=cls.http.serve_forever,daemon=True);cls.thread.start();cls.url='http://127.0.0.1:'+str(cls.http.server_port)
 @classmethod
 def tearDownClass(cls):cls.http.shutdown();cls.http.server_close();cls.thread.join(3);cls.temp.cleanup()
 def setUp(self):
  self.server=self.module
  with self.server.ARCHIVE.db() as db:db.execute('DELETE FROM recordings')
  self.row=self.server.ARCHIVE.ingest('2026-10-01',sample('2026-10-01'))[0];self.key=identity(self.row);self.server.ARCHIVE.record_error('2026-10-01',self.key,'test retry error');r=self.server.ARCHIVE.rows('2026-10-01')[0]
  self.item=json.dumps(['2026-10-01',self.key,r['version']]);self.server.BACKUP.wake.clear();self.server.SESSION=Mock()
 def request(self,path,args=None,headers=None):
  data=urllib.parse.urlencode(args,doseq=True).encode() if args is not None else None
  try:
   with urllib.request.urlopen(urllib.request.Request(self.url+path,data=data,headers=headers or {}),timeout=5) as r:return r.status,r.read()
  except urllib.error.HTTPError as e:return e.code,e.read()
 def test_count_links_list_actions_and_restore_without_device_calls(self):
  code,body=self.request('/');self.assertEqual(code,200);self.assertIn(b'aria-label="'+ '管理待重試錄音'.encode()+b'"',body)
  code,body=self.request('/backup/retries');self.assertEqual(code,200);self.assertIn(b'test retry error',body)
  args=dict(csrf=self.server.CSRF,item=[self.item],operation='skip',view='pending')
  self.assertEqual(self.request('/backup/retries/action',args)[0],200);self.assertEqual(self.server.ARCHIVE.stats()['failed'],0);self.assertEqual(self.server.ARCHIVE.stats()['skipped'],1)
  code,body=self.request('/backup/retries?view=skipped');self.assertIn('恢復重試'.encode(),body)
  args.update(operation='retry',view='skipped');self.assertEqual(self.request('/backup/retries/action',args)[0],200);self.assertTrue(self.server.BACKUP.wake.is_set());self.assertEqual(self.server.ARCHIVE.stats()['failed'],1)
  self.assertEqual(self.server.SESSION.mock_calls,[])
 def test_csrf_cross_origin_and_malformed_items_never_change_queue(self):
  for args,headers in [({'item':self.item,'operation':'skip'},{}),({'csrf':self.server.CSRF,'item':self.item,'operation':'skip'},{'Origin':'http://other.invalid'}),({'csrf':self.server.CSRF,'item':'not-json','operation':'skip'},{}),({'csrf':self.server.CSRF,'operation':'skip'}, {})]:
   self.assertEqual(self.request('/backup/retries/action',args,headers)[0],400);self.assertEqual(self.server.ARCHIVE.stats()['failed'],1);self.assertEqual(self.server.ARCHIVE.stats()['skipped'],0)
  self.assertEqual(self.server.SESSION.mock_calls,[])
 def test_bad_list_queries_reject_and_skipped_badge_in_recordings(self):
  for path in ('/backup/retries?view=bad','/backup/retries?page=0','/backup/retries?page=abc'):self.assertEqual(self.request(path)[0],400)
  self.server.ARCHIVE.retry_action([json.loads(self.item)],'skip');code,body=self.request('/?date=2026-10-01');self.assertEqual(code,200);self.assertIn('已略過備份'.encode(),body)

if __name__=='__main__':unittest.main()
