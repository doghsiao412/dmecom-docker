"""Exercise real console routes against a simulated native session, never a device."""
import importlib.util,json,os,tempfile,threading,unittest,urllib.error,urllib.parse,urllib.request
from pathlib import Path
from unittest.mock import Mock
from http.server import ThreadingHTTPServer
from dmecom import ProtocolError
from live_monitor import LiveMonitor

class LiveHTTPTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.temp=tempfile.TemporaryDirectory();root=Path(cls.temp.name);cfg=root/'config.json';cfg.write_text(json.dumps(dict(username='test',password='test',device_mac='02:00:00:00:00:01')))
  old={key:os.environ.get(key) for key in ['DMECOM_CONFIG','DMECOM_DATA']}
  os.environ.update(DMECOM_CONFIG=str(cfg),DMECOM_DATA=str(root/'data'))
  try:
   spec=importlib.util.spec_from_file_location('monitor_test_server',Path(__file__).with_name('server.py'));cls.module=importlib.util.module_from_spec(spec);spec.loader.exec_module(cls.module)
  finally:
   for key,value in old.items():
    if value is None:os.environ.pop(key,None)
    else:os.environ[key]=value
  cls.http=ThreadingHTTPServer(('127.0.0.1',0),cls.module.Handler);cls.thread=threading.Thread(target=cls.http.serve_forever,daemon=True);cls.thread.start();cls.url='http://127.0.0.1:'+str(cls.http.server_port)
 @classmethod
 def tearDownClass(cls):cls.http.shutdown();cls.http.server_close();cls.thread.join(3);cls.temp.cleanup()
 def setUp(self):
  self.server=self.module;self.server.LIVE=LiveMonitor();self.device=Mock(address='192.0.2.1');self.device.__exit__=Mock();self.server.SESSION=self.device;self.server.CONNECTION.success(self.device.address)
 def request(self,path,args=None,headers=None):
  headers=headers or {}
  if args is not None:
   headers['Content-Type']='application/x-www-form-urlencoded';data=urllib.parse.urlencode(args).encode()
  else:data=None
  try:
   with urllib.request.urlopen(urllib.request.Request(self.url+path,data=data,headers=headers),timeout=4) as r:return r.status,r.read(),dict(r.headers)
  except urllib.error.HTTPError as e:return e.code,e.read(),dict(e.headers)
 def test_page_and_status_get_do_not_start_monitoring(self):
  code,body,headers=self.request('/live');self.assertEqual(code,200);self.assertIn('即時監聽'.encode(),body);self.assertEqual(body.count(b'data-listen='),4)
  code,body,_=self.request('/api/live/status');self.assertEqual(code,200);self.assertEqual(len(json.loads(body)['live']['lines']),4);self.device.live_start.assert_not_called();self.device.live_status.assert_not_called()
  self.assertEqual(self.request('/healthz')[1],b'ok')
 def test_csrf_cross_origin_and_invalid_line_never_send_native_control(self):
  for args,headers in [({'line':'2'},{}),({'csrf':self.server.CSRF,'line':'2'},{'Origin':'http://other.invalid'})]:
   self.assertEqual(self.request('/live/start',args,headers)[0],400)
  for line in ('0','5','-1','bad'):
   self.assertEqual(self.request('/live/start',{'csrf':self.server.CSRF,'line':line})[0],409)
  self.device.live_start.assert_not_called()
 def test_start_stop_token_and_native_failure_return_json(self):
  code,body,_=self.request('/live/start',{'csrf':self.server.CSRF,'line':'2'});self.assertEqual(code,200);data=json.loads(body);token=data['token'];self.device.live_start.assert_called_once_with(1)
  code,body,headers=self.request('/api/live/audio?token=invalid&after=0');self.assertEqual(code,409);self.assertIn('error',json.loads(body));self.assertEqual(headers['Cache-Control'],'no-store')
  self.assertEqual(self.request('/live/stop',{'csrf':self.server.CSRF,'token':'invalid'})[0],200);self.assertIn(token,self.server.LIVE.listeners)
  self.request('/live/stop',{'csrf':self.server.CSRF,'token':token});self.server.LIVE.tick(self.device);self.device.live_stop.assert_called_once()
  self.device.live_start.side_effect=ProtocolError('native rejected')
  code,body,_=self.request('/live/start',{'csrf':self.server.CSRF,'line':'3'});self.assertEqual(code,409);self.assertEqual(json.loads(body)['error'],'native rejected');self.assertIsNone(self.server.SESSION);self.assertIsNone(self.server.LIVE.line)
