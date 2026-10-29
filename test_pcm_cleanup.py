import json
from pathlib import Path
import tempfile
import unittest
from backup import Archive,RecordingConversionError,pcm_info
from pcm_cleanup import cleanup
from test_features import wav
class CleanupTests(unittest.TestCase):
 def test_missing_pcm_is_recovered_before_sources_removed(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);source=root/'original'/'2026-10-29'/'1'/'call.dar.wav';source.parent.mkdir(parents=True);source.write_bytes(wav())
   cleanup(root);target=root/'pcm'/'2026-10-29'/'1'/'call.wav';self.assertGreater(pcm_info(target)['frames'],0);self.assertFalse((root/'original').exists());self.assertFalse((root/'wav').exists())
   self.assertEqual(json.loads((root/'migrations/pcm-only.json').read_text())['status'],'complete')
 def test_bad_source_does_not_delete_any_originals(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);source=root/'original'/'bad.dar.wav';source.parent.mkdir();source.write_bytes(b'not wav')
   with self.assertRaises(RecordingConversionError):cleanup(root)
   self.assertEqual(source.read_bytes(),b'not wav')
 def test_bad_existing_pcm_blocks_deletion(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);(root/'original').mkdir();(root/'pcm').mkdir();(root/'original'/'call.dar.wav').write_bytes(wav());(root/'pcm'/'call.wav').write_bytes(b'bad')
   with self.assertRaises(RecordingConversionError):cleanup(root)
   self.assertTrue((root/'original'/'call.dar.wav').exists())
if __name__=='__main__':unittest.main()
