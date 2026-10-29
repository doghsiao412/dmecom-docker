import io,tempfile,unittest
from pathlib import Path
from media import byte_range,serve_wav
class Handler:
 def __init__(self,headers=None):self.headers=headers or {};self.response_headers={};self.status=None;self.wfile=io.BytesIO()
 def send_response(self,status):self.status=status
 def send_header(self,name,value):self.response_headers[name]=value
 def end_headers(self):pass
class MediaTests(unittest.TestCase):
 def test_range_bounds_suffixes_and_invalid_requests(self):
  self.assertEqual(byte_range('bytes=0-1',100),(0,1));self.assertEqual(byte_range('bytes=30-',100),(30,99));self.assertEqual(byte_range('bytes=-12',100),(88,99));self.assertEqual(byte_range('bytes=-500',100),(0,99));self.assertEqual(byte_range('bytes=99-999',100),(99,99))
  for value in ['bytes=100-','bytes=4-3','bytes=-0','bytes=-','bytes=0-1,3-4','bad','bytes='+'9'*201+'-']:
   with self.assertRaises(ValueError):byte_range(value,100)
 def test_partial_head_and_full_response_are_byte_exact(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)/'test.wav';data=bytes(range(256))*700;p.write_bytes(data)
   h=Handler();serve_wav(h,p);self.assertEqual(h.status,200);self.assertEqual(h.wfile.getvalue(),data);self.assertEqual(h.response_headers['Accept-Ranges'],'bytes')
   h=Handler({'Range':'bytes=65530-65545'});serve_wav(h,p);self.assertEqual(h.status,206);self.assertEqual(h.wfile.getvalue(),data[65530:65546]);self.assertEqual(h.response_headers['Content-Range'],f'bytes 65530-65545/{len(data)}');self.assertEqual(h.response_headers['Content-Length'],'16')
   h=Handler({'Range':'bytes=-8'});serve_wav(h,p,head=True);self.assertEqual(h.status,200);self.assertEqual(h.wfile.getvalue(),b'');self.assertEqual(h.response_headers['Content-Length'],str(len(data)))
   h=Handler({'Range':'bytes=999999-'});serve_wav(h,p);self.assertEqual(h.status,416);self.assertEqual(h.response_headers['Content-Range'],f'bytes */{len(data)}');self.assertEqual(h.wfile.getvalue(),b'')
 def test_if_range_falls_back_to_full_and_attachment_keeps_utf8_name(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)/'test.wav';p.write_bytes(b'12345');h=Handler({'Range':'bytes=0-1','If-Range':'old'});serve_wav(h,p,'上班宣告.wav');self.assertEqual(h.status,200);self.assertEqual(h.wfile.getvalue(),b'12345');self.assertIn("filename*=UTF-8''",h.response_headers['Content-Disposition']);self.assertIn('%E4%B8%8A',h.response_headers['Content-Disposition'])
