"""Install pinned MOSS CPU assets and encode a local reference voice."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request
import venv
import wave

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from desktop.moss_download import download, read_json
from desktop.netease import find_ffmpeg


SOURCE_COMMIT = '8b7bcc9341b3b4ef3a3a58ba1338a7d85ff133eb'
MODELS = [
    {'repository': 'OpenMOSS-Team/MOSS-TTS-Nano-100M-ONNX',
     'revision': 'f52645cb467506d8e18e746ddd59482685b74e58'},
    {'repository': 'OpenMOSS-Team/MOSS-Audio-Tokenizer-Nano-ONNX',
     'revision': 'ceff0d0749bfb3fa2d61149794ec6feef0d1e1ae'},
]


def convert_reference(reference, target):
    try:
        with wave.open(str(reference), 'rb') as audio:
            if audio.getnchannels() == 2 and audio.getsampwidth() == 2 and audio.getframerate() == 48000:
                frames = audio.readframes(min(audio.getnframes(), 48000 * 12))
                with wave.open(str(target), 'wb') as output:
                    output.setparams((2, 2, 48000, 0, 'NONE', 'not compressed'))
                    output.writeframes(frames)
                return
    except (wave.Error, EOFError):
        pass
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError('转换参考音频需要 ffmpeg，或者直接提供 48 kHz 双声道 16 位 WAV。')
    subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', '-i', str(reference),
                    '-t', '12', '-ar', '48000', '-ac', '2', str(target)], check=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--home', type=Path, default=Path(
        os.environ.get('PENGUIN_LOCAL_VOICE_HOME') or Path(__file__).with_name('.runtime')))
    parser.add_argument('--reference', type=Path, help='用于创建本地音色的参考音频')
    options = parser.parse_args()
    home = options.home.resolve()
    voice = home / 'moss-voices' / 'wanwan-evaluation.json'
    if options.reference is None and not voice.is_file():
        parser.error('首次安装需要 --reference 指定参考音频。')
    if options.reference is not None and not options.reference.is_file():
        parser.error('找不到参考音频。')
    home.mkdir(parents=True, exist_ok=True)
    environment = home / 'moss-env'
    python = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not python.is_file():
        venv.EnvBuilder(with_pip=True).create(environment)
    subprocess.run([str(python), '-m', 'pip', 'install', '--disable-pip-version-check',
                    'numpy>=1.26,<3', 'onnxruntime>=1.20,<2', 'sentencepiece>=0.2,<1'], check=True)
    source = home / 'moss-source'
    source.mkdir(exist_ok=True)
    for name in ('ort_cpu_runtime.py', 'LICENSE'):
        url = f'https://raw.githubusercontent.com/OpenMOSS/MOSS-TTS-Nano/{SOURCE_COMMIT}/{name}'
        with urllib.request.urlopen(url, timeout=30) as response:
            data = response.read()
        (source / name).write_bytes(data)
    for model in MODELS:
        repo, revision = model['repository'], model['revision']
        files = read_json(f'https://huggingface.co/api/models/{repo}/tree/{revision}?recursive=true')
        folder = home / 'moss-models' / repo.split('/')[-1]
        for node in files:
            if node.get('type') == 'file':
                download(repo, revision, node, folder)
        shutil.copyfile(source / 'LICENSE', folder / 'LICENSE')
    record = {'source_commit': SOURCE_COMMIT, 'models': MODELS}
    (home / 'moss-install.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
    if options.reference is not None:
        target = home / 'moss-voices' / 'reference-prepared.wav'
        target.parent.mkdir(exist_ok=True)
        convert_reference(options.reference.resolve(), target)
        provenance_path = target.parent / 'source.json'
        provenance = json.loads(provenance_path.read_text(encoding='utf-8')) if provenance_path.exists() else {}
        provenance.update(reference_file=options.reference.name,
                          prepared_sha256=hashlib.sha256(target.read_bytes()).hexdigest())
        provenance_path.write_text(json.dumps(provenance, ensure_ascii=False, indent=2), encoding='utf-8')
        subprocess.run([str(python), str(Path(__file__).with_name('moss_worker.py')), str(home),
                        '--prepare-reference', str(target)], check=True)
    print('本地语音准备完成。重启企鹅后选择“湾湾参考 · 本地实验女声”。')


if __name__ == '__main__':
    main()
