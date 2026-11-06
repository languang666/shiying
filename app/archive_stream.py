"""ZIP_STORED output in bounded chunks; never buffers a full image or archive."""
import asyncio
import time
import zipfile

class ChunkSink:
    def __init__(self): self.offset=0;self.parts=[]
    def write(self,data):
        if data:self.parts.append(bytes(data));self.offset+=len(data)
        return len(data)
    def tell(self):return self.offset
    def flush(self):pass
    def drain(self):
        parts,self.parts=self.parts,[]
        return parts

async def stream_zip(entries, source):
    sink=ChunkSink()
    with zipfile.ZipFile(sink,'w',compression=zipfile.ZIP_STORED,allowZip64=True) as archive:
        for entry in entries:
            info=zipfile.ZipInfo(entry['zip_name'],time.localtime()[:6]);info.compress_type=zipfile.ZIP_STORED
            info.file_size=entry.get('bytes',0)
            with archive.open(info,'w') as target:
                for data in sink.drain():yield data
                async for chunk in source(entry):
                    target.write(chunk)
                    for data in sink.drain():yield data
                    await asyncio.sleep(0)
            for data in sink.drain():yield data
    for data in sink.drain():yield data
