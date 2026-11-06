"""Public, single-post download worker. No generic URL extractor."""
import ipaddress,json,os,re,socket,subprocess,sys,hashlib,time,tempfile
from pathlib import Path
from urllib.parse import urlsplit,parse_qs,urlunsplit
import yt_dlp
from contextlib import nullcontext
from youtube_control import YouTubeControl, CooldownError

PLATFORMS={
 'youtube':('YouTube',('youtube.com','youtu.be'),['Youtube']),
 'twitter':('X / Twitter',('x.com','twitter.com','t.co'),['Twitter','TwitterCard','TwitterAmplify','TwitterShortener']),
 'instagram':('Instagram',('instagram.com',),['Instagram']),
 'weibo':('微博',('weibo.com','weibo.cn'),['Weibo','WeiboVideo']),
 'facebook':('Facebook',('facebook.com','fb.watch','fb.com'),['Facebook','FacebookReel','FacebookPluginsVideo']),
 'vimeo':('Vimeo',('vimeo.com',),['Vimeo']),
 'dailymotion':('Dailymotion',('dailymotion.com','dai.ly'),['Dailymotion']),
}
ALL_DOMAINS={p:v[1] for p,v in PLATFORMS.items()}
ALL_DOMAINS.update({'douyin':('douyin.com','iesdouyin.com'),'tiktok':('tiktok.com',)})
def classify(text):
 m=re.search(r'https?://[^\s<>"\u201c\u201d]+',text)
 if not m:raise ValueError('请粘贴有效的视频分享链接')
 url=m[0].rstrip('。，、！）)]};');u=urlsplit(url);host=(u.hostname or '').lower()
 if u.username or u.password or u.port not in [None,80,443]:raise ValueError('链接地址无效')
 for platform,domains in ALL_DOMAINS.items():
  if any(host==d or host.endswith('.'+d) for d in domains):
   if platform=='youtube':
    vid=u.path.strip('/').split('/')[0] if host=='youtu.be' else (parse_qs(u.query).get('v',[None])[0] if u.path=='/watch' else (u.path.split('/')[2] if re.match(r'^/(shorts|embed|live)/',u.path) else None))
    if not vid or not re.fullmatch(r'[\w-]{11}',vid):raise ValueError('请提供单条 YouTube 视频链接，不支持频道或播放列表')
    url='https://www.youtube.com/watch?v='+vid
   elif platform=='vimeo' and re.fullmatch(r'/[0-9]+/?',u.path):url=urlunsplit(('https','player.vimeo.com','/video/'+u.path.strip('/'),u.query,''))
   else:url=urlunsplit(('https',u.netloc,u.path,u.query,''))
   return platform,url
 raise ValueError('暂不支持这个网站，请使用页面列出的平台')
def public(url):
 u=urlsplit(url)
 if u.scheme not in ['http','https'] or not u.hostname or u.username or u.password:raise ValueError('不允许的请求地址')
 for item in socket.getaddrinfo(u.hostname,u.port or (443 if u.scheme=='https' else 80),type=socket.SOCK_STREAM):
  if not ipaddress.ip_address(item[4][0]).is_global:raise ValueError('不允许访问内网地址')
class PublicYoutubeDL(yt_dlp.YoutubeDL):
 def urlopen(self,req):
  url=req if isinstance(req,str) else getattr(req,'url',None) or req.get_full_url()
  provider=os.environ.get('SITE_YOUTUBE_POT_URL','').rstrip('/')
  if provider and url in (provider+'/ping',provider+'/get_pot'):
   return super().urlopen(req)
  public(url);return super().urlopen(req)
class Silent:
 def debug(self,msg):pass
 def warning(self,msg):pass
 def error(self,msg):pass

