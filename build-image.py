"""Build a candidate image and validate it before an explicit Compose deployment."""
import argparse
import pathlib
import subprocess

root=pathlib.Path(__file__).resolve().parent
parser=argparse.ArgumentParser()
parser.add_argument('--youtube-smoke',action='store_true',help='Also download a short public YouTube video; test files are removed')
args=parser.parse_args()
compose=['docker','compose','-f',str(root/'compose.yaml')]
subprocess.run(compose+['config','--quiet'],check=True)
subprocess.run(compose+['build','--pull','site'],check=True)
# The runtime test needs Redis; it uses a random key prefix and removes it afterwards.
subprocess.run(compose+['up','-d','--wait','redis'],check=True)
candidate=compose+['run','--rm','--no-deps','-T','--entrypoint','python','site']
subprocess.run(candidate+['-m','pip','check'],check=True)
for name in ('test_runtime.py','test_youtube.py'):
    with (root/'tests'/name).open() as source:
        subprocess.run(candidate+['-'],stdin=source,check=True)
if args.youtube_smoke:
    subprocess.run(compose+['up','-d','--wait','youtube-pot'],check=True)
    with (root/'tests/test_youtube_smoke.py').open() as source:
        subprocess.run(compose+['run','--rm','--no-deps','-T','-e','PYTHONPATH=/site','--entrypoint','python','site','-'],stdin=source,check=True)
print('Candidate checks passed. Deploy: docker compose up -d --wait site')
