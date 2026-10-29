"""Bounded local WAV streaming, including byte ranges used by Safari seeking."""
import os
import re

def byte_range(header,size):
    if not header:return None
    if len(header)>200:raise ValueError('Invalid byte range')
    match=re.fullmatch(r'bytes=(\d*)-(\d*)',header.strip(),re.IGNORECASE)
    if not match or not any(match.groups()) or size<=0:raise ValueError('Invalid byte range')
    first,last=match.groups()
    if not first:
        count=int(last)
        if count<=0:raise ValueError('Invalid suffix range')
        return max(0,size-count),size-1
    start=int(first);end=min(int(last),size-1) if last else size-1
    if start>=size or end<start:raise ValueError('Unsatisfiable byte range')
    return start,end

def serve_wav(handler,path,filename=None,head=False):
    with path.open('rb') as source:
        size=os.fstat(source.fileno()).st_size
        try:selected=byte_range(handler.headers.get('Range') if not head and getattr(handler,'command','GET')=='GET' and not handler.headers.get('If-Range') else None,size)
        except ValueError:
            handler.send_response(416);handler.send_header('Content-Range','bytes */'+str(size));handler.send_header('Content-Length','0');handler.send_header('Accept-Ranges','bytes');handler.send_header('Cache-Control','no-store');handler.end_headers();return
        start,end=selected if selected else (0,size-1);length=max(0,end-start+1)
        handler.send_response(206 if selected else 200)
        for name,value in [('Content-Type','audio/wav'),('Content-Length',str(length)),('Accept-Ranges','bytes'),('Cache-Control','no-store'),('Referrer-Policy','same-origin'),('X-Content-Type-Options','nosniff'),('X-Frame-Options','DENY')]:handler.send_header(name,value)
        if selected:handler.send_header('Content-Range',f'bytes {start}-{end}/{size}')
        if filename:
            import urllib.parse
            handler.send_header('Content-Disposition',"attachment; filename=\"recording.wav\"; filename*=UTF-8''"+urllib.parse.quote(filename,safe=''))
        handler.end_headers()
        if head:return
        source.seek(start);remaining=length
        try:
            while remaining:
                chunk=source.read(min(65536,remaining))
                if not chunk:break
                handler.wfile.write(chunk);remaining-=len(chunk)
        except (BrokenPipeError,ConnectionResetError):pass
