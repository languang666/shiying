import asyncio, hashlib, hmac, json, os, re, secrets, time, sys, signal
from media_worker import classify, PLATFORMS
from pathlib import Path
from contextlib import asynccontextmanager, suppress
from urllib.parse import urlsplit, urljoin, quote
import httpx
import redis.asyncio as redis
import logging
from state_store import StateStore
from runtime import RuntimeLease, DiskBudget, DiskCapacityError, JobManager, event, error_code
from archive_stream import stream_zip
from youtube_control import YouTubeControl
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, StreamingResponse, Response
from pydantic import BaseModel, Field

@asynccontextmanager
async def lifespan(app):
 app.state.ready=False
 app.state.redis=redis.from_url(os.environ['SITE_REDIS_URL'],decode_responses=True,socket_timeout=5)
 await app.state.redis.ping()
 lease=RuntimeLease(app.state.redis,state.prefix+':runtime-lock')
 await lease.acquire()
 app.state.backend_client=httpx.AsyncClient(timeout=40,trust_env=False)
 app.state.media_client=httpx.AsyncClient(timeout=httpx.Timeout(60,connect=5),trust_env=False,limits=httpx.Limits(max_connections=32,max_keepalive_connections=16))
 app.state.monitors={}
 app.state.disk=DiskBudget(ROOT,jobs)
 app.state.manager=JobManager(jobs,persist,app.state.redis,state.prefix)
 helpers=[]
 try:
  await state.load(app.state.redis,os.environ.get('SITE_STATE_LEGACY_KEY','media-site:state:v1'))
  await app.state.disk.clean_expired();tidy()
  for jid,j in list(jobs.items()):
   if j.get('state') in TERMINAL:continue
   j['state']='queued';queue_job(jid)
  async def checkpoint():
   while True:
    await state.wake.wait();await asyncio.sleep(0.1)
    try:await persist()
    except Exception as error:
     event('state.checkpoint_failed',detail=str(error));await asyncio.sleep(1)
  async def cleaner():
   while True:
    await asyncio.sleep(60)
    await app.state.disk.clean_expired();tidy();await persist()
  async def heartbeat():
   while True:
    await asyncio.sleep(5)
    try:await lease.renew()
    except Exception as error:
     app.state.ready=False;event('runtime.lease_lost',detail=str(error))
     await app.state.manager.close()
     os.kill(os.getpid(),signal.SIGTERM);return
  helpers=[asyncio.create_task(checkpoint()),asyncio.create_task(cleaner()),asyncio.create_task(heartbeat())]
  app.state.ready=True
  event('runtime.ready',download_slots=DOWNLOAD_CONCURRENCY,parse_slots=PARSE_CONCURRENCY,disk_quota=app.state.disk.quota)
  yield
 finally:
  app.state.ready=False
  for task in helpers:task.cancel()
  await asyncio.gather(*helpers,return_exceptions=True)
  await app.state.manager.close()
  try:await persist()
  finally:
   with suppress(Exception):await lease.release()
   await app.state.media_client.aclose();await app.state.backend_client.aclose();await app.state.redis.aclose()

app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None,lifespan=lifespan)
BASE=os.environ.get('BACKEND_URL','http://api:8000')
KEY=os.environ['BACKEND_KEY']; SECRET=os.environ['SESSION_SECRET'].encode()
# Public access; signed anonymous cookies isolate each visitor's tasks.
ROOT=Path('/data');ROOT.mkdir(exist_ok=True)
UA='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
TERMINAL={'done','partial','failed','cancelled'}
job_events={}
def state_changed(table,key):
 if table=='jobs':job_events.setdefault(key,asyncio.Event()).set()
state=StateStore(os.environ.get('SITE_STATE_PREFIX','media-site:v2'),state_changed)
jobs=state.tables['jobs'];contents=state.tables['contents'];limits=state.tables['limits']
PARSE_CONCURRENCY=int(os.environ.get('SITE_PARSE_CONCURRENCY','2'));DOWNLOAD_CONCURRENCY=int(os.environ.get('SITE_DOWNLOAD_CONCURRENCY','2'))
parse_slots=asyncio.Semaphore(PARSE_CONCURRENCY);download_slots=asyncio.Semaphore(DOWNLOAD_CONCURRENCY)

async def persist():
 return await state.flush()

