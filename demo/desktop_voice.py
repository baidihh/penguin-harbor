"""Opt-in desktop microphone controls and local Mandarin recognition."""

import json
import queue
import re
import threading
import time


class DesktopVoice:
    def __init__(self, runtime, events):
        self.path = runtime / 'voice-model' / 'vosk-model-small-cn-0.22'
        self.events = events
        self.wake = threading.Event()
        self.dictate = threading.Event()
        self.finish = threading.Event()
        self.worker = None
        self._model = None

    def start(self, wake=False):
        if not (self.path / 'am' / 'final.mdl').is_file():
            return False
        (self.wake if wake else self.dictate).set()
        self.finish.clear()
        if self.worker is None or not self.worker.is_alive():
            self.worker = threading.Thread(target=self._run, daemon=True, name='penguin-microphone')
            self.worker.start()
        return True

    def close(self):
        self.wake.clear()
        self.dictate.clear()
        self.finish.clear()

    @staticmethod
    def input_device(devices, default):
        """Keep a headset microphone from downgrading Bluetooth music playback."""
        headset_names = ('bluetooth', 'hands-free', 'headset', 'headphone', 'oppo', '耳机', '蓝牙')
        selected = devices[default]
        if not any(name in selected['name'].lower() for name in headset_names):
            return default
        for index, device in enumerate(devices):
            name = device['name'].lower()
            if (device['hostapi'] == selected['hostapi'] and device['max_input_channels'] > 0
                    and any(word in name for word in ('microphone', '麦克风'))
                    and not any(word in name for word in headset_names)):
                return index
        return default

    def _run(self):
        try:
            import sounddevice
            from vosk import KaldiRecognizer, Model, SetLogLevel

            SetLogLevel(-1)
            if self._model is None:
                self._model = Model(str(self.path))
            devices = sounddevice.query_devices()
            device = self.input_device(devices, sounddevice.default.device[0])
            rate = int(devices[device]['default_samplerate'])
            audio = queue.Queue(maxsize=30)

            def record(data, frames, when, status):
                try:
                    audio.put_nowait(bytes(data))
                except queue.Full:
                    pass

            recognizer = KaldiRecognizer(self._model, rate)
            with sounddevice.RawInputStream(device=device, samplerate=rate, blocksize=int(rate / 10), dtype='int16',
                                            channels=1, callback=record):
                self._recognize(recognizer, audio)
        except Exception:
            self.close()
            self.events.put(('microphone_error', '麦克风暂不可用，请检查 Windows 默认输入设备和权限。'))

    def _recognize(self, recognizer, audio):
        was_dictating = False
        deadline = 0.0
        while self.wake.is_set() or self.dictate.is_set():
            dictating = self.dictate.is_set()
            if dictating != was_dictating:
                recognizer.Reset()
                was_dictating = dictating
                if dictating:
                    deadline = time.monotonic() + 10
                    self.events.put(('microphone', '正在听你说话…'))

            if dictating and (self.finish.is_set() or time.monotonic() >= deadline):
                self._deliver(json.loads(recognizer.FinalResult()).get('text', ''))
                continue
            try:
                chunk = audio.get(timeout=0.2)
            except queue.Empty:
                continue
            if not recognizer.AcceptWaveform(chunk):
                continue
            words = json.loads(recognizer.Result()).get('text', '')
            if dictating and words:
                self._deliver(words)
            elif not dictating:
                phrase = re.sub(r'[\s，。！？、,.!?]+', '', words)
                for wake in ('你好企鹅', '哈喽企鹅', '嘿企鹅'):
                    if phrase.startswith(wake):
                        self.events.put(('wake', phrase[len(wake):]))
                        break

    def _deliver(self, text):
        self.dictate.clear()
        self.finish.clear()
        self.events.put(('transcript', text.replace(' ', '')))


def open_system_dictation():
    import ctypes

    keys = ctypes.windll.user32.keybd_event
    keys(0x5B, 0, 0, 0)
    keys(0x48, 0, 0, 0)
    keys(0x48, 0, 2, 0)
    keys(0x5B, 0, 2, 0)
