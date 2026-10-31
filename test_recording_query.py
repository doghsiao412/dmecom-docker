"""Call-type filters, sorting, statistics and XLSX export (NAS data only, no device)."""
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer
from backup import Archive
from recording_filters import parse_filters, link, stats_link, export_link
import recording_query

# (timestamp, phone, RecType, RecChannel, RecTime, TalkTime, RingTime) shaped like the live archive.
ENTRIES = [('2026-09-30T10:00:00', '0912345678', 2, 0, 120, 120, 3),
           ('2026-10-01T09:00:00', '0222222222', 1, 0, 30, 31, 0),
           ('2026-10-01T21:30:00', '0933333333', 7, 0, 25, 25, 8),
           ('2026-10-02T12:00:00', '0912345678', 4, 1, 0, 0, 18),
           ('2026-10-05T18:00:00', '0944444444', 6, 0, 0, 60, 2),
           ('2026-12-31T23:59:59', '0955555555', 5, 2, 0, 3, 0)]

def populate(root):
    archive = Archive(root)
    with archive.db() as db:
        for n, (ts, phone, kind, line, rec, talk, ring) in enumerate(ENTRIES):
            direction = '撥出' if kind in (1, 5) else '撥入'
            row = dict(AutoID=n, RecDate=n, RecChannel=line, RecType=kind, RecFilePath=f'/r-{n}.wav', FileSize=100,
                       PhoneNumber=phone, PhoneNumberRaw=phone, timestamp=ts, direction=direction,
                       has_audio=kind in (1, 2, 7), RecTime=rec, TalkTime=talk, RingTime=ring)
            db.execute('INSERT INTO recordings(day,key,row_json,version) VALUES(?,?,?,?)', (ts[:10], str(n), json.dumps(row), 'v1'))
    return archive

def keys(result): return [r['key'] for r in result['rows']]