@asynccontextmanager
async def work_slot(action,job_id):
 async with (parse_slots if action=='parse' else download_slots):
  app.state.manager.processing_started(job_id)
  if action=='download':
   async with app.state.disk.reservation(job_id):yield
  else:yield

def queue_job(job_id):
 j=jobs[job_id];resume=j.get('resume') or []
 if resume and resume[0] in ('parse','download'):
  return app.state.manager.start(job_id,multi_worker,*resume)
 if resume and resume[0]=='tiktok-download':
  return app.state.manager.start(job_id,tiktok_download,resume[1])
 if j.get('download_id') or (resume and resume[0]=='core-download'):
  return app.state.manager.start(job_id,core_download)
 if j.get('task_id') or (resume and resume[0]=='core-parse'):
  return app.state.manager.start(job_id,core_parse)
 j.update(state='failed',error='旧任务缺少恢复信息，请重新解析',error_code='LEGACY_JOB')

def sign(value):return hmac.new(SECRET,value.encode(),hashlib.sha256).hexdigest()
def session(request):
 return request.state.media_session

def valid_session(token):
 try:
  value,sig=token.rsplit('.',1)
  return int(value.split('.')[0])>time.time() and hmac.compare_digest(sign(value),sig)
 except (ValueError,IndexError):return False

def same_origin(request):
 origin=request.headers.get('origin')
 if origin and urlsplit(origin).netloc!=request.headers.get('host'):raise HTTPException(403,'请求来源无效')
def rate(k,n=5,window=60):
 now=time.time();times=[t for t in limits.get(k,[]) if now-t<window]
 if len(times)>=n:raise HTTPException(429,'操作过于频繁，请稍后重试')
 limits[k]=times+[now]
def tidy():
 now=time.time()
 for k in list(jobs):
  if now-jobs[k].get('finished_at',jobs[k]['time'])>3600 and jobs[k].get('state') in TERMINAL and not app.state.disk.readers.get(k):
   jobs.pop(k,None);job_events.pop(k,None)
 for k in list(contents):
  if now-contents[k]['time']>3600:contents.pop(k,None)
 for k in list(limits):
  limits[k]=[t for t in limits[k] if now-t<600]
  if not limits[k]:limits.pop(k,None)
def owned(job_id,s):
 j=jobs.get(job_id)
 if not j or j['owner']!=s:raise HTTPException(404,'结果已过期，请重新解析')
 return j
async def backend(path,body=None):
 if True:
  c=app.state.backend_client
  r=await c.request('POST' if body is not None else 'GET',BASE+path,json=body,headers={'X-API-Key':KEY})
  if r.headers.get('x-ratelimit-limit'):
   app.state.backend_budget={k:r.headers.get('x-ratelimit-'+k) for k in ('limit','remaining','reset')}
  if r.status_code==429:raise HTTPException(429,'解析通道暂时繁忙，请稍后重试')
  try:d=r.json()
  except ValueError:raise HTTPException(502,'解析服务暂时不可用')
  if not d.get('success'):
   code=(d.get('error') or {}).get('code','')
   hints={'NOT_FOUND':'作品无法获取：可能已删除、设为私密、属于限时动态，或平台暂时不允许访问','UPSTREAM_RISK_CONTROL':'平台暂时限制了访问，请稍后重试','IDENTITY_POOL_EXHAUSTED':'解析通道繁忙，请稍后重试','INVALID_URL':'链接无法识别，请粘贴作品的分享链接','UNSUPPORTED_CONTENT':'暂不支持这种链接（仅支持单条作品）'}
   raise HTTPException(502, hints.get(code,'解析失败，请稍后重试'))
  return d['data']
def present(data,s):
 if not data or 'media' not in data:raise HTTPException(502,'暂时无法获取作品，请稍后重试')
 cid=secrets.token_urlsafe(20);contents[cid]={'data':data,'owner':s,'time':time.time()}
 v=data.get('media',{}).get('video') or {}
 return {'result_id':cid,'title':data.get('title') or '未命名作品','author':data.get('author',{}).get('nickname',''),'platform':data['platform'],'kind':data.get('kind'),'videos':len(data.get('media',{}).get('videos') or ([v] if v else [])),'width':v.get('width'),'height':v.get('height'),'duration':round((data.get('duration_ms') or 0)/1000),'images':len(data.get('media',{}).get('images') or []),'watermark':v.get('watermark')}
