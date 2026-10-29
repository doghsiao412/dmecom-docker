import tempfile,unittest
from concurrent.futures import ThreadPoolExecutor
from voice_names import VoiceNames,label_for
from settings_ui import options
from announcement_ui import render_announcement
class VoiceNameTests(unittest.TestCase):
 def test_names_survive_reopen_clear_and_case_variation(self):
  with tempfile.TemporaryDirectory() as td:
   names=VoiceNames(td);names.set('Announcement.wav',' 上班宣告 ');reopened=VoiceNames(td);self.assertEqual(label_for(reopened.all(),'announcement.wav'),'上班宣告');reopened.set('ANNOUNCEMENT.wav','');self.assertEqual(label_for(reopened.all(),'announcement.wav'),'announcement.wav')
 def test_names_validate_and_concurrent_updates_do_not_lose_other_files(self):
  with tempfile.TemporaryDirectory() as td:
   names=VoiceNames(td)
   for value in ['x'*61,'bad\nname','bad\x00name']:
    with self.assertRaises(ValueError):names.set('test.wav',value)
   with self.assertRaises(ValueError):names.set('../bad.wav','name')
   with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(lambda n:names.set(f'{n}.wav',f'名稱 {n}'),range(12)))
   self.assertEqual(len(names.all()),12)
 def test_labels_escape_html_and_keep_real_option_values(self):
  names={'test.wav':'<script>bad</script>'};body=options(['test.wav'],'test.wav',names);self.assertIn('value="test.wav" selected',body);self.assertIn('&lt;script&gt;',body);self.assertNotIn('<script>',body)
  rows=[dict(line=1,enabled=1,file='test.wav',revision='r')];body=render_announcement(dict(lines=rows),[dict(name='test.wav')],[],'csrf',display_names=names);self.assertIn('&lt;script&gt;',body);self.assertIn('<code>test.wav</code>',body)
 def test_rename_copies_display_name_and_keeps_restore_name(self):
  with tempfile.TemporaryDirectory() as td:
   names=VoiceNames(td);names.set('old.wav','下班答錄');names.copy('old.wav','new.wav');self.assertEqual(names.all(),{'old.wav':'下班答錄','new.wav':'下班答錄'});names.set('other.wav','其他宣告');names.copy('old.wav','other.wav');self.assertEqual(names.all()['other.wav'],'其他宣告')
