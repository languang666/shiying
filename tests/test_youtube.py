import asyncio, concurrent.futures, contextlib, io, json, os, tempfile, time
from pathlib import Path
from unittest.mock import patch
import media_worker as worker
from youtube_control import YouTubeControl, CooldownError
from runtime import DiskBudget, JobManager
from state_store import StateStore

def gate(root):
    control=YouTubeControl(root,interval=0.08)
    with control.extraction():
        start=time.monotonic();time.sleep(0.03)
    return start

with tempfile.TemporaryDirectory() as root:
    with concurrent.futures.ProcessPoolExecutor(max_workers=3) as pool:
        starts=sorted(pool.map(gate,[root]*3))
    assert all(b-a>=0.07 for a,b in zip(starts,starts[1:])),starts
    control=YouTubeControl(root,interval=0)
    assert control.outcome(False)==0
    assert 119<=control.outcome(False)<=120
    try:
        with control.extraction():raise AssertionError('Cooldown admitted a request')
    except CooldownError as error:assert error.retry_after>0
    # Cooldown survives new worker construction and permits a controlled recovery.
    assert YouTubeControl(root).remaining()>0
    with control.lock():
        state=control.read();state['until']=time.time()-1;control.write(state)
    with control.extraction():pass
    control.outcome(True);assert control.remaining()==0

with tempfile.TemporaryDirectory() as root,patch.dict(os.environ,{'SITE_DATA_ROOT':root,'SITE_YOUTUBE_INTERVAL':'0','SITE_YOUTUBE_POT_URL':'http://youtube-pot:4416'}):
    calls=[];failures={'default'}
    class Fake:
        def __init__(self,params):self.params=params
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def extract_info(self,url,download=False):
            route=self.params.get('extractor_args',{}).get('youtube',{}).get('player_client',['default'])[0]
            calls.append(route)
            if route in failures:raise worker.yt_dlp.utils.DownloadError("Sign in to confirm you're not a bot")
            return {'id':'abcdefghijk','duration':10,'height':1080,'formats':[]}
        def process_ie_result(self,info,download=False):return info
    params={'max_filesize':2*1024**3,'extractor_args':{}}
    with patch.object(worker,'PublicYoutubeDL',Fake),contextlib.redirect_stderr(io.StringIO()) as logs:
        info=worker.extract_with_fallback(Fake(params),'youtube','https://www.youtube.com/watch?v=abcdefghijk',True)
        assert calls==['default','mweb'] and info['height']==1080
        assert params=={'max_filesize':2*1024**3,'extractor_args':{}}
        assert '"outcome": "success"' in logs.getvalue()
        calls.clear();failures.add('mweb')
        worker.extract_with_fallback(Fake(params),'youtube','https://www.youtube.com/watch?v=abcdefghijk')
        assert calls==['default','mweb','android_vr']
    # Simultaneous identical parse jobs issue one upstream extraction.
    calls.clear();failures.clear()
    with patch.object(worker,'PublicYoutubeDL',Fake),contextlib.redirect_stderr(io.StringIO()):
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(lambda _:worker.extract_or_cached(Fake(params),'youtube','https://www.youtube.com/watch?v=abcdefghijk'),range(3)))
    assert calls==['default'],calls

async def lifecycle():
    now=time.time()
    assert StateStore.ttl('jobs',{'state':'running','time':now-7200})==7200
    assert 3599<=StateStore.ttl('jobs',{'state':'done','time':now-7200,'finished_at':now})<=3600
    with tempfile.TemporaryDirectory() as root:
        root=Path(root);now=time.time()
        jobs={'long':{'state':'done','time':now-7200,'finished_at':now,'files':[{'name':'video.mp4'}]}}
        file=root/'long.mp4';file.write_bytes(b'data');os.utime(file,(now-7200,now-7200))
        budget=DiskBudget(root,jobs)
        await budget.clean_expired(now+3599);assert file.exists()
        await budget.clean_expired(now+3601);assert not file.exists()
    class Pipe:
        def __getattr__(self,name):return lambda *args,**kwargs:self
        async def execute(self):pass
    class Redis:
        def pipeline(self,**kwargs):return Pipe()
    async def persist():pass
    jobs={'queued':{'state':'queued','type':'parse'}}
    manager=JobManager(jobs,persist,Redis(),'test')
    async def work(jid):
        await asyncio.sleep(0.02)
        manager.processing_started(jid)
        await asyncio.sleep(0.08)
        jobs[jid]['state']='done'
    with patch.dict(os.environ,{'SITE_QUEUE_TIMEOUT':'0.05'}):
        await manager.start('queued',work)
    assert jobs['queued']['state']=='done' and jobs['queued']['finished_at']>=jobs['queued']['started_at']
    jobs['timeout']={'state':'queued','type':'parse'}
    async def wait_forever(jid):await asyncio.sleep(1)
    with patch.dict(os.environ,{'SITE_QUEUE_TIMEOUT':'0.01'}):await manager.start('timeout',wait_forever)
    assert jobs['timeout']['state']=='failed'

asyncio.run(lifecycle())
print('PASS: pacing/exclusion, persistent cooldown/recovery, route fallback, parse coalescing, completion retention, separate queue timeout')