class Login(BaseModel):password:str=Field(max_length=128)
class Parse(BaseModel):text:str=Field(min_length=1,max_length=4096)
class Download(BaseModel):result_id:str=Field(max_length=80)
@app.middleware('http')
async def security(request,call_next):
 if not getattr(app.state,'ready',False):return JSONResponse({'detail':'服务正在恢复任务，请稍后重试'},status_code=503)
 token=request.cookies.get('media_session','')
 fresh=not valid_session(token)
 if fresh:
  value=str(int(time.time()+7*86400))+'.'+secrets.token_urlsafe(16)
  token=value+'.'+sign(value)
 request.state.media_session=token
 response=await call_next(request)
 if request.method=='POST' and request.url.path.startswith('/api/'):
  await persist()
 if fresh and not request.url.path.startswith('/static/'):response.set_cookie('media_session',token,httponly=True,secure=request.headers.get('x-forwarded-proto')=='https',samesite='strict',max_age=7*86400)
 response.headers['Cache-Control']='public, max-age=31536000, immutable' if request.url.path.startswith('/static/') else 'no-store'
 response.headers['X-Robots-Tag']='noindex, nofollow, noarchive, nosnippet, noimageindex'
 response.headers['X-Content-Type-Options']='nosniff'
 response.headers['Referrer-Policy']='no-referrer'
 response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; media-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
 return response
@app.api_route('/',methods=['GET','HEAD'],response_class=HTMLResponse)
async def home():return Path('/site/index.html').read_text()
@app.get('/healthz')
async def health():
 await app.state.redis.ping()
 return {'ok':True}
@app.get('/robots.txt')
async def robots():return HTMLResponse('User-agent: *\nDisallow: /\n',media_type='text/plain')
@app.get('/api/session')
async def authcheck(request:Request):session(request);return {'ok':True}
@app.post('/api/logout')
async def logout(request:Request):
 same_origin(request);r=JSONResponse({'ok':True});r.delete_cookie('media_session');return r
@app.post('/api/parse')
async def parse(body:Parse,request:Request):
 s=session(request);same_origin(request);rate('parse:'+request.headers.get('x-real-ip',request.client.host));rate('parse-global',30)
 if len(contents)>500 or len(jobs)>500:raise HTTPException(429,'当前任务较多，请稍后重试')
 try:platform,url=classify(body.text)
 except ValueError as e:raise HTTPException(400,str(e))
 if platform in PLATFORMS:url=await expand_share(platform,url)
 if platform=='youtube':check_youtube_cooldown()
 j=secrets.token_urlsafe(20)
 resume=['parse',platform,url] if platform in PLATFORMS else ['core-parse',platform,url]
 jobs[j]={'owner':s,'time':time.time(),'type':'parse','state':'queued','platform':platform,'resume':resume}
 await persist();queue_job(j)
 return {'job':j}

async def wait_change(job_id,after,wait):
 event=job_events.setdefault(job_id,asyncio.Event())
 deadline=asyncio.get_running_loop().time()+max(0,min(20,wait))
 while True:
  event.clear()
  j=jobs.get(job_id)
  if j is None:return
  if job_version(j)!=after or j.get('state') in TERMINAL:return
  remaining=deadline-asyncio.get_running_loop().time()
  if remaining<=0:return
  try:await asyncio.wait_for(event.wait(),remaining)
  except asyncio.TimeoutError:return

def job_version(j):
 return hashlib.sha256(json.dumps([j.get('state'),j.get('files',[]),j.get('error'),bool(j.get('data'))],ensure_ascii=False,separators=(',',':')).encode()).hexdigest()[:16]

def ensure_monitor(job_id):
 return queue_job(job_id)

async def core_parse(job_id):
 j=jobs[job_id]
 async with work_slot('parse',job_id):
  j['state']='running'
  if not j.get('task_id'):
   d=await backend('/api/v1/parse?wait=30',{'url':j['resume'][2]})
   if d.get('task_id'):
    j['task_id']=d['task_id'];await persist()
   else:
    j.update(state='done',data=d);return
  app.state.monitors[job_id]=asyncio.current_task()
  try:await monitor_backend(job_id)
  finally:app.state.monitors.pop(job_id,None)

