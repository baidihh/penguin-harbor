"""Torch-free host for the pinned MOSS ONNX core and reusable voice codes."""

import importlib.util
import io
import json
from pathlib import Path
import re
import sys
import time
import wave

import numpy as np
import sentencepiece


def load_runtime(home):
    core_path = Path(home) / 'moss-source' / 'ort_cpu_runtime.py'
    spec = importlib.util.spec_from_file_location('penguin_moss_ort', core_path)
    core = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = core
    spec.loader.exec_module(core)
    return core.OrtCpuRuntime(Path(home) / 'moss-models', thread_count=4, max_new_frames=375)


def prepare_voice(home, reference):
    home = Path(home)
    runtime = load_runtime(home)
    with wave.open(str(reference), 'rb') as audio:
        if audio.getnchannels() != 2 or audio.getframerate() != 48000 or audio.getsampwidth() != 2:
            raise ValueError('参考音频需要为 48 kHz、双声道、16 位 WAV。')
        waveform = np.frombuffer(audio.readframes(audio.getnframes()), dtype='<i2').astype(np.float32) / 32768
    waveform = np.ascontiguousarray(waveform.reshape(-1, 2).T[None])
    session = runtime.sessions['codec_encode']
    outputs = session.run(None, {'waveform': waveform,
                                'input_lengths': np.array([waveform.shape[-1]], dtype=np.int32)})
    result = dict(zip([item.name for item in session.get_outputs()], outputs))
    length = int(result['audio_code_lengths'].reshape(-1)[0])
    codes = result['audio_codes'][0, :length].astype(int).tolist()
    record = json.loads((home / 'moss-install.json').read_text(encoding='utf-8'))
    voice = {'format_version': 1, 'engine': 'MOSS-TTS-Nano', 'sample_rate': 48000,
             'audio_codes': codes, 'models': record['models']}
    target = home / 'moss-voices' / 'wanwan-evaluation.json'
    target.parent.mkdir(exist_ok=True)
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps(voice, ensure_ascii=False), encoding='utf-8')
    temporary.replace(target)


class MossEngine:
    def __init__(self, home):
        self.home = Path(home)
        self.runtime = load_runtime(home)
        model = self.runtime.manifest['model_files']['tokenizer_model']
        self.tokenizer = sentencepiece.SentencePieceProcessor(
            model_file=str(self.runtime.resolve_manifest_relative_path(model)))
        voice = json.loads((self.home / 'moss-voices' / 'wanwan-evaluation.json').read_text(encoding='utf-8'))
        installed = json.loads((self.home / 'moss-install.json').read_text(encoding='utf-8'))
        if voice.get('models') and voice['models'] != installed['models']:
            raise ValueError('音色编码与模型版本不匹配，请重新准备参考音频。')
        self.codes = voice['audio_codes']
        self.sample_rate = int(self.runtime.codec_meta['codec_config']['sample_rate'])

    def synthesize(self, text):
        started = time.perf_counter()
        pieces = []
        pending = []
        first_audio = None
        generated = 0

        def flush():
            nonlocal first_audio
            decoded = self.runtime.codec_streaming_session.run_frames(pending)
            pending.clear()
            if decoded is not None:
                audio, length = decoded
                if length > 0:
                    if first_audio is None:
                        first_audio = time.perf_counter() - started
                    pieces.append(audio[0, :, :length].T)

        def on_frame(frames, index, frame):
            pending.append(frame)
            if len(pending) >= (1 if first_audio is None else 4):
                flush()

        sentences = re.findall(r'[^。！？!?\n]+[。！？!?]?', text.strip())
        for sentence in sentences:
            tokens = self.tokenizer.encode(sentence, out_type=int)
            for start in range(0, len(tokens), 75):
                self.runtime.codec_streaming_session.reset()
                request = self.runtime.build_voice_clone_request_rows(self.codes, tokens[start:start + 75])
                frames = self.runtime.generate_audio_frames(request, on_frame=on_frame)
                if len(frames) >= 375:
                    raise RuntimeError('本地语音没有正常结束，请缩短句子后重试。')
                generated += len(frames)
                if pending:
                    flush()
        if not pieces:
            raise RuntimeError('本地模型没有生成语音。')
        waveform = np.concatenate(pieces, axis=0)
        pcm = np.round(np.clip(waveform, -1, 1) * 32767).astype('<i2')
        output = io.BytesIO()
        with wave.open(output, 'wb') as audio:
            audio.setnchannels(pcm.shape[1])
            audio.setsampwidth(2)
            audio.setframerate(self.sample_rate)
            audio.writeframes(pcm.tobytes())
        return output.getvalue(), {
            'first_audio_seconds': round(first_audio, 3),
            'elapsed_seconds': round(time.perf_counter() - started, 3),
            'audio_seconds': round(len(pcm) / self.sample_rate, 3),
            'frames': generated,
        }
