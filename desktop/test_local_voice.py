import io
import math
from pathlib import Path
import struct
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import wave

from desktop import voice
from desktop import local_voice
from desktop.companion import Companion
from desktop.netease import find_ffmpeg
from desktop.moss_download import download
from desktop.test_native_voice import FileVoice


def sample_wav():
    output = io.BytesIO()
    with wave.open(output, 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(48000)
        audio.writeframes(b'\x00\x00' * 2400)
    return output.getvalue()


class LocalVoiceTests(unittest.TestCase):
    def test_voice_assets_with_spaces_in_filename_download_successfully(self):
        root = Path(__file__).parent / '.runtime' / 'tests'
        root.mkdir(parents=True, exist_ok=True)
        def fetch(request, timeout):
            self.assertIn('Mr%20serious', request.full_url)
            return io.BytesIO(b'abc')
        with tempfile.TemporaryDirectory(dir=root) as directory:
            with patch('desktop.moss_download.urllib.request.urlopen', side_effect=fetch):
                download('example/model', 'pinned', {'path': 'voices/Mr serious', 'size': 3}, Path(directory))
            self.assertEqual((Path(directory) / 'voices/Mr serious').read_bytes(), b'abc')

    @unittest.skipUnless(find_ffmpeg(), 'Audio shaping requires ffmpeg')
    def test_local_tuning_speeds_up_speech_and_lifts_pitch_independently(self):
        output = io.BytesIO()
        pcm = b''.join(struct.pack('<h', round(12000 * math.sin(2 * math.pi * 440 * index / 48000)))
                       for index in range(48000 * 2))
        with wave.open(output, 'wb') as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(48000)
            audio.writeframes(pcm)
        tuned = local_voice.tune_wav(output.getvalue(), speed=1.1, pitch_semitones=0.5)
        with wave.open(io.BytesIO(tuned)) as audio:
            rate = audio.getframerate()
            duration = audio.getnframes() / rate
            samples = struct.unpack('<' + 'h' * audio.getnframes(), audio.readframes(audio.getnframes()))
        self.assertAlmostEqual(duration, 2 / 1.1, delta=0.06)
        middle = samples[rate // 4:rate * 5 // 4]
        crossings = sum(left <= 0 < right for left, right in zip(middle, middle[1:]))
        self.assertAlmostEqual(crossings, 453, delta=4)

    def test_local_selection_returns_the_local_engine_audio(self):
        expected = sample_wav()
        backend = SimpleNamespace(synthesize=lambda text: expected)
        voice.synthesize.cache_clear()
        with patch.dict(sys.modules, {'desktop.local_voice': backend}):
            result = voice.synthesize('moss_wanwan', '你好，今天海面好亮。')
        self.assertEqual(result, expected)
        voice.synthesize.cache_clear()

    def test_kokoro_selection_reaches_the_requested_local_speaker(self):
        expected = sample_wav()
        def generate(text, voice_id):
            self.assertEqual(voice_id, 'kokoro_xiaobei')
            return expected
        voice.synthesize.cache_clear()
        with patch.dict(sys.modules, {'desktop.local_voice': SimpleNamespace(synthesize=generate)}):
            self.assertEqual(voice.synthesize('kokoro_xiaobei', '今天想听什么？'), expected)
        voice.synthesize.cache_clear()

    def test_english_voice_preview_uses_english_and_persists_selection(self):
        root = Path(__file__).parent / '.runtime' / 'tests'
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as directory:
            path = Path(directory) / 'pet.json'
            pet = Companion(path)
            pet.act('voice_select', value='kokoro_heart')
            preview = pet.act('voice_preview')
            self.assertIn('Hello', preview['speech'])
            self.assertTrue(preview['preview'])
            self.assertEqual(Companion(path).snapshot()['voice_id'], 'kokoro_heart')

    def test_local_wav_reaches_player_with_a_wav_filename(self):
        root = Path(__file__).parent / '.runtime' / 'tests'
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as directory:
            class WavePlayer(FileVoice):
                def launch_player(self, path):
                    self.suffix = path.suffix
                    return super().launch_player(path)

            player = WavePlayer(directory)
            expected = sample_wav()
            try:
                with patch('desktop.voice.synthesize', return_value=expected):
                    player.say('moss_wanwan', '你好')
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline and not player.output.exists():
                        time.sleep(0.02)
                    self.assertTrue(player.output.exists(), player.error)
                    self.assertEqual(player.output.read_bytes(), expected)
                    self.assertEqual(player.suffix, '.wav')
            finally:
                player.close()
