import json, os, tempfile
import media_worker as m
with tempfile.TemporaryDirectory(dir='/data',prefix='.verification-') as root:
    os.environ['SITE_DATA_ROOT']=root
    files=m.download('youtube','https://www.youtube.com/watch?v=T0zf2gNdn8M','test')
    print(json.dumps({'download':True,'files':[{k:v for k,v in f.items() if k in ('kind','bytes','audio')} for f in files]}),flush=True)
