import copy
import struct
import tempfile
import unittest
from answer import AnswerSettings,encode_weekly,parse_weekly
from test_answer import Recorder,week
from settings_ui import render_settings,parse_line_form

class EditableRecorder(Recorder):
 def voices(self):return [dict(name='old.wav',size=123),dict(name='nas-new.wav',size=100)]
class SettingsTests(unittest.TestCase):
 def service(self,root):
  c=EditableRecorder();a=AnswerSettings(root,lambda fn:fn(c));a.inspect();return c,a
 def test_edit_one_line_then_restore_keeps_other_lines_and_original_active_plan(self):
  with tempfile.TemporaryDirectory() as td:
   c,a=self.service(td);a.configure('nas-new.wav',lambda c:None);a.inspect();before=copy.deepcopy(c.values);active=a.snapshot()['active']
   revisions={str(r['line']):r['revision'] for r in a.snapshot()['lines']}
   a.edit_lines({'1':dict(rings=5,message_limit=60,announcement=0)},revisions)
   self.assertEqual(struct.unpack('<I',c.values[0,0x2d])[0],5)
   self.assertTrue(all(c.values[k]==v for k,v in before.items() if k[0]!=0))
   a.restore();self.assertEqual(c.values,before);self.assertEqual(a.snapshot()['active'],active)
 def test_stale_form_and_invalid_second_line_prevent_all_writes(self):
  with tempfile.TemporaryDirectory() as td:
   c,a=self.service(td);rev={str(r['line']):r['revision'] for r in a.snapshot()['lines']}
   c.values[0,0x2d]=struct.pack('<I',7)
   with self.assertRaises(ValueError):a.edit_lines({'1':dict(rings=4)},rev)
   self.assertFalse(c.writes)
   a.inspect();rev={str(r['line']):r['revision'] for r in a.snapshot()['lines']}
   with self.assertRaises(ValueError):a.edit_lines({'1':dict(rings=4),'2':dict(rings=0)},rev)
   self.assertFalse(c.writes)
 def test_missing_active_voice_rejected_and_disabled_missing_name_can_stay(self):
  with tempfile.TemporaryDirectory() as td:
   c,a=self.service(td);rev={str(r['line']):r['revision'] for r in a.snapshot()['lines']};days=parse_weekly(week());days[0]['slots'][0]['file']='missing.wav'
   with self.assertRaises(ValueError):a.edit_lines({'1':dict(weekly=days)},rev)
   self.assertFalse(c.writes);days[0]['slots'][0]['enabled']=0
   a.edit_lines({'1':dict(weekly=days)},rev)
 def test_restore_owns_only_changed_fields(self):
  with tempfile.TemporaryDirectory() as td:
   c,a=self.service(td);rev={str(r['line']):r['revision'] for r in a.snapshot()['lines']}
   a.edit_lines({'1':dict(rings=4)},rev);c.values[1,0x2b]=struct.pack('<I',90);a.restore()
   self.assertEqual(struct.unpack('<I',c.values[1,0x2b])[0],90)
 def test_unchanged_form_preserves_native_filename_padding(self):
  raw=bytearray(week());raw[120:124]=b'x\0y\0'
  self.assertEqual(encode_weekly(bytes(raw),parse_weekly(bytes(raw))),bytes(raw))
 def test_form_contains_all_editable_slots_and_round_trips(self):
  with tempfile.TemporaryDirectory() as td:
   c,a=self.service(td);snapshot=a.snapshot();page=render_settings(snapshot,c.voices(),3600,'testnonce')
   self.assertEqual(page.count('class="line-settings"'),4);self.assertIn('name="d6_s3_file"',page);self.assertIn('name="apply_all"',page)
   args=dict(rings='8',message_limit='60',announcement='1')
   for d,day in enumerate(snapshot['lines'][0]['weekly']):
    for n,slot in enumerate(day['slots']):
     p=f'd{d}_s{n}_';args.update({p+'enabled':str(slot['enabled']),p+'start':slot['start'],p+'end':slot['end'],p+'file':slot['file']})
   result=parse_line_form(args);self.assertEqual(encode_weekly(week(),result['weekly']),week())
if __name__=='__main__':unittest.main()