async def core_download(job_id):
 j=jobs[job_id]
 async with work_slot('download',job_id):
  j['state']='running'
  if not j.get('download_id'):
   d=await backend('/api/v1/downloads',{'platform':j['platform'],'content_id':j['content_id'],'skip_existing':True})
   j['download_id']=d['download_id'];await persist()
  app.state.monitors[job_id]=asyncio.current_task()
  try:await monitor_backend(job_id)
  finally:app.state.monitors.pop(job_id,None)

def monitor_interval(started):
 base=0.3 if time.monotonic()-started<15 else 1.0
 budget=getattr(app.state,'backend_budget',{})
 try:
  limit=float(budget.get('limit') or 180)
  remaining=float(budget.get('remaining') or 0)
  seconds=max(1,float(budget.get('reset') or time.time()+60)-time.time())
  if limit<=0:return base
  allowed=min(limit*0.6/60,max(0.1,remaining*0.8/seconds))
  return max(base,len(app.state.monitors)/max(0.1,allowed))
 except (ValueError,TypeError):return max(base,0.6*len(app.state.monitors))

async def monitor_backend(job_id):
 started=time.monotonic();errors=0
 while job_id in jobs:
  j=jobs[job_id]
  if j.get('state') in TERMINAL:return
  try:
   if 'download_id' in j:
    d=await backend('/api/v1/downloads/'+j['download_id'])
    j.update({'state':d['state'],'files':d.get('files') or [],'error':d.get('error')})
   elif j.get('task_id'):
    d=await backend('/api/v1/tasks/'+j['task_id'])
    if d.get('state') in ('failed','cancelled'):
     j.update({'state':'failed','error':'解析失败，请稍后重试'})
    elif isinstance(d.get('result'),dict) and 'media' in d['result']:
     j.update({'state':'done','data':d['result']})
   errors=0
  except asyncio.CancelledError:raise
  except HTTPException as error:
   if error.status_code==429:
    reset=getattr(app.state,'backend_budget',{}).get('reset')
    await asyncio.sleep(max(1,min(60,float(reset or time.time()+5)-time.time())))
    continue
   errors+=1
  except Exception:
   errors+=1
   if errors>=30:
    j.update({'state':'failed','error':'任务状态暂时无法获取，请稍后重试'})
    return
  if j.get('state') in TERMINAL:
   await persist();return
  await asyncio.sleep(monitor_interval(started))

@app.get('/api/parse/{job_id}')
async def parse_status(job_id:str,request:Request,wait:float=0):
 s=session(request);j=owned(job_id,s)
 if j['type']!='parse':raise HTTPException(400)
 if j.get('task_id') and j.get('state') not in TERMINAL:ensure_monitor(job_id)
 if j.get('state') not in TERMINAL and wait>0:
  deadline=asyncio.get_running_loop().time()+min(wait,20)
  while j.get('state') not in TERMINAL and asyncio.get_running_loop().time()<deadline:
   await wait_change(job_id,job_version(j),deadline-asyncio.get_running_loop().time())
 if j.get('state') in ('failed','cancelled'):
  remaining=max(0,int(j.get('retry_at',0)-time.time()+0.999))
  raise HTTPException(429 if remaining else 502,j.get('error','解析失败'),headers={'Retry-After':str(remaining)} if remaining else None)
 if j.get('state')=='done' and j.get('data'):
  if not j.get('view'):j['view']=present(j['data'],s)
  await persist()
  return j['view']
 return {'pending':True}
@app.post('/api/download')
async def download(body:Download,request:Request):
 s=session(request);same_origin(request);rate('download:'+request.headers.get('x-real-ip',request.client.host));rate('download-global',20);c=contents.get(body.result_id)
 if not c or c['owner']!=s:raise HTTPException(404,'结果已过期，请重新解析')
 existing=c.get('download_job')
 if existing and existing in jobs and jobs[existing].get('state') in ('queued','running','done','partial'):return {'job':existing}
 d=c['data']
 if d['platform']=='youtube':check_youtube_cooldown()
 j=secrets.token_urlsafe(20);jobs[j]={'owner':s,'time':time.time(),'type':'download','platform':d['platform'],'content_id':d['content_id'],'title':d.get('title',''),'author':d.get('author',{}).get('nickname',''),'state':'queued','files':[]}
 c['download_job']=j
 if d.get('engine')=='multi':
  jobs[j].update(engine='multi',resume=['download',d['platform'],d['source_url']])
 elif d['platform']=='tiktok':jobs[j]['resume']=['tiktok-download',d]
 else:jobs[j]['resume']=['core-download',d['platform'],d['content_id']]
 await persist();queue_job(j)
 return {'job':j}
