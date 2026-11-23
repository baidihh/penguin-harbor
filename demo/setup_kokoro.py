"""Install the pinned Kokoro multilingual CPU voice bundle."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.request
import venv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from demo.moss_download import download, read_json


REPOSITORY = 'csukuangfj/kokoro-multi-lang-v1_0'
REVISION = 'f7b96bb6bef5c5da4d3aa4f4e0498fbbf62dc78b'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--home', type=Path, default=Path(
        os.environ.get('PENGUIN_LOCAL_VOICE_HOME') or Path(__file__).with_name('.runtime')))
    home = parser.parse_args().home.resolve()
    home.mkdir(parents=True, exist_ok=True)
    environment = home / 'moss-env'
    python = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not python.is_file():
        venv.EnvBuilder(with_pip=True).create(environment)
    subprocess.run([str(python), '-m', 'pip', 'install', '--disable-pip-version-check',
                    'numpy>=1.26,<3', 'sherpa-onnx>=1.12,<2'], check=True)
    folder = home / 'kokoro-model'
    files = read_json(f'https://huggingface.co/api/models/{REPOSITORY}/tree/{REVISION}?recursive=true&limit=1000')
    files = [node for node in files if node.get('type') == 'file']
    small = [node for node in files if node.get('size', 0) < 16 * 1024 * 1024]
    large = [node for node in files if node.get('size', 0) >= 16 * 1024 * 1024]

    def fetch(node):
        download(REPOSITORY, REVISION, node, folder)

    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(fetch, small))
    for node in large:
        fetch(node)
    notices = {
        'UPSTREAM-MODEL.md': 'https://huggingface.co/hexgrad/Kokoro-82M/raw/main/README.md',
        'VOICES.md': 'https://huggingface.co/hexgrad/Kokoro-82M/raw/main/VOICES.md',
        'ESPEAK-LICENSE': 'https://raw.githubusercontent.com/espeak-ng/espeak-ng/master/COPYING',
        'JIEBA-LICENSE': 'https://raw.githubusercontent.com/fxsjy/jieba/master/LICENSE',
    }
    for name, url in notices.items():
        with urllib.request.urlopen(url, timeout=30) as response:
            (folder / name).write_bytes(response.read())
    record = {'repository': REPOSITORY, 'revision': REVISION,
              'upstream_model': 'hexgrad/Kokoro-82M', 'model_license': 'Apache-2.0',
              'download_bytes': sum(node.get('size', 0) for node in files)}
    (home / 'kokoro-install.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
    print('Kokoro local voices ready.', flush=True)


if __name__ == '__main__':
    main()
