"""Single-owner runtime, durable job dispatch, and bounded download storage."""
import asyncio
import json
import logging
import os
import re
import secrets
import shutil
import time
from contextlib import asynccontextmanager

TERMINAL = {'done', 'partial', 'failed', 'cancelled'}
LOGGER = logging.getLogger('media')
LOGGER.setLevel(logging.INFO)
if not LOGGER.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter('%(message)s'))
    LOGGER.addHandler(handler)
LOGGER.propagate = False

def event(name, **fields):
    safe = {}
    for key, value in fields.items():
        if value is None:
            continue
        if isinstance(value, str):
            value = re.sub(r'https?://[^\s]+', '[url]', value)
            value = re.sub(r'(?i)(cookie|authorization|token|secret|password)\s*[:=]\s*[^\s,;]+', r'\1=[redacted]', value)
            value = value[:500]
        safe[key] = value
    LOGGER.info(json.dumps({'event': name, **safe}, ensure_ascii=False))

def error_code(error):
    status = getattr(getattr(error, 'response', None), 'status_code', None)
    message = str(error).lower()
    if status in (403, 412, 429) or any(x in message for x in ('captcha', 'bot', 'risk control', '限制')):
        return 'UPSTREAM_RESTRICTED'
    if status in (404, 410): return 'MEDIA_EXPIRED'
    if 'timeout' in type(error).__name__.lower() or 'timed out' in message: return 'UPSTREAM_TIMEOUT'
    if isinstance(error, DiskCapacityError): return 'DISK_CAPACITY'
    if '2gb' in message.replace(' ', ''): return 'SIZE_LIMIT'
    return 'DOWNLOAD_ERROR'

class RuntimeLease:
    RENEW = "if redis.call('get',KEYS[1])==ARGV[1] then return redis.call('expire',KEYS[1],ARGV[2]) else return 0 end"
    RELEASE = "if redis.call('get',KEYS[1])==ARGV[1] then return redis.call('del',KEYS[1]) else return 0 end"
    def __init__(self, client, key, ttl=30):
        self.client, self.key, self.ttl = client, key, ttl
        self.token = secrets.token_urlsafe(24)

    async def acquire(self):
        if not await self.client.set(self.key, self.token, nx=True, ex=self.ttl):
            raise RuntimeError('The website runtime is already owned by another process; use one web worker.')

    async def renew(self):
        if not await self.client.eval(self.RENEW, 1, self.key, self.token, self.ttl):
            raise RuntimeError('Runtime lease lost')

    async def release(self):
        await self.client.eval(self.RELEASE, 1, self.key, self.token)

class DiskCapacityError(Exception):
    pass

class DiskBudget:
    def __init__(self, root, jobs, quota=None, reserve=None, min_free=None):
        self.root, self.jobs = root, jobs
        self.quota = quota if quota is not None else int(os.environ.get('SITE_DISK_QUOTA_BYTES', 12*1024**3))
        self.reserve = reserve if reserve is not None else int(os.environ.get('SITE_JOB_RESERVE_BYTES', 4*1024**3))
        self.min_free = min_free if min_free is not None else int(os.environ.get('SITE_DISK_MIN_FREE_BYTES', 1024**3))
        self.reservations = {}
        self.readers = {}
        self.lock = asyncio.Lock()

    def protect(self, job_id):
        self.readers[job_id] = self.readers.get(job_id, 0) + 1

    def unprotect(self, job_id):
        value = self.readers.get(job_id, 1)-1
        if value: self.readers[job_id] = value
        else: self.readers.pop(job_id, None)

    async def release_reader(self, job_id):
        self.unprotect(job_id)

    def _files(self, job_id):
        return [p for p in self.root.iterdir() if p.is_file() and (p.name.startswith(job_id+'.') or p.name.startswith(job_id+'-'))]

    def _snapshot(self):
        files = [p for p in self.root.iterdir() if p.is_file()]
        cache = self.root/'.extract-cache'
        if cache.is_dir(): files += [p for p in cache.iterdir() if p.is_file()]
        usage = 0; reserved_usage = {jid: 0 for jid in self.reservations}
        for path in files:
            try: size = path.stat().st_size
            except FileNotFoundError: continue
            usage += size
            for jid in reserved_usage:
                if path.parent == self.root and (path.name.startswith(jid+'.') or path.name.startswith(jid+'-')):
                    reserved_usage[jid] += size
        pending = sum(max(0, value-reserved_usage[jid]) for jid,value in self.reservations.items())
        return usage, pending, shutil.disk_usage(self.root).free

    async def _evict(self, job_id, reason):
        job = self.jobs.get(job_id)
        if not job or self.readers.get(job_id) or job_id in self.reservations or job.get('state') not in TERMINAL:
            return False
        paths = await asyncio.to_thread(self._files, job_id)
        if not paths or self.readers.get(job_id): return False
        # Invalidate before yielding so a new reader cannot enter this cache.
        job.update(state='failed', files=[], error='下载缓存已清理，请重新解析', error_code='CACHE_EVICTED')
        await asyncio.to_thread(self._unlink, paths)
        event('cache.evicted', job=job_id, reason=reason)
        return True

    @staticmethod
    def _unlink(paths):
        for path in paths: path.unlink(missing_ok=True)

    @asynccontextmanager
    async def reservation(self, job_id):
        async with self.lock:
            candidates = sorted((key for key,value in self.jobs.items() if key!=job_id and value.get('state') in TERMINAL), key=lambda key:self.jobs[key].get('time',0))
            while True:
                used, pending, free = await asyncio.to_thread(self._snapshot)
                if used+pending+self.reserve<=self.quota and free-pending-self.reserve>=self.min_free:
                    self.reservations[job_id] = self.reserve
                    break
                if not candidates:
                    raise DiskCapacityError('下载暂存空间不足，请稍后重试')
                await self._evict(candidates.pop(0), 'capacity')
        try:
            yield
        finally:
            async with self.lock: self.reservations.pop(job_id, None)

    async def clean_expired(self, now=None):
        now = now or time.time()
        async with self.lock:
            for jid,job in list(self.jobs.items()):
                if now-job.get('finished_at',job.get('time',now))>3600:
                    await self._evict(jid, 'expiry')
            active = {jid for jid,job in self.jobs.items() if job.get('state') not in TERMINAL or now-job.get('finished_at',job.get('time',now))<=3600} | set(self.readers) | set(self.reservations)
            def expired_files():
                found=[]
                for path in self.root.iterdir():
                    if not path.is_file(): continue
                    try: old=now-path.stat().st_mtime>3600
                    except FileNotFoundError: continue
                    if old and not any(path.name.startswith(jid+'.') or path.name.startswith(jid+'-') for jid in active): found.append(path)
                cache=self.root/'.extract-cache'
                if cache.is_dir():
                    found.extend(p for p in cache.iterdir() if p.is_file() and now-p.stat().st_mtime>300)
                return found
            await asyncio.to_thread(self._unlink, await asyncio.to_thread(expired_files))

