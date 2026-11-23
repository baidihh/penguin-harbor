"""Neural speech for the penguin's scripted lines."""

import asyncio
import logging
from functools import lru_cache
from pathlib import Path
import subprocess
import sys
import tempfile
import threading


VOICES = {
    'hsiaochen': ('晓臻 · 台湾轻柔女声', 'zh-TW-HsiaoChenNeural', '+2Hz'),
    'hsiaoyu': ('晓雨 · 台湾清甜女声', 'zh-TW-HsiaoYuNeural', '+0Hz'),
    'xiaoxiao': ('晓晓 · 普通话活泼女声', 'zh-CN-XiaoxiaoNeural', '+0Hz'),
    'yunjian': ('云健 · 普通话男声', 'zh-CN-YunjianNeural', '+0Hz'),
    'moss_wanwan': ('湾湾参考 · 本地实验女声', 'local:moss', '+0Hz'),
    'kokoro_xiaobei': ('小北 · Kokoro 中文女声', 'local:kokoro:45', '+0Hz'),
    'kokoro_xiaoni': ('小妮 · Kokoro 中文女声', 'local:kokoro:46', '+0Hz'),
    'kokoro_yunxi': ('云希 · Kokoro 中文男声', 'local:kokoro:50', '+0Hz'),
    'kokoro_yunyang': ('云扬 · Kokoro 中文男声', 'local:kokoro:52', '+0Hz'),
    'kokoro_heart': ('Heart · Kokoro 英文女声', 'local:kokoro:3', '+0Hz'),
}
DEFAULT_VOICE = 'hsiaochen'
PREVIEW_LINE = '欸，你来啦。今天海面好亮喔。要不要陪我去码头走走？'
VOICE_PREVIEWS = dict.fromkeys(
    ('kokoro_xiaobei', 'kokoro_xiaoni', 'kokoro_yunxi', 'kokoro_yunyang'),
    '你好呀，我在这里陪你。今天想聊些什么？',
)
VOICE_PREVIEWS['kokoro_heart'] = 'Hello, I am your little penguin. I am happy to see you.'


@lru_cache(maxsize=32)
def synthesize(voice_id, text):
    _, speaker, pitch = VOICES[voice_id]
    if speaker == 'local:moss':
        from .local_voice import synthesize as local_synthesize
        return local_synthesize(text)
    if speaker.startswith('local:kokoro:'):
        from .local_voice import synthesize as local_synthesize
        return local_synthesize(text, voice_id=voice_id)
    import edge_tts

    async def collect():
        stream = edge_tts.Communicate(text, speaker, rate='+0%', pitch=pitch, connect_timeout=5, receive_timeout=8)
        return b''.join([chunk['data'] async for chunk in stream.stream() if chunk['type'] == 'audio'])

    async def timed_collect():
        # Keep a network outage from delaying the next spoken line indefinitely.
        return await asyncio.wait_for(collect(), timeout=16)

    return asyncio.run(timed_collect())


class MacVoice:
    """Play only the latest requested line and stop promptly on mute."""

    def __init__(self, runtime=None):
        self.runtime = runtime
        self.error = ''
        self.condition = threading.Condition()
        self.pending = None
        self.generation = 0
        self.process = None
        self.stopped = False
        threading.Thread(target=self.run, name='penguin-voice', daemon=True).start()

    def launch_player(self, sample):
        return subprocess.Popen(['/usr/bin/afplay', str(sample)])

    def _cancel(self):
        from .local_voice import cancel
        cancel()
        self.generation += 1
        self.pending = None
        self.error = ''
        if self.process and self.process.poll() is None:
            self.process.terminate()

    def say(self, voice_id, text):
        with self.condition:
            self._cancel()
            self.pending = (self.generation, voice_id, text)
            self.condition.notify()

    def cancel(self):
        with self.condition:
            self._cancel()

    def close(self):
        with self.condition:
            self._cancel()
            self.stopped = True
            self.condition.notify()
        from .local_voice import close
        close()

    def run(self):
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.stopped or self.pending is not None)
                if self.stopped:
                    return
                generation, voice_id, text = self.pending
                self.pending = None
            try:
                audio = synthesize(voice_id, text)
                if not audio:
                    raise ValueError('No speech audio')
                with tempfile.TemporaryDirectory(prefix='penguin-voice-', dir=self.runtime) as directory:
                    suffix = '.wav' if audio.startswith(b'RIFF') else '.mp3'
                    sample = Path(directory) / ('reply' + suffix)
                    sample.write_bytes(audio)
                    with self.condition:
                        if generation != self.generation:
                            continue
                        process = self.launch_player(sample)
                        self.process = process
                    status = process.wait()
                    with self.condition:
                        self.process = None
                        if status and generation == self.generation:
                            self.error = '语音播放失败，可以再试一次。'
            except Exception:
                logging.exception("Voice synthesis or playback failed for %s", voice_id)
                with self.condition:
                    if generation == self.generation:
                        self.error = ('本地语音合成失败，请查看 moss-worker.log。' if VOICES[voice_id][1].startswith('local:')
                                      else '神经语音暂时连不上，请检查网络后重试。')


class WindowsVoice(MacVoice):
    """Play the selected neural voice directly through Windows audio."""

    def launch_player(self, sample):
        if sample.suffix.lower() == '.wav':
            return subprocess.Popen([
                sys.executable, '-c',
                'import sys, winsound; winsound.PlaySound(sys.argv[1], winsound.SND_FILENAME | winsound.SND_NODEFAULT)',
                str(sample),
            ], creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return subprocess.Popen([
            'powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
            '-File', str(Path(__file__).with_name('play_audio.ps1')), '-Path', str(sample),
        ], creationflags=subprocess.CREATE_NO_WINDOW)
