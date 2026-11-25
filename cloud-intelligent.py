"""On-demand archive research, standard library only. No scheduled jobs."""
import os,json,hmac,re,time,urllib.request,urllib.parse,concurrent.futures,threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
TOKEN=os.environ.get("ART_TOKEN","")
ORIGIN=os.environ.get("ART_ORIGIN","")
cases={}
lock=threading.Lock()
pool=concurrent.futures.ThreadPoolExecutor(max_workers=1)
def fetch(url,params):
 req=urllib.request.Request(url+"?"+urllib.parse.urlencode(params,doseq=True),headers={"User-Agent":"ArtDetective/1.0 (archive research)"})
 with urllib.request.urlopen(req,timeout=10) as r:return json.loads(r.read(3000000))
def search(q,source):
 if source=="Internet Archive":
  j=fetch("https://archive.org/advancedsearch.php",{"q":q,"fl[]":["identifier","title","description"],"rows":12,"output":"json"})
  return [{"url":"https://archive.org/details/"+urllib.parse.quote(x["identifier"],safe=""),"title":str(x.get("title","")),"description":str(x.get("description",""))[:1500]} for x in j["response"]["docs"] if x.get("identifier")]
 j=fetch("https://openlibrary.org/search.json",{"q":q,"limit":12,"fields":"key,title,author_name,first_publish_year"})
 return [{"url":"https://openlibrary.org"+x["key"],"title":x.get("title",""),"description":str(x.get("author_name",[]))+" "+str(x.get("first_publish_year",""))} for x in j["docs"] if x.get("key","").startswith("/works/")]
def words(s):return set(re.findall(r"[a-z0-9]{3,}",s.lower()))-{"the","and","for","with","from","site","auction","catalogue"}
def run(name,qs):
 hits={}; errors=[]; done=0
 tasks=[(q,s) for q in qs[:4] for s in ("Internet Archive","Open Library")]
 seen=set(tasks)
 def follow(q,s):
  q=re.sub(r"site:\\S+","",q).replace(chr(34),"").strip()
  q=re.sub(r"\\b(?:June|December|January|February|March|April|May|July|August|September|October|November)\\b","",q,flags=re.I)
  q=re.sub(r"\\bBrueghel\\b","Bruegel",q,flags=re.I) if re.search(r"Brueghel",q,re.I) else q
  q=" ".join(q.split())
  if q and (q,s) not in seen and len(tasks)<16:
   seen.add((q,s));tasks.append((q,s))
 for q,s in tasks:
  try:
   results=search(q,s)
   before=len(hits)
   for x in results:
    w=words(q+" "+name); overlap=w & words(x["title"]+" "+x["description"])
    score=round(100*len(overlap)/max(1,len(w)))
    if not overlap:continue
    item=dict(x,query=q,source=s,score=score,status="Unverified candidate",matched_terms=sorted(overlap))
    if x["url"] not in hits or score>hits[x["url"]]["score"]:hits[x["url"]]=item
   done+=1
   if len(hits)==before:follow(q,s)
   else:
    for x in results[:2]:
     title=x["title"]
     if re.search(r"catalog|auction|sale",title,re.I) and 8<len(title)<160 and len(tasks)<16:
      other="Open Library" if s=="Internet Archive" else "Internet Archive"
      if (title,other) not in seen:seen.add((title,other));tasks.append((title,other))
  except Exception as e:
   detail=type(e).__name__+(" HTTP "+str(e.code) if hasattr(e,"code") else "")
   errors.append({"query":q,"source":s,"error":detail})
   print("Archive search failure:",s,detail,flush=True)
   follow(q,s)
  with lock:cases[name]={"case":name,"results":sorted(hits.values(),key=lambda x:-x["score"]),"run":{"time":time.time(),"fresh":len(hits),"errors":errors[:],"completed":done,"attempted":len(tasks),"status":"running"}}
 with lock:cases[name]["run"]["status"]="complete"
class Handler(BaseHTTPRequestHandler):
 def reply(self,status,obj):
  self.send_response(status);self.send_header("Content-Type","application/json");self.send_header("Access-Control-Allow-Origin",ORIGIN);self.send_header("Access-Control-Allow-Headers","Authorization, Content-Type");self.send_header("Access-Control-Allow-Methods","GET, POST, OPTIONS");self.send_header("Cache-Control","no-store");self.end_headers();self.wfile.write(json.dumps(obj).encode())
 def do_OPTIONS(self):self.reply(200,{})
 def auth(self):
  if hmac.compare_digest(self.headers.get("Authorization",""),"Bearer "+TOKEN):return True
  self.reply(401,{"error":"Unauthorized"});return False
 def do_GET(self):
  if not self.auth():return
  u=urllib.parse.urlsplit(self.path)
  if u.path=="/health":return self.reply(200,{"provider":"Public archive APIs","mode":"on-demand","email":False,"interval":0})
  if u.path!="/results":return self.reply(404,{})
  name=urllib.parse.parse_qs(u.query).get("case",[""])[0]
  with lock:result=json.loads(json.dumps(cases.get(name,{"case":name,"results":[],"run":None})))
  self.reply(200,result)
 def do_POST(self):
  if not self.auth():return
  if self.path!="/watch":return self.reply(404,{})
  try:
   n=int(self.headers.get("Content-Length","0"))
   if not 0<n<=100000:raise ValueError()
   j=json.loads(self.rfile.read(n));name=j["case"];qs=j["queries"]
   if not isinstance(name,str) or not 0<len(name)<=300 or not isinstance(qs,list) or len(qs)>50 or not all(isinstance(q,str) and 0<len(q)<=1000 for q in qs):raise ValueError()
   qs=list(dict.fromkeys(qs))
   with lock:
    if any(x.get("run",{}).get("status")=="running" for x in cases.values()):return self.reply(409,{"error":"Research already running"})
    if len(cases)>=20 and name not in cases:cases.pop(next(iter(cases)))
    cases[name]={"case":name,"results":[],"run":{"status":"running" if qs else "complete","time":time.time(),"fresh":0,"errors":[]}}
   if qs:pool.submit(run,name,qs)
   self.reply(200,{"saved":len(qs),"scheduled":min(4,len(qs)),"mode":"on-demand"})
  except Exception:self.reply(400,{"error":"Invalid watchlist"})
if __name__=="__main__":
 if len(TOKEN)<32 or not ORIGIN.startswith("https://"):raise RuntimeError("Configure ART_TOKEN and ART_ORIGIN")
 ThreadingHTTPServer(("0.0.0.0",int(os.environ.get("PORT","8080"))),Handler).serve_forever()
