"""Verified, resumable downloads for the optional local voice installer."""

import hashlib
import json
from pathlib import Path
import time
import urllib.request
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

HEADERS = {'User-Agent': 'PenguinLocalVoice/1.0'}
CHUNK = 4 * 1024 * 1024

def read_json(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=30) as response:
        return json.load(response)

def download(repo, revision, node, folder):
    name, size = node['path'], node.get('size', 0)
    target = (folder / name).resolve()
    if not target.is_relative_to(folder.resolve()):
        raise ValueError('Model file path leaves its destination folder')
    target.parent.mkdir(parents=True, exist_ok=True)
    digest = (node.get('lfs') or {}).get('oid')
    if target.exists() and target.stat().st_size == size:
        if not digest or hashlib.sha256(target.read_bytes()).hexdigest() == digest:
            return
    encoded_name = urllib.parse.quote(name, safe='/')
    base = f'https://huggingface.co/{repo}/resolve/{revision}/{encoded_name}'
    if size < 16 * 1024 * 1024:
        for attempt in range(4):
            try:
                with urllib.request.urlopen(urllib.request.Request(base+'?download=true',headers=HEADERS),timeout=45) as response:
                    data = response.read()
                if len(data) != size:
                    raise RuntimeError(f'Wrong file size for {name}')
                if digest and hashlib.sha256(data).hexdigest() != digest:
                    raise RuntimeError(f'Wrong hash for {name}')
                temporary = target.with_name(target.name + '.partial')
                temporary.write_bytes(data)
                temporary.replace(target)
                print('Ready', name, flush=True)
                return
            except Exception:
                if attempt == 3:
                    raise
                time.sleep(2)
    parts = folder / '.parts' / name
    parts.mkdir(parents=True, exist_ok=True)
    starts = list(range(0, size, CHUNK))

    def get_part(start):
        end = min(size, start + CHUNK) - 1
        part = parts / str(start)
        if part.exists() and part.stat().st_size == end - start + 1:
            return part
        for attempt in range(5):
            try:
                url = base + f'?download=true&segment={start}&attempt={attempt}'
                req = urllib.request.Request(url,headers={**HEADERS,'Range':f'bytes={start}-{end}'})
                with urllib.request.urlopen(req,timeout=40) as response:
                    content_range = response.headers.get('Content-Range','')
                    if response.status != 206 or not content_range.startswith(f'bytes {start}-'):
                        raise RuntimeError('Server returned a different range')
                    data = response.read()
                if len(data) != end - start + 1:
                    raise RuntimeError('Incomplete range')
                part.write_bytes(data)
                return part
            except Exception:
                if attempt == 4:
                    raise
                time.sleep(2)

    print('Downloading ranges', name, round(size/1024/1024), 'MiB',flush=True)
    with ThreadPoolExecutor(max_workers=8) as pool:
        for index, _ in enumerate(pool.map(get_part, starts), 1):
            if index % 8 == 0 or index == len(starts):
                print(name, index, '/', len(starts), 'segments ready',flush=True)
    temporary = target.with_name(target.name + '.assembled')
    sha = hashlib.sha256()
    with temporary.open('wb') as output:
        for start in starts:
            data = (parts / str(start)).read_bytes()
            output.write(data)
            sha.update(data)
    if digest and sha.hexdigest() != digest:
        raise RuntimeError('Final model hash mismatch')
    temporary.replace(target)
    print('Verified', name, sha.hexdigest(),flush=True)
