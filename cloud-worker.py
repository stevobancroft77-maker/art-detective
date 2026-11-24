"""Run with Python 3 on a scheduled host; requires SEARCH_PROXY HTTPS URL.
Usage: python cloud-worker.py watchlist.json results.json
Proxy must accept q and return results or web.results. No credentials embedded.
"""
import json, os, sys, urllib.request, urllib.parse
from datetime import datetime, timezone
base=os.environ['SEARCH_PROXY']; u=urllib.parse.urlsplit(base)
if u.scheme!='https': raise ValueError('HTTPS proxy required')
with open(sys.argv[1]) as f: watch=json.load(f)
results=[]; errors=[]
for q in watch['queries'][:50]:
    try:
        url=urllib.parse.urlunsplit(u._replace(query=urllib.parse.urlencode(urllib.parse.parse_qsl(u.query)+[('q',q)])))
        with urllib.request.urlopen(url,timeout=20) as r: j=json.load(r)
        arr=j.get('results',j.get('web',{}).get('results'))
        if not isinstance(arr,list): raise ValueError('Invalid response')
        for x in arr[:50]:
            if isinstance(x,dict) and urllib.parse.urlsplit(str(x.get('url',''))).scheme in ('http','https'): results.append(dict(url=x['url'],title=x.get('title',''),description=x.get('description',''),query=q))
    except Exception as e: errors.append({'query':q,'error':str(e)})
with open(sys.argv[2],'w') as f: json.dump(dict(case=watch['case'],results=results,errors=errors,time=datetime.now(timezone.utc).isoformat()),f,indent=2)