@app.get('/api/download/{job_id}')
async def download_status(job_id:str,request:Request,wait:float=0,after:str=''):
 j=owned(job_id,session(request))
 if j['type']!='download':raise HTTPException(400)
 if 'download_id' in j and j.get('state') not in TERMINAL:ensure_monitor(job_id)
 if wait>0 and after:await wait_change(job_id,after,wait)
 ready={kind:sum(f.get('kind')==kind and f.get('state')=='done' for f in j['files']) for kind in ZIP_KINDS}
 zip_url='/api/download/'+job_id+'/zip' if j['state'] in ('done','partial') and max(ready.values())>1 else None
 return {'state':j['state'],'error':j.get('error'),'error_code':j.get('error_code'),'retry_after':max(0,int(j.get('retry_at',0)-time.time()+0.999)),'zip_url':zip_url,'version':job_version(j),'files':[{'name':f['name'],'kind':f['kind'],'bytes':f.get('bytes',0),'state':f['state'],'error':f.get('error'),'url':'/api/file/'+job_id+'/'+quote(f['name'])} for f in j['files']]}
@app.get('/api/file/{job_id}/{name}')
async def file(job_id:str,name:str,request:Request,preview:bool=False,fn:str=None):
 j=owned(job_id,session(request));f=next((f for f in j['files'] if f['name']==name and f['state']=='done'),None)
 if not f:raise HTTPException(404,'文件尚未准备好')
 ext=os.path.splitext(name)[1] or ('.jpg' if f['kind'] in ('image','cover') else '.mp4')
 if fn:
  safe_name=re.sub(r'[\\/:*?"<>|\r\n\t]','_',fn).strip()
  if not safe_name.lower().endswith(ext.lower()):safe_name+=ext
 else:
  clean_title=re.sub(r'[\\/:*?"<>|\r\n\t]','_',j.get('title','')).strip()[:30] or '作品'
  clean_author=re.sub(r'[\\/:*?"<>|\r\n\t]','_',j.get('author','')).strip()[:20]
  time_tag=time.strftime('%Y%m%d_%H%M%S')
  rand_tag=secrets.token_hex(2)
  cover_tag='封面' if f['kind']=='cover' else None
  parts=[p for p in [j['platform'],clean_author,clean_title,cover_tag,time_tag,rand_tag] if p]
  safe_name='_'.join(parts)+ext
 encoded_name=quote(safe_name)
 ascii_name=re.sub(r'[^a-zA-Z0-9_.-]','_',safe_name) or 'download'+ext
 disp=f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{encoded_name}'
 if 'local_name' in f:
  if not (ROOT/f['local_name']).is_file():raise HTTPException(404,'下载缓存已清理，请重新解析')
  app.state.disk.protect(job_id)
  resp=FileResponse(ROOT/f['local_name'],media_type=f['content_type'],background=BackgroundTask(app.state.disk.release_reader,job_id))
  if not preview:resp.headers['Content-Disposition']=disp
  return resp
 client=app.state.media_client;headers={'X-API-Key':KEY}
 if request.headers.get('range'):headers['Range']=request.headers['range']
 r=await client.send(client.build_request('GET',BASE+'/api/v1/downloads/'+j['download_id']+'/files/'+quote(name),headers=headers),stream=True)
 if r.status_code not in [200,206]:await r.aclose();raise HTTPException(502,'文件读取失败，请重新解析')
 async def chunks():
  try:
   async for b in r.aiter_bytes(chunk_size=65536):yield b
  finally:await r.aclose()
 out={k:v for k,v in r.headers.items() if k in ['content-length','content-type','content-range','accept-ranges']}
 if not preview:out['Content-Disposition']=disp
 return StreamingResponse(chunks(),status_code=r.status_code,headers=out)

ALLOWED=('tiktok.com','tiktokcdn.com','tiktokcdn-eu.com','tiktokcdn-us.com','tiktokv.com','byteoversea.com','ibytedtos.com','ibyteimg.com','muscdn.com','bytecdn.cn','akamaized.net')
def vet(url):
 u=urlsplit(url);h=u.hostname or ''
 if u.scheme!='https' or u.username or u.password or u.port not in [None,443] or not any(h==d or h.endswith('.'+d) for d in ALLOWED):raise ValueError('媒体地址不可用')
