"""Loopback-only report preview with HTTP byte-range support for video QA."""
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from functools import partial
import re
class Handler(SimpleHTTPRequestHandler):
 def send_head(self):
  self.byte_range=None
  file=Path(self.translate_path(self.path));header=self.headers.get('Range','')
  match=re.fullmatch(r'bytes=(\d+)-(\d*)',header)
  if file.is_file() and match:
   size=file.stat().st_size;start=int(match[1]);end=min(size-1,int(match[2]) if match[2] else size-1)
   if start>=size or end<start:self.send_error(416);return None
   stream=file.open('rb');stream.seek(start);self.byte_range=(start,end)
   self.send_response(206);self.send_header('Content-Type',self.guess_type(str(file)));self.send_header('Content-Length',str(end-start+1));self.send_header('Content-Range',f'bytes {start}-{end}/{size}');self.send_header('Accept-Ranges','bytes');self.end_headers();return stream
  return super().send_head()
 def copyfile(self,source,outputfile):
  if self.byte_range is None:return super().copyfile(source,outputfile)
  left=self.byte_range[1]-self.byte_range[0]+1
  try:
   while left:
    buf=source.read(min(left,65536))
    if not buf:break
    outputfile.write(buf);left-=len(buf)
  except (BrokenPipeError,ConnectionResetError):pass
 def log_message(self,*args):pass
if __name__=='__main__':
 root=Path(__file__).resolve().parent.parent
 print('Video preview on http://127.0.0.1:8769/watch.html',flush=True)
 ThreadingHTTPServer(('127.0.0.1',8769),partial(Handler,directory=str(root))).serve_forever()