def friendly(error):
 if isinstance(error,CooldownError):return str(error)
 message=str(error)
 if isinstance(error,ValueError) and re.match(r'^(请提供|作品|平台|仅支持|媒体|没有)',message):return message[:180]
 s=message.lower()
 if 'drm' in s:return '该视频受 DRM 保护，无法提供下载；请尝试其他公开作品'
 if any(x in s for x in ('not available in your country','cannot be played in your region','geo restricted','geographic restriction')):return '该作品在当前服务器所在地区不可播放'
 if any(x in s for x in ('no suitable extractor','unsupported url')):return '该链接格式暂不支持，请使用单条作品的原始分享链接'
 if 'not a bot' in s or 'sign in to confirm' in s:return 'YouTube 暂时要求验证访问，请稍后重试'
 if any(x in s for x in ['cookies','login','log in','sign in','logged in','login_required']):return '此作品需要登录会话，当前服务器暂时无法获取；可尝试其他公开视频'
 if any(x in s for x in ['403','412','429','captcha','bot','blocked','risk']):return '平台暂时限制了服务器访问，请稍后重试或尝试其他作品'
 if any(x in s for x in ['private','not available','unavailable','not found','removed','deleted']):return '该作品不可访问，可能已删除、设为私密或受地区限制'
 if any(x in s for x in ['no video','only images','no formats','requested format']):return '没有获取到可下载的视频，图文帖子或受限清晰度可能无法解析'
 if any(x in s for x in ['2 gb','size limit','maxfilesize']):return '文件超过 2 GB 限制'
 if '60 minutes' in s:return '视频超过 1 小时限制'
 if 'playlist' in s:return '仅支持单条视频，不支持主页、合集或直播'
 return '平台暂时没有返回可下载视频，请稍后重试'

def extractor_patterns(platform):
 from yt_dlp.extractor import get_info_extractor
 return ['(?i:'+re.escape(key)+'|'+re.escape(get_info_extractor(key).IE_NAME)+')' for key in PLATFORMS[platform][2]]

def opts(platform):
 return {'quiet':True,'no_warnings':True,'logger':Silent(),'noplaylist':True,'playlistend':1,'extract_flat':False,'socket_timeout':20,'retries':1,'fragment_retries':1,'extractor_retries':1,'proxy':'','cachedir':False,'allowed_extractors':extractor_patterns(platform),'js_runtimes':{'node':{}},'format':'bv*[height<=1080][vcodec^=avc1]+ba[ext=m4a]/b[height<=1080][ext=mp4]/bv*[height<=1080]+ba/b[height<=1080]','merge_output_format':'mp4','max_filesize':2*1024*1024*1024,'concurrent_fragment_downloads':1,'overwrites':True,'extractor_args':{}}
def validate(info):
 if not info or info.get('_type') in ['playlist','multi_video'] or info.get('is_live'):raise ValueError('Playlist or live is not supported')
 if (info.get('duration') or 0)>3600:raise ValueError('Video exceeds 60 minutes')
 if (info.get('filesize') or info.get('filesize_approx') or 0)>2*1024*1024*1024:raise ValueError('2 GB size limit')
CACHE_TTL=120
CACHE_MAX_BYTES=8*1024*1024

def cache_path(platform,url):
 root=Path(os.environ.get('SITE_DATA_ROOT','/data'))/'.extract-cache'
 root.mkdir(mode=0o700,parents=True,exist_ok=True)
 return root/(hashlib.sha256((platform+'\n'+url).encode()).hexdigest()+'.json')

def cached_info(platform,url):
 path=cache_path(platform,url)
 try:
  if path.stat().st_size>CACHE_MAX_BYTES:return None
  saved=json.loads(path.read_text())
  if saved['platform']!=platform or saved['url']!=url or not 0<=time.time()-saved['time']<CACHE_TTL:return None
  validate(saved['info'])
  return saved['info']
 except (OSError,ValueError,KeyError,TypeError):return None

def save_info(platform,url,info):
 path=cache_path(platform,url)
 raw=json.dumps({'time':time.time(),'platform':platform,'url':url,'info':yt_dlp.YoutubeDL.sanitize_info(info,remove_private_keys=True)},ensure_ascii=False)
 if len(raw.encode())>CACHE_MAX_BYTES:return
 temporary=None
 try:
  with tempfile.NamedTemporaryFile(mode='w',dir=path.parent,prefix='.tmp-',delete=False) as out:
   temporary=Path(out.name);out.write(raw)
  temporary.replace(path)
 finally:
  if temporary:temporary.unlink(missing_ok=True)

def youtube_reason(error):
 s=str(error).lower()
 if '429' in s:return 'rate_limit'
 if any(x in s for x in ('not a bot','sign in to confirm','page needs to be reloaded')):return 'challenge'
 if '403' in s:return 'forbidden'
 if any(x in s for x in ('requested format','no video formats')):return 'formats'
 return None