async def fetch(c,url,**kw):
 for _ in range(6):
  vet(url);r=await c.send(c.build_request('GET',url,**kw),stream=True)
  if r.status_code in [301,302,303,307,308]:
   target=urljoin(url,r.headers.get('location',''));await r.aclose();url=target;continue
  try:r.raise_for_status()
  except Exception:
   await r.aclose();raise
  return r
 raise ValueError('跳转次数过多')
async def tiktok_download(job_id,data):
 j=jobs[job_id]
 try:
  async with work_slot('download',job_id):
   j['state']='running'
   async with httpx.AsyncClient(timeout=45,trust_env=False,headers={'User-Agent':UA,'Referer':'https://www.tiktok.com/'}) as c:
    sources=[]
    images=data.get('media',{}).get('images') or []
    if images:
     for n,image in enumerate(images):
      candidates=list(dict.fromkeys(u for u in [image.get('url')]+list(image.get('urls') or []) if isinstance(u,str) and u))
      if not candidates:raise ValueError('图集图片地址不可用，请重新解析')
      sources.append((f'image-{n+1}.jpg','image',candidates))
    else:
     r=await fetch(c,data['web_url']);raw=await r.aread();await r.aclose()
     m=re.search(r'<script[^>]+id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',raw.decode(),re.S)
     if not m:raise ValueError('TikTok 暂时限制访问，请稍后重试')
     item=json.loads(m[1])['__DEFAULT_SCOPE__']['webapp.video-detail']['itemInfo']['itemStruct']
     if item.get('imagePost'):
      for n,image in enumerate(item['imagePost'].get('images',[])):
       candidates=image.get('imageURL',{}).get('urlList') or []
       if not candidates:raise ValueError('图集图片地址不可用，请重新解析')
       sources.append((f'image-{n+1}.jpg','image',candidates))
     else:
      u=item.get('video',{}).get('playAddr')
      if u:sources.append(('video.mp4','video',u))
      cover=item.get('video',{}).get('cover')
      if cover:sources.append(('cover.jpg','cover',cover))
    if not sources:raise ValueError('未获取到可下载文件')
    if len(sources)>50:raise ValueError('图集图片数量超过限制')
    slots=asyncio.Semaphore(int(os.environ.get('SITE_MEDIA_ITEM_WORKERS','4')))
    previous={f['name']:dict(f) for f in j.get('files',[]) if f.get('state')=='done' and f.get('local_name') and (ROOT/f['local_name']).is_file() and (ROOT/f['local_name']).stat().st_size==f.get('bytes')}
    j['files']=[previous.get(name) or {'name':name,'kind':kind,'state':'queued','bytes':0} for name,kind,url in sources]
    async def transfer(index,source):
     async with slots:
      await transfer_one(index,source)
    downloaded_total=sum(f.get('bytes',0) for f in j['files'] if f['state']=='done')
    async def transfer_one(index,source):
     nonlocal downloaded_total
     if j['files'][index]['state']=='done':return
     name,kind,url=source
     local=job_id+'-'+name;part=ROOT/(local+'.part');target=ROOT/local;resp=None
     try:
      candidates=url if isinstance(url,list) else [url]
      for candidate in candidates[:4]:
       try:
        resp=await fetch(c,candidate);break
       except (httpx.HTTPError,ValueError):
        if candidate==candidates[:4][-1]:raise
      if resp is None:raise ValueError('图片地址不可用，请重新解析')
      ctype=resp.headers.get('content-type','').split(';')[0].lower()
      if kind in ('image','cover') and not ctype.startswith('image/'):raise ValueError('平台没有返回图片文件')
      if not (ctype.startswith('video/') or ctype.startswith('image/') or ctype=='application/octet-stream'):raise ValueError('平台没有返回媒体文件')
      if kind in ('image','cover'):
       extension={'image/jpeg':'.jpg','image/png':'.png','image/webp':'.webp','image/avif':'.avif','image/gif':'.gif'}.get(ctype,'.jpg')
       name=os.path.splitext(name)[0]+extension;local=job_id+'-'+name;part=ROOT/(local+'.part');target=ROOT/local
      if int(resp.headers.get('content-length','0'))>2*1024*1024*1024:raise ValueError('文件超过 2 GB 限制')
      size=0
      with part.open('wb') as out:
       async for b in resp.aiter_bytes():
        size+=len(b);downloaded_total+=len(b)
        if downloaded_total>2*1024*1024*1024:raise ValueError('作品文件总量超过 2 GB 限制')
        out.write(b)
      if not size:raise ValueError('平台返回空文件')
      part.rename(target);j['files'][index]={'name':name,'kind':kind,'bytes':size,'state':'done','content_type':ctype,'local_name':local}
     except asyncio.CancelledError:
      part.unlink(missing_ok=True);raise
     except Exception as error:
      code=error_code(error);event('media.file_failed',job=job_id,platform='tiktok',file=name,code=code,detail=str(error))
      part.unlink(missing_ok=True);j['files'][index]={'name':name,'kind':kind,'state':'failed','bytes':0,'error':'文件下载失败，请稍后重试','error_code':code}
     finally:
      if resp:await resp.aclose()
    await asyncio.gather(*(transfer(index,source) for index,source in enumerate(sources)))
   j['state']='done' if all(f['state']=='done' for f in j['files']) else ('partial' if any(f['state']=='done' for f in j['files']) else 'failed')
   if j['state']=='failed':j['error']='TikTok 文件下载失败，请重新解析后重试'
 except DiskCapacityError:raise
 except Exception as error:
  j['state']='failed';j['error']='TikTok 下载失败，请稍后重新解析';j['error_code']=error_code(error)
  event('media.tiktok_failed',job=job_id,code=j['error_code'],detail=str(error))

