import unittest
from web_security import validate_form_source,SourceError

class BrowserPostTests(unittest.TestCase):
    token='7'*48
    def test_safari_opaque_origin_with_valid_nonce(self):
        validate_form_source({'Host':'100.64.0.10:8766','Origin':'null'},self.token,self.token)
    def test_same_origin_normalizes_host_and_default_port(self):
        validate_form_source({'Host':'NAS.Example:80','Origin':'http://nas.example'},self.token,self.token)
    def test_same_origin_multipart_metadata(self):
        validate_form_source({'Host':'100.64.0.10:8766','Origin':'http://100.64.0.10:8766','Sec-Fetch-Site':'same-origin'},self.token,self.token)
    def test_no_origin_api_requires_nonce(self):
        validate_form_source({'Host':'100.64.0.10:8766'},self.token,self.token)
        with self.assertRaises(ValueError):validate_form_source({'Host':'100.64.0.10:8766'},'',self.token)
    def test_opaque_origin_does_not_bypass_nonce(self):
        for token in ['', 'wrong']:
            with self.assertRaises(ValueError):validate_form_source({'Host':'100.64.0.10:8766','Origin':'null'},token,self.token)
    def test_other_host_scheme_or_port_is_rejected(self):
        for origin in ['http://attacker.example','http://100.64.0.10','https://100.64.0.10:8766']:
            with self.assertRaises(SourceError):validate_form_source({'Host':'100.64.0.10:8766','Origin':origin},self.token,self.token)
    def test_cross_site_opaque_origin_is_rejected(self):
        with self.assertRaises(SourceError):validate_form_source({'Host':'100.64.0.10:8766','Origin':'null','Sec-Fetch-Site':'cross-site'},self.token,self.token)
    def test_opaque_origin_checks_referer_when_present(self):
        validate_form_source({'Host':'100.64.0.10:8766','Origin':'null','Referer':'http://100.64.0.10:8766/voices'},self.token,self.token)
        with self.assertRaises(SourceError):validate_form_source({'Host':'100.64.0.10:8766','Origin':'null','Referer':'http://attacker.example/voices'},self.token,self.token)

if __name__=='__main__':unittest.main()
