import ipaddress,struct,unittest
from discovery import response_ip,normalize_mac,DiscoveryError

class IdentityTests(unittest.TestCase):
 def test_reply_requires_expected_identity_and_local_network(self):
  device=bytes.fromhex('02000000005b');ours=bytes.fromhex('020000000046')
  own_ip=bytes([192,0,2,46]);device_ip=bytes([192,0,2,177])
  frame=ours+device+b'\x08\x06'+struct.pack('!HHBBH',1,0x0800,6,4,2)+device+device_ip+ours+own_ip
  net=ipaddress.ip_network('192.0.2.0/24')
  self.assertEqual(response_ip(frame,device,ours,'192.0.2.46',net),'192.0.2.177')
  self.assertIsNone(response_ip(frame,bytes(6),ours,'192.0.2.46',net))
  self.assertIsNone(response_ip(frame,device,ours,'192.0.2.45',net))
  self.assertIsNone(response_ip(frame,device,ours,'192.0.2.46',ipaddress.ip_network('192.168.1.0/24')))
  self.assertIsNone(response_ip(frame[:28],device,ours,'192.0.2.46',net))
 def test_invalid_mac_never_silently_matches(self):
  self.assertEqual(normalize_mac('02-00-00-00-00-5B'),'02:00:00:00:00:5b')
  for value in ['02:00:00:00:00:100','02:00:00','zz:00:00:00:00:5b']:
   with self.assertRaises(DiscoveryError):normalize_mac(value)
if __name__=='__main__':unittest.main()