def youtube_event(name,**fields):
 print(json.dumps({'event':'media.youtube.'+name,**fields}),file=sys.stderr)

def extract_with_fallback(y,platform,url,download=False,cleanup=None):
 if platform!='youtube':return y.extract_info(url,download=download)
 control=YouTubeControl();action='download' if download else 'parse'
 provider=os.environ.get('SITE_YOUTUBE_POT_URL','').rstrip('/')
 routes=['default']+(['mweb'] if provider else [])+['android_vr']
 last=None
 for route in routes:
  options=dict(y.params)
  if route!='default':
   options['extractor_args']={**options.get('extractor_args',{}),'youtube':{'player_client':[route]}}
  if provider:
   options['extractor_args']={**options.get('extractor_args',{}),'youtubepot-bgutilhttp':{'base_url':[provider]}}
  try:
   with PublicYoutubeDL(options) as client:
    # Only metadata extraction is serialized; media transfers keep their own slots.
    with control.extraction():info=client.extract_info(url,download=False)
    validate(info)
    if download:info=client.process_ie_result(info,download=True)
   control.outcome(True)
   info['site_youtube_client']=route
   youtube_event('route',client=route,action=action,outcome='success',height=info.get('height'))
   return info
  except yt_dlp.utils.DownloadError as error:
   reason=youtube_reason(error)
   youtube_event('route',client=route,action=action,outcome='failed',reason=reason or 'other')
   if not reason:raise
   last=error
   if download and cleanup:cleanup()
   # A 429 must cool down instead of multiplying requests with extra clients.
   if reason=='rate_limit':break
 if youtube_reason(last) in ('challenge','forbidden','rate_limit'):
  remaining=control.outcome(False,immediate=youtube_reason(last)=='rate_limit')
  if remaining:
   youtube_event('cooldown',seconds=remaining)
   raise CooldownError(remaining) from last
 raise last

def extract_or_cached(y,platform,url):
 lock=YouTubeControl().video_lock(url) if platform=='youtube' else nullcontext()
 with lock:
  info=cached_info(platform,url)
  if info is None:
   info=extract_with_fallback(y,platform,url,download=False);validate(info)
   save_info(platform,url,info)
  elif platform=='youtube':youtube_event('cache_hit',action='parse')
 return info

def parse(platform,url):
 if platform in ('instagram','twitter'):
  from post_media import parse as parse_post
  return parse_post(platform,url)
 with PublicYoutubeDL(opts(platform)) as y:
  info=extract_or_cached(y,platform,url);validate(info)
 return {'engine':'multi','source_url':url,'platform':platform,'content_id':str(info['id']),'title':info.get('title') or '未命名视频','author':{'nickname':info.get('uploader') or info.get('channel') or ''},'kind':'video','duration_ms':round((info.get('duration') or 0)*1000),'media':{'video':{'width':info.get('width'),'height':info.get('height'),'watermark':None},'images':[]}}
def extract_cover(y,info,root,prefix):
 if not info:return None
 thumbs=info.get('thumbnails') or []
 if not thumbs and info.get('thumbnail'):thumbs=[{'url':info['thumbnail']}]
 valid=[t for t in thumbs if isinstance(t,dict) and t.get('url')]
 if not valid and isinstance(info.get('thumbnail'),str):valid=[{'url':info['thumbnail']}]
 valid=[t for t in valid if t['url'].startswith(('http://','https://'))]
 if not valid:return None
 def sort_key(t):return (t.get('preference') or 0,(t.get('width') or 0)*(t.get('height') or 0))
 for cand in sorted(valid,key=sort_key,reverse=True)[:4]:
  try:
   thumb_url=cand['url'];public(thumb_url)
   req=y.urlopen(thumb_url)
   with req as resp:data=resp.read(30*1024*1024+1)
   if not data or len(data)>30*1024*1024:continue
   ext,ctype='.jpg','image/jpeg'
   if data.startswith(b'\x89PNG\r\n\x1a\n'):ext,ctype='.png','image/png'
   elif data.startswith(b'RIFF') and len(data)>=12 and data[8:12]==b'WEBP':ext,ctype='.webp','image/webp'
   elif data.startswith(b'GIF8'):ext,ctype='.gif','image/gif'
   target=root/(prefix+'-cover'+ext)
   target.write_bytes(data)
   return {'name':'cover'+ext,'kind':'cover','bytes':len(data),'state':'done','content_type':ctype,'local_name':target.name}
  except Exception:continue
 return None