def check_youtube_cooldown():
 remaining=YouTubeControl().remaining()
 if remaining:raise HTTPException(429,f'YouTube 暂时限制访问，请在 {remaining} 秒后重试',headers={'Retry-After':str(remaining)})

async def worker_events(stderr,job_id):
 for line in stderr.decode(errors='replace').splitlines():
  try:record=json.loads(line)
  except ValueError:continue
  name=record.get('event')
  if name not in ('media.youtube.route','media.youtube.cache_hit','media.youtube.cooldown'):continue
  fields={k:record[k] for k in ('client','action','outcome','height','reason','seconds') if k in record}
  event(name,job=job_id,**fields)
  try:
   key=state.prefix+':metrics:youtube:routes'
   field=':'.join(str(fields.get(k,'none')) for k in ('client','action','outcome')) if name.endswith('.route') else name.rsplit('.',1)[-1]
   pipe=app.state.redis.pipeline(transaction=False)
   pipe.hincrby(key,field,1);pipe.expire(key,30*86400);await pipe.execute()
  except Exception as error:event('metrics.write_failed',detail=str(error))

async def multi_worker(job_id,action,platform,url):
 j=jobs[job_id];proc=None;progress_task=None
 progress=ROOT/(job_id+'.progress.json')
 j['resume']=[action,platform,url]
 await persist()
 try:
  async with work_slot(action,job_id):
   j['state']='running'
   args=[sys.executable,'/site/media_worker.py',action,platform,url]
   if action=='download':args.append(job_id)
   environment=os.environ.copy()
   if platform=='youtube':environment['PYTHONPATH']=os.environ.get('SITE_YOUTUBE_PYTHONPATH','/site')
   proc=await asyncio.create_subprocess_exec(*args,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,start_new_session=True,env=environment)
   if action=='download' and platform in ('instagram','twitter'):
    async def publish_files():
     while proc.returncode is None:
      try:
       ready=json.loads(progress.read_text())
       if isinstance(ready,list):j['files']=ready
      except (OSError,ValueError):pass
      await asyncio.sleep(0.2)
    progress_task=asyncio.create_task(publish_files())
   stdout,stderr=await asyncio.wait_for(proc.communicate(),timeout=90 if action=='parse' else 3600)
   await worker_events(stderr,job_id)
   result=json.loads(stdout.decode().strip().splitlines()[-1])
   if not result.get('ok'):
    logging.warning('media.worker.failed platform=%s action=%s detail=%s',platform,action,stderr.decode(errors='replace')[-1000:])
    j.update(state='failed',error=result.get('error','平台暂时无法解析'),error_code=result.get('error_code'),retry_at=time.time()+result.get('retry_after',0));return
   if action=='parse':j['data']=result['data']
   else:j['files']=result['data']
   j['state']='done' if action=='parse' or all(f.get('state')=='done' for f in j['files']) else ('partial' if any(f.get('state')=='done' for f in j['files']) else 'failed')
 except asyncio.CancelledError:
  if proc and proc.returncode is None:
   import signal
   with suppress(ProcessLookupError):os.killpg(proc.pid,signal.SIGKILL)
   await proc.wait()
  raise
 except asyncio.TimeoutError:
  j['state']='failed';j['error']='平台响应超时，请稍后重试'
  if proc and proc.returncode is None:
   import signal
   os.killpg(proc.pid,signal.SIGKILL);await proc.wait()
  for f in ROOT.glob(job_id+'.*'):f.unlink(missing_ok=True)
 except DiskCapacityError:raise
 except Exception:
  logging.exception('media.worker.crashed platform=%s action=%s',platform,action)
  j['state']='failed';j['error']='平台暂时无法解析，请稍后重试'
 finally:
  if progress_task:
   progress_task.cancel()
   with suppress(asyncio.CancelledError):await progress_task
  progress.unlink(missing_ok=True);progress.with_suffix('.tmp').unlink(missing_ok=True)
  await persist()

