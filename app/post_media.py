"""Public single-post image/video bundles for Instagram and X."""
import concurrent.futures
import copy
import json
import os
import re
import subprocess
import threading
import httpx
import time
from pathlib import Path
from urllib.parse import urlsplit,urljoin
from yt_dlp.extractor.instagram import InstagramIE

MAX_BYTES=2*1024*1024*1024
MEDIA_DOMAINS={'instagram':('cdninstagram.com','fbcdn.net','instagram.com'),'twitter':('twimg.com',)}
REFERERS={'instagram':'https://www.instagram.com/','twitter':'https://x.com/'}
# Each platform accepts only links that name one post.
POST_PATHS={
 'instagram':(r'/(?:[^/]+/)?(?:p|reel|reels|tv)/[A-Za-z0-9_-]+/?','请提供 Instagram 单条帖子或 Reel 链接，不支持主页和限时动态'),
 'twitter':(r'/(?:[A-Za-z0-9_]{1,20}|i(?:/web)?)/status(?:es)?/[0-9]+(?:/(?:video|photo)/[1-9])?/?','请提供 X 单条推文链接，不支持主页、列表或搜索'),
}

class ImageInstagramIE(InstagramIE):
 @property
 def _can_impersonate(self):return False
 def _extract_product(self,product_info,*args,**kwargs):
  item=product_info[0] if isinstance(product_info,list) else product_info
  result=super()._extract_product(product_info,*args,**kwargs)
  result['_post_total']=int(item.get('carousel_media_count') or len(item.get('carousel_media_ids') or []) or len(item.get('carousel_media') or []) or 1)
  return result
 @classmethod
 def ie_key(cls):return 'Instagram'
 def _extract_product_media(self, product_media):
  data=super()._extract_product_media(product_media)
  data['_media_kind']='video' if product_media.get('media_type')==2 or product_media.get('video_versions') or product_media.get('video_dash_manifest') else 'image'
  return data

def vet_media(platform,address):
 from media_worker import public
 u=urlsplit(address);h=(u.hostname or '').lower()
 if u.scheme not in ('http','https') or u.username or u.password or u.port not in (None,80,443):raise ValueError('媒体地址无效')
 if not any(h==domain or h.endswith('.'+domain) for domain in MEDIA_DOMAINS[platform]):raise ValueError('媒体域名不在允许列表')
 public(address)
 return address

def extract(platform,url,force=False):
 import media_worker as w
 if platform not in POST_PATHS:raise ValueError('平台暂不支持')
 # The URL must name one post, never an account, topic or arbitrary playlist.
 u=urlsplit(url);pattern,message=POST_PATHS[platform]
 short=platform=='twitter' and (u.hostname or '').lower()=='t.co' and re.fullmatch(r'/[A-Za-z0-9]+/?',u.path)
 if not short and not re.fullmatch(pattern,u.path):raise ValueError(message)
 cached=None if force else w.cached_info(platform,url)
 if cached and cached.get('_post_bundle') and cached.get('post_items'):
  return cached
 options=w.opts(platform)
 # A /video/N link names one video of a post; anything else takes the whole post.
 single=platform=='twitter' and bool(re.search(r'/video/[1-9]/?$',u.path))
 options.update(ignore_no_formats_error=True,noplaylist=single,playlistend=None,extract_flat=False)
 with w.PublicYoutubeDL(options) as y:
  if platform=='instagram':y.add_info_extractor(ImageInstagramIE())
  raw=y.extract_info(url,download=False,process=False)
  if raw.get('_type') in ('url','url_transparent'):
   # Short links resolve once, and only to a post on the same platform.
   ep,eu=w.classify(raw['url'])
   if ep!=platform:raise ValueError('作品链接跳转到了其他平台')
   pattern,message=POST_PATHS[platform]
   if not re.fullmatch(pattern,urlsplit(eu).path):raise ValueError(message)
   raw=y.extract_info(eu,download=False,process=False)
  raw_entries=list(raw.get('entries') or [raw])
  if len(raw_entries)<int(raw.get('_post_total') or len(raw_entries)):raise ValueError('平台只返回了部分轮播内容，请使用完整分享链接后重试')
  if not raw_entries or len(raw_entries)>50:raise ValueError('仅支持最多 50 项媒体的单条作品')
  entries=[]
  for index,entry in enumerate(raw_entries):
   if entry.get('_type') in ('url','url_transparent'):
    # Resolve only entries belonging to the same platform.
    ep,eu=w.classify(entry['url'])
    if ep!=platform:raise ValueError('作品媒体跳转到了其他平台')
    entry=y.extract_info(eu,download=False,process=False)
   kind=entry.get('_media_kind') or ('video' if entry.get('formats') or entry.get('url') or entry.get('duration') else 'image')
   if kind=='video':
    w.validate(entry)
    if not entry.get('formats') and not entry.get('url'):raise ValueError('平台没有返回可用视频资源，可能需要登录')
    thumbs=entry.get('thumbnails') or ([{'url':entry['thumbnail']}] if entry.get('thumbnail') else [])
    thumbs=[t for t in thumbs if t.get('url')]
    cover_url=max(thumbs,key=lambda t:(int(t.get('width') or 0)*int(t.get('height') or 0)))['url'] if thumbs else None
    if cover_url:
     try:vet_media(platform,cover_url)
     except Exception:cover_url=None
    entries.append({'kind':'video','info':entry,'cover_url':cover_url,'position':index+1})
   else:
    thumbs=entry.get('thumbnails') or ([{'url':entry['thumbnail']}] if entry.get('thumbnail') else [])
    thumbs=[t for t in thumbs if t.get('url')]
    if not thumbs:raise ValueError('作品图片地址不可用')
    best=max(enumerate(thumbs),key=lambda p:(int(p[1].get('width') or 0)*int(p[1].get('height') or 0),p[0]))[1]
    vet_media(platform,best['url'])
    entries.append({'kind':'image','url':best['url'],'width':best.get('width'),'height':best.get('height'),'position':index+1})
  bundle={'_post_bundle':True,'id':raw.get('id') or raw_entries[0].get('id'),'title':raw.get('title') or raw.get('description') or raw_entries[0].get('title') or '未命名作品','uploader':raw.get('uploader') or raw.get('channel') or raw_entries[0].get('uploader') or '', 'post_items':entries}
  w.save_info(platform,url,bundle)
  return bundle

