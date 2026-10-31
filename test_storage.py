import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from dmecom import Client, ProtocolError
from device_storage import DeviceStorage, summary

# Replies captured from the DAR-A4100LS on 2026-10-31 (16 GB card, freshly formatted).
CARD = bytes.fromhex('0d0009f73a0000ec3a00007275fe0d')
LINE_STATUS = bytes.fromhex('6200') + bytes([9, 0]) + bytes(96)
GLOBALS = {0x15: bytes.fromhex('0a00000000'), 0x16: bytes.fromhex('0100000000'),
           0x23: bytes.fromhex('e70707000a00000001000000176700008c050000')}

def fake(frames, globals_=GLOBALS):
    c = Client({}); sent = []; queue = list(frames)
    c.send = lambda cmd, sub, data=b'': sent.append((cmd, sub, data))
    c.receive = lambda: queue.pop(0)
    c.global_parameter = lambda key: globals_[key]
    return c, sent

class StorageProtocolTests(unittest.TestCase):
    def test_parses_live_reply_and_skips_line_status(self):
        c, sent = fake([LINE_STATUS, CARD])
        info = c.storage()
        self.assertEqual(sent, [(9, 0, b'\0\0')])
        self.assertEqual(info, dict(total_mb=15095, free_mb=15084, reserve_percent=10, loop_recording=True,
                                    last_loop_delete=dt.datetime(2023, 7, 10, 0, 1)))

    def test_no_loop_history(self):
        g = dict(GLOBALS); g[0x23] = bytes(20)
        c, _ = fake([CARD], g)
        self.assertIsNone(c.storage()['last_loop_delete'])

    def test_rejects_implausible_values(self):
        c, _ = fake([bytes.fromhex('0d0009') + (100).to_bytes(4, 'little') + (200).to_bytes(4, 'little') + bytes(4)])
        with self.assertRaises(ProtocolError): c.storage()
        g = dict(GLOBALS); g[0x16] = bytes.fromhex('0700000000')
        c, _ = fake([CARD], g)
        with self.assertRaises(ProtocolError): c.storage()

    def test_allowlist_only_read_queries(self):
        c = Client({}); c.sock = None
        for data in (b'\0\x15', b'\0\x16', b'\0\x23'):
            with self.assertRaises(AttributeError): c.send(8, 0, data)  # allowed, then fails on missing socket
        with self.assertRaises(AttributeError): c.send(9, 0, b'\0\0')
        for cmd, sub, data in ((8, 1, b'\0\x16\0\0\0\0'), (8, 1, b'\0\x15\x05\0\0\0'), (9, 0, b'\0\1')):
            with self.assertRaisesRegex(ProtocolError, 'allowlist'): c.send(cmd, sub, data)

class StorageServiceTests(unittest.TestCase):
    def test_inspect_saves_snapshot(self):
        info = dict(total_mb=15095, free_mb=15084, reserve_percent=10, loop_recording=True, last_loop_delete=dt.datetime(2023, 7, 10, 0, 1))
        class Recorder:
            def storage(self): return dict(info)
        with tempfile.TemporaryDirectory() as td:
            s = DeviceStorage(td, lambda fn: fn(Recorder()))
            result = s.inspect()
            self.assertEqual(result['last_loop_delete'], '2023-07-10 00:01')
            self.assertEqual(json.loads((Path(td) / 'device-storage.json').read_text()), result)
            self.assertEqual(s.snapshot(), result)

    def test_summary_and_warnings(self):
        base = dict(total_mb=15095, free_mb=15084, reserve_percent=10, loop_recording=True)
        s = summary(base)
        self.assertEqual((s['total'], s['used'], s['free'], s['used_percent'], s['warnings']), ('14.74 GB', '0.01 GB', '14.73 GB', '0.1%', []))
        self.assertIn('循環錄音已關閉', summary(dict(base, loop_recording=False))['warnings'][0])
        self.assertIn('最舊的錄音', summary(dict(base, free_mb=1500))['warnings'][0])
        self.assertEqual(len(summary(dict(base, free_mb=1500, loop_recording=False))['warnings']), 2)

if __name__ == '__main__': unittest.main()
