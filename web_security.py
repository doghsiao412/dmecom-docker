"""Validate console form nonces, including opaque Origin on browser POSTs."""
import secrets
import urllib.parse

class SourceError(ValueError): pass

def authority(url):
    p=urllib.parse.urlsplit(url)
    if p.scheme not in ('http','https') or not p.hostname or p.username is not None or p.password is not None:
        raise SourceError('來源網址無效')
    return p.scheme,p.hostname.lower(),p.port or (443 if p.scheme=='https' else 80)

def validate_form_source(headers,token,expected_token):
    # The unpredictable page nonce is mandatory even when Origin is unavailable.
    if not isinstance(token,str) or not secrets.compare_digest(token,expected_token):
        raise ValueError('頁面已更新，請返回招呼語管理並重新整理後再操作')
    expected=authority('http://'+headers.get('Host',''))
    if headers.get('Sec-Fetch-Site','').lower()=='cross-site':
        raise SourceError('來源不符，請從管理台操作')
    origin=headers.get('Origin','').strip()
    if origin and origin!='null':
        if authority(origin)!=expected:raise SourceError('來源不符，請從管理台操作')
    else:
        # Safari can send an opaque Origin under a no-referrer policy. A valid
        # nonce still prevents other sites from forging a form submission.
        referer=headers.get('Referer','')
        if referer and authority(referer)!=expected:raise SourceError('來源不符，請從管理台操作')