class QueryTests(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(); self.addCleanup(td.cleanup); self.archive = populate(td.name)

    def test_call_type_line_and_seconds(self):
        a = self.archive
        self.assertEqual(keys(a.search(call_type='MISSED')), ['3'])
        self.assertEqual(keys(a.search(call_type='UNRECORDED')), ['4', '5'])
        self.assertEqual(keys(a.search(call_type='AUTO')), ['2'])
        self.assertEqual(keys(a.search(call_type='RECORDED')), ['0', '1', '2'])
        self.assertEqual(keys(a.search(line=2)), ['3'])
        self.assertEqual(keys(a.search(min_seconds=25, max_seconds=30)), ['1', '2'])
        self.assertEqual(keys(a.search(direction='IN', call_type='MISSED', phone='0912')), ['3'])
        for bad in (dict(call_type='X'), dict(line=5), dict(min_seconds=9, max_seconds=3), dict(sort='x')):
            with self.assertRaises(ValueError): a.search(**bad)

    def test_sorting(self):
        a = self.archive
        self.assertEqual(keys(a.search(sort='time_desc')), ['5', '4', '3', '2', '1', '0'])
        self.assertEqual(keys(a.search(sort='length_desc'))[:3], ['0', '1', '2'])
        self.assertEqual(keys(a.search(sort='phone'))[:3], ['1', '0', '3'])

    def test_statistics_by_period(self):
        a = self.archive
        month = a.statistics(period='month')
        self.assertEqual([r['period'] for r in month['rows']], ['2026-12', '2026-10', '2026-09'])
        oct_ = month['rows'][1]
        self.assertEqual((oct_['total'], oct_['incoming'], oct_['outgoing'], oct_['missed'], oct_['unrecorded'], oct_['auto'], oct_['seconds']), (4, 1, 1, 1, 1, 1, 55))
        self.assertEqual(month['totals']['total'], 6)
        self.assertEqual([r['period'] for r in a.statistics(period='quarter')['rows']], ['2026-Q4', '2026-Q3'])
        self.assertEqual([r['period'] for r in a.statistics(period='year')['rows']], ['2026'])
        # 2026-10-01 is a Thursday; its week starts Monday 2026-09-28, like 2026-09-30.
        week = {r['period']: r['total'] for r in a.statistics(period='week')['rows']}
        self.assertEqual(week, {'2026-12-28': 1, '2026-10-05': 1, '2026-09-28': 4})
        self.assertEqual(len(a.statistics(period='day', call_type='MISSED')['rows']), 1)
        with self.assertRaises(ValueError): a.statistics(period='hour')

    def test_xlsx_export_keeps_phone_text(self):
        rows = self.archive.export(call_type='MISSED')
        data = recording_query.xlsx(recording_query.EXPORT_HEADER, [recording_query.export_row(r) for r in rows])
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            self.assertIn('xl/workbook.xml', z.namelist())
            sheet = z.read('xl/worksheets/sheet1.xml').decode()
        self.assertIn('<t xml:space="preserve">0912345678</t>', sheet)
        self.assertIn('來電未接', sheet)
        self.assertIn('<c r="H2"><v>18</v></c>', sheet)
        with self.assertRaisesRegex(ValueError, '超過匯出上限'): self.archive.export(limit=2)

    def test_xlsx_escapes_and_strips_control_characters(self):
        data = recording_query.xlsx(['a'], [['<b>&\x01x']])
        sheet = zipfile.ZipFile(io.BytesIO(data)).read('xl/worksheets/sheet1.xml').decode()
        self.assertIn('&lt;b&gt;&amp;x', sheet)
        self.assertEqual(recording_query.column(27), 'AA')

class FilterParsingTests(unittest.TestCase):
    def test_parse_round_trip_and_defaults(self):
        q = {'start': [''], 'end': [''], 'call_type': ['missed'], 'line': ['2'], 'min_seconds': ['5'], 'max_seconds': [''], 'sort': ['time_desc']}
        f = parse_filters(q)
        self.assertEqual((f['call_type'], f['line'], f['min_seconds'], f['max_seconds'], f['sort']), ('MISSED', 2, 5, None, 'time_desc'))
        back = urllib.parse.parse_qs(urllib.parse.urlsplit(link(f, page=2)).query, keep_blank_values=True)
        self.assertEqual(parse_filters(back), dict(f, page=2))
        self.assertNotIn('call_type', link(parse_filters({})))
        self.assertIn('start=&end=', stats_link(parse_filters({})))
        self.assertTrue(export_link(f).startswith('/recordings/export.xlsx?'))
        for bad in ({'call_type': ['x']}, {'line': ['9']}, {'min_seconds': ['-1']}, {'min_seconds': ['9'], 'max_seconds': ['2']}, {'sort': ['evil']}):
            with self.assertRaises(ValueError): parse_filters(bad)

import urllib.parse

class QueryHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(); root = Path(cls.temp.name); cfg = root / 'config.json'
        cfg.write_text(json.dumps(dict(username='test', password='test', device_mac='02:00:00:00:00:01')))
        populate(root / 'data')
        old = {k: os.environ.get(k) for k in ['DMECOM_CONFIG', 'DMECOM_DATA']}
        os.environ.update(DMECOM_CONFIG=str(cfg), DMECOM_DATA=str(root / 'data'))
        try:
            spec = importlib.util.spec_from_file_location('query_test_server', Path(__file__).with_name('server.py'))
            cls.module = importlib.util.module_from_spec(spec); spec.loader.exec_module(cls.module)
        finally:
            for k, v in old.items():
                if v is None: os.environ.pop(k, None)
                else: os.environ[k] = v
        cls.http = ThreadingHTTPServer(('127.0.0.1', 0), cls.module.Handler)
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True); cls.thread.start()
        cls.url = 'http://127.0.0.1:' + str(cls.http.server_port)

    @classmethod
    def tearDownClass(cls): cls.http.shutdown(); cls.http.server_close(); cls.thread.join(3); cls.temp.cleanup()

    def get(self, path):
        with urllib.request.urlopen(self.url + path) as r: return r.headers, r.read()

    def test_list_shows_type_labels_and_new_controls(self):
        _, body = self.get('/?start=&end=&call_type=MISSED')
        page = body.decode()
        self.assertIn('來電未接', page); self.assertIn('0912345678', page); self.assertNotIn('0222222222', page)
        for text in ('通話類型', '線路', '排序', '匯出 Excel', '/stats?'): self.assertIn(text, page)

    def test_stats_page_and_period_tabs(self):
        _, body = self.get('/stats?start=&end=&period=month')
        page = body.decode()
        self.assertIn('2026-10', page); self.assertIn('按季', page)
        self.assertRegex(page, r'來電未接</span><strong>1<')
        with self.assertRaises(urllib.error.HTTPError) as e: self.get('/stats?period=hour')
        self.assertEqual(e.exception.code, 400)

    def test_export_download(self):
        headers, body = self.get('/recordings/export.xlsx?start=&end=&line=1')
        self.assertEqual(headers['Content-Type'], 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        self.assertIn('filename="recording.xlsx"', headers['Content-Disposition'])
        sheet = zipfile.ZipFile(io.BytesIO(body)).read('xl/worksheets/sheet1.xml').decode()
        self.assertEqual(len(re.findall('<row ', sheet)), 1 + 4)

if __name__ == '__main__': unittest.main()
