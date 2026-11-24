"""Private single-user HTTPS deployment behind a TLS reverse proxy. Python 3 standard library."""
import smtplib,re
from email.message import EmailMessage
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
def search(q):
 key=os.environ.get('BRAVE_API_KEY','')
 if key:
  url='https://api.search.brave.com/res/v1/web/search?'+urllib.parse.urlencode({'q':q,'count':20})
  req=urllib.request.Request(url,headers={'Accept':'application/json','X-Subscription-Token':key})
 else:
  u=urllib.parse.urlsplit(PROXY); req=urllib.request.Request(urllib.parse.urlunsplit(u._replace(query=urllib.parse.urlencode(urllib.parse.parse_qsl(u.query)+[('q',q)]))))
 with urllib.request.urlopen(req,timeout=20) as r: return json.loads(r.read(2000000))
def assess(q,x):
 words=set(re.findall(r'[a-z0-9]{3,}',q.lower()))-{'site','www','com','https','the','and','for'}
 found=words & set(re.findall(r'[a-z0-9]{3,}',(str(x.get('title',''))+' '+str(x.get('description',''))).lower()))
 return round(100*len(found)/max(1,len(words)))
def email_alert(name,fresh):
 host=os.environ.get('SMTP_HOST',''); target=os.environ.get('ALERT_TO','')
 if not host or not target:return
 m=EmailMessage();m['Subject']='Art Detective: new candidates';m['From']=os.environ['SMTP_FROM'];m['To']=target;m.set_content(f'{fresh} new candidate links for {name}. Synchronise your app to review them. These are not verified matches.')
 with smtplib.SMTP_SSL(host,int(os.environ.get('SMTP_PORT','465')),timeout=20) as smtp:
  if os.environ.get('SMTP_USER'):smtp.login(os.environ['SMTP_USER'],os.environ['SMTP_PASSWORD'])
  smtp.send_message(m)
def scan():
 with lock:
  with db() as c: watches=c.execute('select name,queries from watches').fetchall()
  for name,qs in watches:
   errors=[]; fresh=0
   for q in json.loads(qs):
    try:
     j=search(q)
     arr=j.get('results',j.get('web',{}).get('results')); assert isinstance(arr,list),'Invalid results'
     with db() as c:
      for x in arr[:50]:
       if not isinstance(x,dict):continue
       url=normalize(str(x.get('url','')))
       if url:fresh+=c.execute('insert or ignore into hits values (?,?,?)',(name,url,json.dumps(dict(url=url,title=str(x.get('title','')),description=str(x.get('description','')),query=q,score=assess(q,x))))).rowcount
    except Exception as e:errors.append({'query':q,'error':str(e)})
   with db() as c:c.execute('insert or replace into runs values (?,?)',(name,json.dumps(dict(time=time.time(),fresh=fresh,errors=errors))))
   if fresh:
    try:email_alert(name,fresh)
    except Exception as e:print('Email failed:',type(e).__name__,flush=True)
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
  if u.path=='/health':return self.reply(200,{'provider':'Brave' if os.environ.get('BRAVE_API_KEY') else 'Proxy','email':bool(os.environ.get('SMTP_HOST') and os.environ.get('ALERT_TO')),'interval':INTERVAL})
  if u.path!='/results':return self.reply(404,{})
  name=urllib.parse.parse_qs(u.query).get('case',[''])[0]
  with db() as c:
   rows=c.execute('select payload from hits where name=?',(name,)).fetchall();run=c.execute('select payload from runs where name=?',(name,)).fetchone()
  self.reply(200,{'case':name,'results':[json.loads(r[0]) for r in rows],'run':json.loads(run[0]) if run else None})
if __name__=='__main__':
 assert len(TOKEN)>=32 and ORIGIN.startswith('https://') and (PROXY.startswith('https://') or os.environ.get('BRAVE_API_KEY')),'Configure token, origin and HTTPS proxy'
 threading.Thread(target=loop,daemon=True).start();ThreadingHTTPServer((os.environ.get('BIND_HOST','127.0.0.1'),int(os.environ.get('PORT','8080'))),Handler).serve_forever()