def download(platform,url,prefix):
 if platform in ('instagram','twitter'):
  from post_media import download as download_post
  return download_post(platform,url,prefix)
 root=Path(os.environ.get('SITE_DATA_ROOT','/data'));base=root/prefix;amount={}
 def reset_partial():
  for partial in root.glob(prefix+'.*'):partial.unlink(missing_ok=True)
  amount.clear()
 def progress(d):
  if d.get('filename'):
   amount[d['filename']]=d.get('downloaded_bytes',0)
   if sum(amount.values())>2*1024*1024*1024:raise ValueError('2 GB size limit')
 def match(info,*,incomplete=False):
  if info.get('is_live'):return 'Playlist or live is not supported'
  if (info.get('duration') or 0)>3600:return 'Video exceeds 60 minutes'
 options=opts(platform);options.update({'outtmpl':str(base)+'.%(ext)s','progress_hooks':[progress],'match_filter':match,'writethumbnail':False,'writeinfojson':False,'postprocessors':[{'key':'FFmpegVideoRemuxer','preferedformat':'mp4'}]})
 try:
  with PublicYoutubeDL(options) as y:
   info=cached_info(platform,url)
   if info is not None:
    try:
     validate(info)
     info=y.process_ie_result(info,download=True)
     if platform=='youtube':
      YouTubeControl().outcome(True)
      youtube_event('route',client=info.get('site_youtube_client','cache'),action='download',outcome='success',height=info.get('height'))
    except yt_dlp.utils.DownloadError as error:
     if not any(code in str(error).lower() for code in ('403','410','expired','signature','url has expired')):raise
     cache_path(platform,url).unlink(missing_ok=True)
     for partial in root.glob(prefix+'.*'):partial.unlink(missing_ok=True)
     amount.clear()
     info=extract_with_fallback(y,platform,url,download=True,cleanup=reset_partial)
   else:
    info=extract_with_fallback(y,platform,url,download=True,cleanup=reset_partial)
   validate(info)
   cover_item=extract_cover(y,info,root,prefix)
  files=[f for f in root.glob(prefix+'.*') if f.suffix in ['.mp4','.mkv','.webm','.mov'] and f.stat().st_size>0 and '.f' not in f.name[len(prefix):]]
  if not files:raise ValueError('No video downloaded')
  f=files[0]
  if f.stat().st_size>2*1024*1024*1024:raise ValueError('2 GB size limit')
  meta=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_entries','stream=codec_type','-of','json',str(f)],timeout=15))
  if not any(x.get('codec_type')=='video' for x in meta.get('streams',[])):raise ValueError('No video stream')
  items=[{'name':'video'+f.suffix,'kind':'video','bytes':f.stat().st_size,'state':'done','content_type':'video/mp4' if f.suffix=='.mp4' else 'video/webm','local_name':f.name,'audio':any(x.get('codec_type')=='audio' for x in meta.get('streams',[]))}]
  if cover_item:items.append(cover_item)
  return items
 except Exception:
  for f in root.glob(prefix+'.*'):f.unlink(missing_ok=True)
  raise
if __name__=='__main__':
 try:
  action,platform,url=sys.argv[1:4];p,normalized=classify(url)
  if p!=platform or p not in PLATFORMS:raise ValueError('Unsupported platform')
  data=parse(platform,normalized) if action=='parse' else download(platform,normalized,sys.argv[4])
  print(json.dumps({'ok':True,'data':data},ensure_ascii=False))
 except Exception as e:
  print(json.dumps({'event':'media.extract.failed','platform':locals().get('platform'),'action':locals().get('action'),'error_type':type(e).__name__,'detail':re.sub(r'https?://[^\s]+','[url]',str(e))[:600]},ensure_ascii=False),file=sys.stderr)
  print(json.dumps({'ok':False,'error':friendly(e),'retry_after':getattr(e,'retry_after',0),'error_code':'YOUTUBE_COOLDOWN' if isinstance(e,CooldownError) else ('UPSTREAM_RESTRICTED' if youtube_reason(e) in ('challenge','forbidden','rate_limit') else 'UPSTREAM_EXTRACT')},ensure_ascii=False));sys.exit(1)
