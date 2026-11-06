import asyncio,io,json,logging,os,tempfile,time,tracemalloc,zipfile
from pathlib import Path
import redis.asyncio as redis
from runtime import RuntimeLease,DiskBudget,DiskCapacityError,JobManager,event,LOGGER
from archive_stream import stream_zip

async def main():
 r=redis.from_url(os.environ['SITE_REDIS_URL'],decode_responses=True)
 prefix='media-site:unit:'+__import__('secrets').token_hex(8)
 a=RuntimeLease(r,prefix+':lease',ttl=5);b=RuntimeLease(r,prefix+':lease',ttl=5)
 await a.acquire()
 try:await b.acquire()
 except RuntimeError:pass
 else:raise AssertionError('Second runtime acquired the same lease')
 await b.release();await a.renew();await a.release();await b.acquire();await b.release()
 with tempfile.TemporaryDirectory() as temporary:
  root=Path(temporary);(root/'old.image.jpg').write_bytes(b'x'*100)
  jobs={'old':{'state':'done','time':0,'files':[{'name':'image.jpg'}]}}
  budget=DiskBudget(root,jobs,quota=120,reserve=40,min_free=0);budget.protect('old')
  try:
   async with budget.reservation('new'):raise AssertionError('Capacity incorrectly admitted')
  except DiskCapacityError:pass
  assert (root/'old.image.jpg').exists() and jobs['old']['state']=='done'
  budget.unprotect('old')
  async with budget.reservation('new'):
   assert not (root/'old.image.jpg').exists() and jobs['old']['error_code']=='CACHE_EVICTED'
   (root/'new.image.jpg').write_bytes(b'x'*35)
   async with budget.reservation('second'):assert len(budget.reservations)==2
  assert not budget.reservations
  (root/'running.part').write_bytes(b'x');os.utime(root/'running.part',(0,0))
  jobs['running']={'state':'running','time':0}
  await budget.clean_expired();assert (root/'running.part').exists()
 jobs={'recover':{'state':'queued','platform':'test','type':'download','files':[]}}
 async def persist():pass
 entered=asyncio.Event();block=asyncio.Event();calls=0
 async def interrupted(jid):
  nonlocal calls
  calls+=1;jobs[jid].update(state='running',files=[{'name':'completed','state':'done'}]);entered.set();await block.wait()
 manager=JobManager(jobs,persist,r,prefix)
 for _ in range(5):manager.start('recover',interrupted)
 await entered.wait();assert calls==1;await manager.close()
 assert jobs['recover']['state']=='queued' and jobs['recover']['files'][0]['state']=='done'
 async def resumed(jid):
  assert jobs[jid]['files'][0]['name']=='completed';jobs[jid]['state']='done'
 manager=JobManager(jobs,persist,r,prefix);await manager.start('recover',resumed);await manager.close()
 assert jobs['recover']['attempt']==2 and await r.hget(prefix+':metrics:test:download','done')=='1'
 entries=[{'zip_name':'图片-01.jpg','bytes':1000},{'zip_name':'image-02.webp','bytes':1000}]
 async def small_source(item):yield b'x'*1000
 chunks=[chunk async for chunk in stream_zip(entries,small_source)]
 with zipfile.ZipFile(io.BytesIO(b''.join(chunks))) as archive:
  assert len(archive.namelist())==2 and archive.testzip() is None
 async def large_source(item):
  chunk=b'x'*65536
  for _ in range(1024):yield chunk
 tracemalloc.start();amount=0;largest=0
 async for chunk in stream_zip([{'zip_name':'large.jpg','bytes':64*1024**2}],large_source):amount+=len(chunk);largest=max(largest,len(chunk))
 _,peak=tracemalloc.get_traced_memory();tracemalloc.stop()
 assert amount>64*1024**2 and largest<=65536 and peak<4*1024**2,(amount,largest,peak)
 output=io.StringIO();handler=logging.StreamHandler(output);LOGGER.addHandler(handler)
 event('redaction.test',detail='https://example.com/media?token=private Cookie=private authorization=private password=private')
 LOGGER.removeHandler(handler);assert 'private' not in output.getvalue()
 keys=[key async for key in r.scan_iter(match=prefix+':*')]
 if keys:await r.delete(*keys)
 await r.aclose()
 print(json.dumps({'lease_exclusion_and_ownership':True,'disk_eviction_and_reader_protection':True,'active_partial_files_preserved':True,'job_deduplication_and_restart_resume':True,'zip_crc':True,'zip_streamed_bytes':amount,'largest_chunk':largest,'stream_memory_peak':peak,'secret_redaction':True}))
asyncio.run(main())
