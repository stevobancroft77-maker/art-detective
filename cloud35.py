"""Private single-user HTTPS deployment behind a TLS reverse proxy. Python 3 standard library."""
import os,json,sqlite3,hmac,threading,time,urllib.request,urllib.parse
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
TOKEN=os.environ.get('ART_TOKEN',''); ORIGIN=os.environ.get('ART_ORIGIN',''); PROXY=os.environ.get('SEARCH_PROXY',''); DB=os.environ.get('ART_DB','art-private.sqlite'); INTERVAL=max(900,int(os.environ.get('ART_INTERVAL','3600')))
lock=threading.Lock()
def db():
 c=sqlite3.connect(DB); c.execute('create table if not exists watches (name text primary key, queries text)'); c.execute('create table if not exists hits (name text,url text, payload text, primary key(name,url))'); c.execute('create table if not exists runs (name text primary key, payload text)'); return c
def normalize(s):
 u=urllib.parse.urlsplit(s)
 if u.scheme not in ('http','https') or not u.hostname:return ''
 return urllib.parse.urlunsplit(u._replace(fragment='',query=urllib.parse.urlencode(sorted((k,v) for k,v in urllib.parse.parse_qsl(u.query) if not k.lower().startswith('utm_') and k.lower() not in ('fbclid','gclid')))))
def scan():
 with lock:
  with db() as c: watches=c.execute('select name,queries from watches').fetchall()
  for name,qs in watches:
   errors=[]; fresh=0
   for q in json.loads(qs):
    try:
     u=urllib.parse.urlsplit(PROXY); url=urllib.parse.urlunsplit(u._replace(query=urllib.parse.urlencode(urllib.parse.parse_qsl(u.query)+[('q',q)])))
     with urllib.request.urlopen(url,timeout=20) as r: j=json.loads(r.read(2000000))
     arr=j.get('results',j.get('web',{}).get('results')); assert isinstance(arr,list),'Invalid results'
     with db() as c:
      for x in arr[:50]:
       if not isinstance(x,dict):continue
       url=normalize(str(x.get('url','')))
       if url:fresh+=c.execute('insert or ignore into hits values (?,?,?)',(name,url,json.dumps(dict(url=url,title=str(x.get('title','')),description=str(x.get('description','')),query=q)))).rowcount
    except Exception as e:errors.append({'query':q,'error':str(e)})
   with db() as c:c.execute('insert or replace into runs values (?,?)',(name,json.dumps(dict(time=time.time(),fresh=fresh,errors=errors))))
   hook=os.environ.get('ART_WEBHOOK','')
   if fresh and hook.startswith('https://'):
    try:
     with urllib.request.urlopen(urllib.request.Request(hook,data=json.dumps({'text':f'Art Detective: {fresh} new links for {name}'}).encode(),headers={'Content-Type':'application/json'}),timeout=15):pass
    except Exception as e: print('Notification failed:',e,flush=True)
def loop():
 while True:
  try:scan()
  except Exception as e:print('Scan failed:',e,flush=True)
  time.sleep(INTERVAL)
class Handler(BaseHTTPRequestHandler):
 def reply(self,status,obj):
  self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Access-Control-Allow-Origin',ORIGIN);self.send_header('Access-Control-Allow-Headers','Authorization, Content-Type');self.send_header('Access-Control-Allow-Methods','GET, POST, OPTIONS');self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(json.dumps(obj).encode())
 def do_OPTIONS(self):self.reply(200,{})
 def auth(self):
  if hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+TOKEN):return True
  self.reply(401,{'error':'Unauthorized'});return False
 def do_POST(self):
  if not self.auth():return
  try:
   if self.path!='/watch':return self.reply(404,{})
   n=int(self.headers.get('Content-Length','0'));assert 0<n<=100000
   j=json.loads(self.rfile.read(n));name=j['case'];qs=j['queries'];assert isinstance(name,str) and 0<len(name)<=300 and isinstance(qs,list) and len(qs)<=50 and all(isinstance(q,str) and 0<len(q)<=1000 for q in qs)
   with db() as c:c.execute('insert or replace into watches values (?,?)',(name,json.dumps(list(dict.fromkeys(qs)))))
   self.reply(200,{'saved':len(qs)})
  except Exception:self.reply(400,{'error':'Invalid watchlist'})
 def do_GET(self):
  if not self.auth():return
  u=urllib.parse.urlsplit(self.path)
  if u.path!='/results':return self.reply(404,{})
  name=urllib.parse.parse_qs(u.query).get('case',[''])[0]
  with db() as c:
   rows=c.execute('select payload from hits where name=?',(name,)).fetchall();run=c.execute('select payload from runs where name=?',(name,)).fetchone()
  self.reply(200,{'case':name,'results':[json.loads(r[0]) for r in rows],'run':json.loads(run[0]) if run else None})
if __name__=='__main__':
 assert len(TOKEN)>=32 and ORIGIN.startswith('https://') and PROXY.startswith('https://'),'Configure token, origin and HTTPS proxy'
 threading.Thread(target=loop,daemon=True).start();ThreadingHTTPServer(('127.0.0.1',int(os.environ.get('PORT','8080'))),Handler).serve_forever()
