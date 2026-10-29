import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
import urllib.parse
from backup import Archive
from recording_filters import parse_filters,link,render_filters,render_pagination
class FilterTests(unittest.TestCase):
 def populate(self,root):
  archive=Archive(root)
  entries=[('2026-10-28T23:59:59','0912345678','撥入'),('2026-10-29T00:00:00','0222222222','撥出'),('2026-10-29T02:41:51','0912345678','撥入'),('2026-10-29T23:59:59','','未知')]
  with archive.db() as db:
   for n,(timestamp,phone,direction) in enumerate(entries):
    row=dict(AutoID=n,RecDate=n,RecChannel=0,RecType=2 if direction=='撥入' else 1 if direction=='撥出' else 0,RecFilePath=f'/record-{n}.wav',FileSize=100,PhoneNumber=phone,PhoneNumberRaw=phone,timestamp=timestamp,direction=direction,has_audio=True,RecTime=1)
    db.execute('INSERT INTO recordings(day,key,row_json,version) VALUES(?,?,?,?)',(timestamp[:10],str(n),json.dumps(row),'v1'))
  return archive
 def test_cross_day_bounds_are_inclusive_and_combined(self):
  with tempfile.TemporaryDirectory() as td:
   a=self.populate(td);result=a.search(start='2026-10-28T23:59:59',end='2026-10-29T02:41:51',phone='9123',direction='IN')
   self.assertEqual([r['key'] for r in result['rows']],['0','2']);self.assertEqual(result['rows'][1]['day'],'2026-10-29')
   self.assertEqual(a.search(direction='OUT')['total'],1);self.assertEqual(a.search(direction='UNKNOWN')['total'],1)
 def test_literal_phone_and_pagination_do_not_drop_filters(self):
  with tempfile.TemporaryDirectory() as td:
   a=self.populate(td);self.assertEqual(a.search(phone="%' OR 1=1 --")['total'],0)
   result=a.search(page=2,page_size=2);self.assertEqual([r['key'] for r in result['rows']],['2','3']);self.assertEqual(result['total'],4)
   filters=parse_filters({'start':[''],'end':[''],'phone':['0912'],'direction':['IN']});url=link(filters,page=2);query=urllib.parse.parse_qs(urllib.parse.urlsplit(url).query,keep_blank_values=True)
   self.assertEqual(parse_filters(query),dict(filters,page=2));self.assertIn('上一頁',render_pagination(filters,result))
 def test_default_today_old_date_links_and_validation(self):
  today=dt.date(2026,10,29);self.assertEqual(parse_filters({},today)['start'],'2026-10-29T00:00:00')
  self.assertEqual(parse_filters({'date':['2026-10-28']},today)['end'],'2026-10-28T23:59:59')
  self.assertEqual(parse_filters({'start':[''],'end':['']},today)['start'],'')
  for query in [{'start':['2026-10-29T04:00'],'end':['2026-10-29T03:00']},{'direction':['BAD']},{'start':['2026-02-30T00:00']},{'page':['0']}]:
   with self.assertRaises(ValueError):parse_filters(query,today)
 def test_filter_controls_escape_user_input(self):
  filters=parse_filters({'phone':['\"><script>bad</script>']});page=render_filters(filters,dict(total=0,backed_up=0,page=1,pages=1))
  self.assertNotIn('<script>',page);self.assertIn('datetime-local',page);self.assertIn('來去電',page)
if __name__=='__main__':unittest.main()
