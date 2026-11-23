"""Earbud replies must reach the desktop player without a browser."""

from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from demo.main import App
from demo.voice import MacVoice


class FileVoice(MacVoice):
    """Replace only the OS audio player with a real file-reading child process."""

    def __init__(self, runtime):
        self.output = Path(runtime) / 'played-audio.bin'
        self.finished = threading.Event()
        super().__init__(runtime)

    def run(self):
        try:
            super().run()
        finally:
            self.finished.set()

    def close(self):
        super().close()
        self.finished.wait(5)

    def launch_player(self, sample):
        return subprocess.Popen([
            sys.executable, '-c',
            'from pathlib import Path; import sys; Path(sys.argv[2]).write_bytes(Path(sys.argv[1]).read_bytes())',
            str(sample), str(self.output),
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class NativeVoiceTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).parent / '.runtime' / 'tests'
        root.mkdir(parents=True, exist_ok=True)
        self.folder = tempfile.TemporaryDirectory(dir=root)
        self.assertTrue(Path(self.folder.name).resolve().is_relative_to(root.resolve()))
        self.addCleanup(self.folder.cleanup)
        self.app = App(SimpleNamespace(state_dir=self.folder.name, port=18799, headless=False))
        # 测试只关心桌面播放链路，不要让 GLM 真的发网络请求。
        self.app.glm.key = ''
        self.app.pet.speech = None
        self.addCleanup(self.app.stop)

    def test_earbud_reply_plays_selected_voice_without_browser(self):
        self.assertTrue(callable(getattr(self.app, 'start_native_voice', None)), 'Windows desktop playback is missing')
        self.app.pet.act('voice_select', value='xiaoxiao')
        self.app.pet.act('voice', enabled=True)
        with patch('demo.main.WindowsVoice', FileVoice, create=True), patch('demo.main.sys.platform', 'win32'), patch(
            'demo.voice.synthesize', side_effect=lambda voice, text: (voice + '\n' + text).encode('utf-8')
        ):
            self.app.start_native_voice()
            self.app.pet.earbud_touch({'ear': 'right', 'gesture': 'double_tap'})
            speech = self.app.pet.snapshot()['message']['speech']
            self.assertTrue(speech, '右耳双击应当产生一句回应')
            expected = ('xiaoxiao\n' + speech).encode('utf-8')
            output = Path(self.folder.name) / 'played-audio.bin'
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if output.exists() and output.read_bytes() == expected:
                    break
                time.sleep(0.03)
            self.assertTrue(output.exists(), self.app.voice.error)
            self.assertEqual(output.read_bytes(), expected)
            self.app.stop()

    def test_headless_room_keeps_browser_playback(self):
        self.assertTrue(callable(getattr(self.app, 'start_native_voice', None)), 'Native playback initialization is missing')
        self.app.options.headless = True
        self.app.start_native_voice()
        self.assertIsNone(self.app.voice)

    def test_closed_case_cancels_speech_until_ears_are_worn_again(self):
        self.app.pet.act('voice', enabled=True)
        with patch('demo.main.WindowsVoice', FileVoice), patch('demo.main.sys.platform', 'win32'), patch(
            'demo.voice.synthesize', side_effect=lambda voice, text: text.encode('utf-8')
        ):
            self.app.pet.simulate_case('lid_close')
            self.app.start_native_voice()
            self.assertTrue(self.app.stopped.wait(0.4) is False)
            output = Path(self.folder.name) / 'played-audio.bin'
            self.assertFalse(output.exists(), 'Closing the case must keep desktop speech silent')
            self.app.pet.simulate_case('wear')
            expected = self.app.pet.snapshot()['message']['speech'].encode('utf-8')
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not output.exists():
                time.sleep(0.03)
            self.assertTrue(output.exists(), self.app.voice.error)
            self.assertEqual(output.read_bytes(), expected)
            self.app.stop()


if __name__ == '__main__':
    unittest.main()
