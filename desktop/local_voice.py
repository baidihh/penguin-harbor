"""Local-only transport to the persistent MOSS CPU worker."""

import atexit
import io
import json
import os
from pathlib import Path
import queue
import subprocess
import tempfile
import threading
import wave


HOME = Path(os.environ.get('PENGUIN_LOCAL_VOICE_HOME') or Path(__file__).with_name('.runtime'))


def tune_wav(audio, *, speed=1.0, pitch_semitones=0.0):
    if not 0.5 <= speed <= 2.0 or not -6 <= pitch_semitones <= 6:
        raise ValueError('语速或音调参数超出支持范围。')
    if speed == 1.0 and pitch_semitones == 0.0:
        return audio
    from .netease import find_ffmpeg
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError('调整本地语速和音调需要 ffmpeg。')
    with wave.open(io.BytesIO(audio), 'rb') as source:
        rate = source.getframerate()
    pitch = 2 ** (pitch_semitones / 12)
    tempo = (speed / pitch) ** 0.5
    filters = f'asetrate={rate * pitch:.6f},aresample={rate},atempo={tempo:.8f},atempo={tempo:.8f}'
    folder = HOME / 'moss-output'
    folder.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=folder) as temporary:
        source = Path(temporary) / 'original.wav'
        target = Path(temporary) / 'tuned.wav'
        source.write_bytes(audio)
        result = subprocess.run(
            [ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-i', str(source),
             '-af', filters, '-c:a', 'pcm_s16le', str(target)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            timeout=30, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        if result.returncode:
            raise RuntimeError('本地语音的音调处理没有完成。')
        return target.read_bytes()


class LocalVoice:
    def __init__(self):
        self.process = None
        self.responses = None
        self.lock = threading.Lock()
        self.active = threading.Event()
        self.stats = {}

    def _start(self, voice_id):
        if self.process is not None and self.process.poll() is None:
            return
        python = HOME / 'moss-env' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        voice = (HOME / 'kokoro-model' / 'model.onnx' if voice_id.startswith('kokoro_')
                 else HOME / 'moss-voices' / 'wanwan-evaluation.json')
        if not python.is_file() or not voice.is_file():
            raise RuntimeError('本地音色还没有安装完成。')
        responses = queue.Queue()
        env = {**os.environ, 'PYTHONIOENCODING': 'utf-8', 'HF_HUB_OFFLINE': '1'}
        with (HOME / 'moss-worker.log').open('a', encoding='utf-8') as log:
            process = subprocess.Popen(
                [str(python), str(Path(__file__).with_name('moss_worker.py')), str(HOME)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, text=True, encoding='utf-8',
                env=env, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
        self.process, self.responses = process, responses

        def read():
            with process.stdout:
                for line in process.stdout:
                    try:
                        responses.put(json.loads(line))
                    except ValueError:
                        continue
            responses.put({'ok': False, 'error': '本地语音进程已结束，请重试。'})

        threading.Thread(target=read, daemon=True, name='moss-results').start()
        try:
            ready = responses.get(timeout=45)
        except queue.Empty as error:
            self.cancel()
            raise RuntimeError('本地模型启动超时，请检查 moss-worker.log。') from error
        if not ready.get('ready'):
            raise RuntimeError(ready.get('error', '本地模型启动失败。'))

    def synthesize(self, text, voice_id='moss_wanwan'):
        with self.lock:
            self.active.set()
            try:
                self._start(voice_id)
                folder = HOME / 'moss-output'
                folder.mkdir(exist_ok=True)
                with tempfile.TemporaryDirectory(dir=folder) as temporary:
                    output = Path(temporary) / 'speech.wav'
                    self.process.stdin.write(json.dumps({'text': text, 'voice': voice_id, 'output': str(output)},
                                                       ensure_ascii=False) + '\n')
                    self.process.stdin.flush()
                    try:
                        result = self.responses.get(timeout=120)
                    except queue.Empty as error:
                        self.cancel()
                        raise RuntimeError('本地语音合成超时，请缩短内容。') from error
                    if not result.get('ok'):
                        raise RuntimeError(result.get('error', '本地语音合成失败。'))
                    self.stats = result
                    if voice_id.startswith('kokoro_'):
                        return output.read_bytes()
                    settings_path = HOME / 'moss-voices' / 'settings.json'
                    settings = json.loads(settings_path.read_text(encoding='utf-8')) if settings_path.exists() else {}
                    return tune_wav(output.read_bytes(), speed=float(settings.get('speed', 1.0)),
                                    pitch_semitones=float(settings.get('pitch_semitones', 0.0)))
            finally:
                self.active.clear()

    def cancel(self):
        process = self.process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        if process is not None and process.stdin and not process.stdin.closed:
            process.stdin.close()


_voice = LocalVoice()
atexit.register(_voice.cancel)


def synthesize(text, voice_id='moss_wanwan'):
    return _voice.synthesize(text, voice_id)


def cancel():
    if _voice.active.is_set():
        _voice.cancel()


def close():
    _voice.cancel()
