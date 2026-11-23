"""Install the optional Windows HTML panel environment with verified PyPI wheels."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import subprocess
import urllib.request
import venv

VERSION = '6.8.3'
PACKAGES = ('shiboken6', 'PySide6_Essentials', 'PySide6_Addons', 'PySide6')

def fetch(url, headers=None):
    request = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()

def wheel(package, folder):
    meta = json.loads(fetch(f'https://pypi.org/pypi/{package}/{VERSION}/json'))
    item = next(x for x in meta['urls'] if x['filename'].endswith('win_amd64.whl'))
    target = folder / item['filename']
    size, digest = item['size'], item['digests']['sha256']
    if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest()==digest:
        return target
    step = 1024*1024
    def chunk(offset):
        end = min(size,offset+step)-1
        for attempt in range(4):
            try:
                data = fetch(item['url']+f'?part={offset}&retry={attempt}', {'Range':f'bytes={offset}-{end}'})
                if len(data)!=end-offset+1:
                    raise OSError('Incomplete wheel segment')
                return data
            except OSError:
                if attempt==3:
                    raise
    with ThreadPoolExecutor(max_workers=12) as workers:
        data = b''.join(workers.map(chunk, range(0,size,step)))
    if hashlib.sha256(data).hexdigest()!=digest:
        raise ValueError('Wheel verification failed')
    target.write_bytes(data)
    print('Verified',target.name,flush=True)
    return target

if __name__=='__main__':
    runtime = Path(__file__).resolve().with_name('.runtime')
    cache=runtime/'panel-wheels'
    cache.mkdir(parents=True,exist_ok=True)
    env=runtime/'panels-env'
    python=env/'Scripts/python.exe'
    if not python.exists():
        venv.EnvBuilder(with_pip=True).create(env)
    wheels=[wheel(name,cache) for name in PACKAGES]
    subprocess.run([str(python),'-m','pip','install','--no-index',*map(str,wheels)],check=True)
