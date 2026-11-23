"""Native CPU synthesis with the pinned multilingual Kokoro model."""

import io
from pathlib import Path
import time
import wave

import numpy as np
import sherpa_onnx

from .voice import VOICES


class KokoroEngine:
    def __init__(self, home):
        folder = Path(home) / 'kokoro-model'
        kokoro = sherpa_onnx.OfflineTtsKokoroModelConfig(
            model=str(folder / 'model.onnx'),
            voices=str(folder / 'voices.bin'),
            tokens=str(folder / 'tokens.txt'),
            data_dir=str(folder / 'espeak-ng-data'),
            dict_dir=str(folder / 'dict'),
            lexicon=','.join(str(folder / name) for name in ('lexicon-us-en.txt', 'lexicon-zh.txt')),
        )
        config = sherpa_onnx.OfflineTtsConfig(
            model=sherpa_onnx.OfflineTtsModelConfig(kokoro=kokoro, num_threads=4, provider='cpu', debug=False),
            rule_fsts=','.join(str(folder / name) for name in ('date-zh.fst', 'number-zh.fst', 'phone-zh.fst')),
            max_num_sentences=1,
        )
        self.tts = sherpa_onnx.OfflineTts(config)

    def synthesize(self, text, voice_id):
        started = time.perf_counter()
        speaker = int(VOICES[voice_id][1].rsplit(':', 1)[1])
        audio = self.tts.generate(text, sid=speaker, speed=1.0)
        pcm = np.round(np.clip(audio.samples, -1, 1) * 32767).astype('<i2')
        if not len(pcm):
            raise RuntimeError('Kokoro 没有生成语音。')
        output = io.BytesIO()
        with wave.open(output, 'wb') as result:
            result.setnchannels(1)
            result.setsampwidth(2)
            result.setframerate(audio.sample_rate)
            result.writeframes(pcm.tobytes())
        return output.getvalue(), {
            'engine': 'kokoro', 'speaker': speaker,
            'elapsed_seconds': round(time.perf_counter() - started, 3),
            'audio_seconds': round(len(pcm) / audio.sample_rate, 3),
        }