class JobManager:
    def __init__(self, jobs, persist, client, prefix):
        self.jobs, self.persist, self.client, self.prefix = jobs, persist, client, prefix
        self.tasks = {}
        self.closing = False
        self.deadlines = {}

    def processing_started(self, job_id):
        timer = self.deadlines.get(job_id)
        if timer:
            seconds = 120 if self.jobs[job_id].get('type') == 'parse' else 3660
            timer.reschedule(asyncio.get_running_loop().time() + seconds)
        self.jobs[job_id]['started_at'] = time.time()

    def start(self, job_id, function, *args):
        existing=self.tasks.get(job_id)
        if existing and not existing.done(): return existing
        if self.closing: return None
        task=asyncio.create_task(self._run(job_id,function,args))
        self.tasks[job_id]=task
        task.add_done_callback(lambda done:self.tasks.pop(job_id,None) if self.tasks.get(job_id) is done else None)
        return task

    async def _run(self, job_id, function, args):
        job=self.jobs[job_id];started=time.monotonic()
        job['attempt']=job.get('attempt',0)+1
        event('job.started',job=job_id,platform=job.get('platform'),action=job.get('type'),attempt=job['attempt'])
        await self.persist()
        completed=False
        try:
            async with asyncio.timeout(float(os.environ.get('SITE_QUEUE_TIMEOUT','300'))) as timer:
                self.deadlines[job_id] = timer
                await function(job_id,*args)
            completed=True
        except asyncio.CancelledError:
            if job.get('state') not in TERMINAL:
                job.update(state='queued',error=None,error_code=None)
                event('job.requeued',job=job_id,platform=job.get('platform'))
            raise
        except Exception as error:
            code=error_code(error)
            detail=getattr(error,'detail',None)
            message='下载暂存空间不足，请稍后重试' if code=='DISK_CAPACITY' else ('任务响应超时，请重试' if isinstance(error,TimeoutError) else (detail if isinstance(detail,str) else '任务暂时无法完成，请重试'))
            job.update(state='failed',error=message,error_code=code)
            event('job.failed',job=job_id,platform=job.get('platform'),code=code,detail=str(error))
            completed=True
        finally:
            self.deadlines.pop(job_id,None)
            if completed and job.get('state') in TERMINAL:
                job['finished_at'] = time.time()
            await self.persist()
            if completed:
                if job.get('state') in ('failed','partial') and not job.get('error_code'):
                    job['error_code']=next((f.get('error_code') for f in job.get('files',[]) if f.get('error_code')),'UPSTREAM_EXTRACT')
                    await self.persist()
                elapsed=round((time.monotonic()-started)*1000)
                event('job.finished',job=job_id,platform=job.get('platform'),action=job.get('type'),state=job.get('state'),elapsed_ms=elapsed,code=job.get('error_code'))
                try:
                    key=f'{self.prefix}:metrics:{job.get("platform","unknown")}:{job.get("type","unknown")}'
                    pipe=self.client.pipeline(transaction=False)
                    pipe.hincrby(key,'count',1);pipe.hincrby(key,job.get('state','unknown'),1);pipe.hincrby(key,'duration_ms',elapsed)
                    bucket=next((bound for bound in (1000,3000,10000,30000,90000,300000,660000) if elapsed<=bound),'inf')
                    pipe.hincrby(key,f'latency_le_{bucket}',1)
                    if job.get('error_code'):pipe.hincrby(key,'error_'+job['error_code'],1)
                    pipe.expire(key,30*86400);await pipe.execute()
                except Exception as error:event('metrics.write_failed',detail=str(error))

    async def close(self):
        self.closing=True
        for task in list(self.tasks.values()):task.cancel()
        await asyncio.gather(*list(self.tasks.values()),return_exceptions=True)