def parse(platform,url):
 bundle=extract(platform,url)
 images=[e for e in bundle['post_items'] if e['kind']=='image'];videos=[e for e in bundle['post_items'] if e['kind']=='video']
 video=(videos[0]['info'] if videos else {})
 if videos and not video.get('height'):
  # Unprocessed entries carry size only on their formats; report the largest.
  sized=[f for f in video.get('formats') or [] if f.get('height') and f.get('width')]
  if sized:
   best=max(sized,key=lambda f:f['height']);video={**video,'width':best['width'],'height':best['height']}
 return {'engine':'multi','source_url':url,'platform':platform,'content_id':str(bundle['id']),'title':bundle['title'],'author':{'nickname':bundle['uploader']},'kind':'mixed' if images and videos else ('images' if images else 'video'),'duration_ms':round(sum(float(e['info'].get('duration') or 0) for e in videos)*1000),'media':{'video':{'width':video.get('width'),'height':video.get('height'),'watermark':None} if videos else None,'videos':[{'position':e['position']} for e in videos],'images':[{'position':e['position'],'width':e.get('width'),'height':e.get('height')} for e in images]}}

def download(platform,url,prefix):
 import media_worker as w
 bundle=extract(platform,url)
 root=Path(os.environ.get('SITE_DATA_ROOT','/data'));progress=root/(prefix+'.progress.json')
 lock=threading.Lock();used=0
 items_to_run=[]
 for orig_idx,e in enumerate(bundle['post_items']):
  if e['kind']=='image':
   items_to_run.append({'kind':'image','position':e['position'],'url':e['url'],'item_index':orig_idx})
  else:
   items_to_run.append({'kind':'video','position':e['position'],'info':e['info'],'item_index':orig_idx})
   if e.get('cover_url'):
    items_to_run.append({'kind':'cover','position':e['position'],'url':e['cover_url'],'item_index':orig_idx})
 results=[{'name':(item['kind'])+'-'+str(item['position'])+('.jpg' if item['kind'] in ('image','cover') else '.mp4'),'kind':item['kind'],'state':'queued','bytes':0} for item in items_to_run]
 def report():
  temporary=progress.with_suffix('.tmp');temporary.write_text(json.dumps(results,ensure_ascii=False));temporary.replace(progress)
 def budget(amount):
  nonlocal used
  with lock:
   used+=amount
   if used>MAX_BYTES:raise ValueError('2 GB size limit')
 def finished(index,result):
  with lock:results[index]=result;report()
 with lock:report()
 def image(index,entry):
  response=None;partial=None
  try:
   # Inspect every redirect before connecting to the next address.
   with httpx.Client(timeout=20,trust_env=False,follow_redirects=False) as client:
    address=entry['url']
    for attempt in range(6):
     vet_media(platform,address)
     response=client.send(client.build_request('GET',address,headers={'Referer':REFERERS[platform],'User-Agent':'Mozilla/5.0'}),stream=True)
     if response.status_code in (301,302,303,307,308):
      target=urljoin(address,response.headers.get('location',''));response.close();address=target;continue
     response.raise_for_status();break
    else:raise ValueError('媒体地址跳转次数过多')
    ctype=response.headers.get('Content-Type','').split(';')[0].lower()
    ext={'image/jpeg':'.jpg','image/png':'.png','image/webp':'.webp','image/avif':'.avif','image/gif':'.gif'}.get(ctype)
    if not ext:raise ValueError('平台没有返回图片文件')
    name=(entry['kind'])+'-'+str(entry['position'])+ext;local=prefix+'.'+name;partial=root/(local+'.part');size=0
    with partial.open('wb') as out:
     for chunk in response.iter_bytes(65536):
      budget(len(chunk));out.write(chunk);size+=len(chunk)
    if not size:raise ValueError('平台返回空图片')
    partial.replace(root/local)
    finished(index,{'name':name,'kind':entry['kind'],'state':'done','bytes':size,'content_type':ctype,'local_name':local})
  except Exception as error:
   if partial:partial.unlink(missing_ok=True)
   finished(index,{**results[index],'state':'failed','error':w.friendly(error)})
  finally:
   if response:response.close()
 def video(index,entry):
  base=prefix+'.video-'+str(entry['position']);amount={}
  def hook(data):
   filename=data.get('filename')
   if filename:
    current=data.get('downloaded_bytes') or 0;old=amount.get(filename,0)
    if current>old:budget(current-old);amount[filename]=current
  options=w.opts(platform);options.update(outtmpl=str(root/base)+'.%(ext)s',progress_hooks=[hook],postprocessors=[{'key':'FFmpegVideoRemuxer','preferedformat':'mp4'}])
  try:
   info=copy.deepcopy(entry['info'])
   with w.PublicYoutubeDL(options) as y:
    try:y.process_ie_result(info,download=True)
    except w.yt_dlp.utils.DownloadError as error:
     if not any(code in str(error).lower() for code in ('403','410','expired','signature')):raise
     refreshed=extract(platform,url,force=True);replacement=refreshed['post_items'][entry['item_index']]
     if replacement['kind']!='video':raise ValueError('作品媒体类型发生变化')
     for f in root.glob(base+'.*'):f.unlink(missing_ok=True)
     y.process_ie_result(copy.deepcopy(replacement['info']),download=True)
   files=[f for f in root.glob(base+'.*') if f.suffix in ('.mp4','.mkv','.webm','.mov') and '.f' not in f.name[len(base):]]
   if not files:raise ValueError('No video downloaded')
   f=files[0];size=f.stat().st_size
   if not 0<size<=MAX_BYTES:raise ValueError('2 GB size limit')
   meta=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_entries','stream=codec_type:format=duration','-of','json',str(f)],timeout=15))
   if not any(s.get('codec_type')=='video' for s in meta.get('streams',[])):raise ValueError('No video stream')
   if float(meta.get('format',{}).get('duration') or 0)>3600:raise ValueError('Video exceeds 60 minutes')
   finished(index,{'name':'video-'+str(entry['position'])+f.suffix,'kind':'video','state':'done','bytes':size,'content_type':'video/mp4' if f.suffix=='.mp4' else 'video/webm','local_name':f.name})
  except Exception as error:
   for f in root.glob(base+'.*'):f.unlink(missing_ok=True)
   finished(index,{**results[index],'state':'failed','error':w.friendly(error)})
 # Images and video preparation progress independently, with a bounded total.
 with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
  futures=[pool.submit(image if e['kind'] in ('image','cover') else video,i,e) for i,e in enumerate(items_to_run)]
  for future in futures:future.result()
 if not any(r['state']=='done' for r in results):raise ValueError(next((r.get('error') for r in results if r.get('error')),'未获取到可下载媒体'))
 return results