async def expand_share(platform,url):
 short=('t.co','fb.watch')
 if (urlsplit(url).hostname or '') not in short:return url
 try:
  async with httpx.AsyncClient(timeout=15,trust_env=False,headers={'User-Agent':UA}) as client:
   for _ in range(6):
    p,checked=classify(url)
    if p!=platform:raise ValueError('短链接跳转到了其他平台，请使用作品的完整链接')
    response=await client.send(client.build_request('GET',checked),stream=True)
    target=response.headers.get('location')
    is_redirect=response.status_code in [301,302,303,307,308]
    await response.aclose()
    if not is_redirect:return checked
    if not target:raise ValueError('分享短链接无效')
    url=urljoin(checked,target)
  raise ValueError('分享链接跳转次数过多')
 except ValueError as e:raise HTTPException(400,str(e))
 except Exception:raise HTTPException(502,'分享短链接暂时无法打开，请使用完整作品链接')


ZIP_KINDS=('image','video')

@app.get('/api/download/{job_id}/zip')
async def media_zip(job_id:str,request:Request,fn:str=None,kind:str='image'):
 j=owned(job_id,session(request))
 if kind not in ZIP_KINDS:raise HTTPException(400,'不支持的打包类型')
 label='图片' if kind=='image' else '视频'
 if j.get('state') not in ('done','partial'):raise HTTPException(409,'请等待'+label+'准备完成')
 images=[dict(f) for f in j.get('files',[]) if f.get('kind')==kind and f.get('state')=='done']
 if not images:raise HTTPException(404,'没有已就绪'+label)
 rate('zip:'+request.headers.get('x-real-ip',request.client.host),n=5)
 if sum(app.state.disk.readers.values())>=8:raise HTTPException(429,'下载繁忙，请稍后重试')
 for index,item in enumerate(images):
  item['zip_name']=f"{kind}-{index+1:02d}"+os.path.splitext(item['name'])[1]
  if item.get('local_name') and not (ROOT/item['local_name']).is_file():raise HTTPException(404,'下载缓存已清理，请重新解析')
 title=re.sub(r'[\\/:*?"<>|\r\n\t]','_',fn or j.get('title') or ('图集' if kind=='image' else '视频合集')).strip()[:140]
 if not title.lower().endswith('.zip'):title+='.zip'
 async def source(item):
  if item.get('local_name'):
   with (ROOT/item['local_name']).open('rb') as file:
    while chunk:=await asyncio.to_thread(file.read,65536):yield chunk
  else:
   client=app.state.media_client
   async with client.stream('GET',BASE+'/api/v1/downloads/'+j['download_id']+'/files/'+quote(item['name']),headers={'X-API-Key':KEY}) as response:
    response.raise_for_status()
    async for chunk in response.aiter_bytes(65536):yield chunk
 async def chunks():
  try:
   async for chunk in stream_zip(images,source):yield chunk
  except Exception as error:
   event('zip.failed',job=job_id,code=error_code(error),detail=str(error));raise
  finally:app.state.disk.unprotect(job_id)
 app.state.disk.protect(job_id)
 return StreamingResponse(chunks(),media_type='application/zip',headers={'Content-Disposition':"attachment; filename="+kind+"s.zip; filename*=UTF-8''"+quote(title),'X-Accel-Buffering':'no'})

app.mount('/static',StaticFiles(directory='/site/static'),name='static')
