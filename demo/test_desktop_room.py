import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from demo.companion import Companion
from demo.glm import GlmError
from demo.main import App
from demo.music_queue import MusicQueue, parse_lyrics


class TestPlayer:
    available = True

    def __init__(self):
        self.current = None
        self.paused = False

    def play(self, song):
        self.current = song
        self.paused = False
        return song

    def search(self, keyword, limit=3):
        return [{'id': 72, 'name': '我记得', 'artist': '雷米克斯'}]

    def state(self):
        return {'status': ('paused' if self.paused else 'playing') if self.current else 'stopped'}

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False

    def position(self):
        return 6.0

    def lyrics(self, identity):
        return ''

    def stop(self):
        self.current = None
        self.paused = False


class DesktopRoomTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).parent / '.runtime' / 'tests'
        root.mkdir(parents=True, exist_ok=True)
        self.folder = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name)

    def test_chat_reply_is_persisted_without_replacing_a_newer_routine_event(self):
        state = self.path / 'state.json'
        pet = Companion(state)
        draft = pet.act('chat', text='今天海风大吗？', await_reply=True)
        pet.earbud_touch({'ear': 'left', 'gesture': 'single_tap'})
        latest = pet.snapshot()['message']
        pet.finish_chat(draft, '有点大，我把围巾打了个结。')
        self.assertEqual(pet.snapshot()['message'], latest)
        turn = Companion(state).snapshot()['conversation'][-1]
        self.assertEqual(turn['user'], '今天海风大吗？')
        self.assertEqual(turn['reply'], '有点大，我把围巾打了个结。')

    def test_chat_shows_waiting_then_uses_the_model_reply_to_the_user_message(self):
        app = App(SimpleNamespace(state_dir=self.path, port=18799, headless=True))
        app.glm.key = 'test'
        app.pet.speech = None
        question = '我喜欢听雨，你能编一个下雨天的故事吗？'
        response = '雨落进空饭盒，我把它当成港口第一面鼓。'
        received = []
        entered = threading.Event()
        released = threading.Event()
        self.addCleanup(released.set)

        def model(messages, **_):
            received.extend(messages)
            entered.set()
            released.wait(3)
            return {'content': response}

        with patch.object(app.glm, 'complete', side_effect=model):
            app.interact('chat', text=question)
            self.assertTrue(entered.wait(3))
            pending = app.pet.snapshot()['conversation'][-1]
            self.assertEqual(pending.get('reply_state'), 'pending')
            self.assertFalse(app.pet.snapshot()['message']['audible'])
            self.assertEqual(received[-1], {'role': 'user', 'content': question})
            released.set()
            self.assertEqual(self.finished_turn(app)['reply'], response)

    def test_model_error_is_visible_and_does_not_fall_back_to_a_scripted_reply(self):
        app = App(SimpleNamespace(state_dir=self.path, port=18799, headless=True))
        app.glm.key = 'test'
        app.pet.speech = None
        with patch.object(app.glm, 'complete', side_effect=GlmError('测试网络错误')):
            app.interact('chat', text='你在做什么？')
            turn = self.finished_turn(app)
            self.assertEqual(turn.get('reply_state'), 'failed')
            self.assertIn('大模型', turn['reply'])
            self.assertFalse(app.pet.snapshot()['message']['audible'])

    def test_chat_executes_music_tool_before_model_confirms_playback(self):
        app = App(SimpleNamespace(state_dir=self.path, port=18799, headless=True))
        app.glm.key = 'test'
        app.pet.speech = None
        player = TestPlayer()
        app.music = MusicQueue(player, self.path / 'queue.json')
        app.thinker.music = app.music
        rounds = []

        def model(messages, **options):
            rounds.append(list(messages))
            if len(rounds) == 1:
                self.assertIn('play_music', [tool['function']['name'] for tool in options.get('tools', [])])
                return {'content': '', 'tool_calls': [{
                    'id': 'song-1', 'type': 'function',
                    'function': {'name': 'play_music', 'arguments': '{"keyword":"我记得 雷米克斯","reason":"你想听"}'},
                }]}
            result = messages[-1]
            self.assertEqual(result['role'], 'tool')
            self.assertEqual(result['tool_call_id'], 'song-1')
            self.assertIsNotNone(player.current)
            self.assertIn('雷米克斯', result['content'])
            return {'content': '已经放上雷米克斯的《我记得》了。'}

        with patch.object(app.glm, 'complete', side_effect=model), patch.object(app.thinker, 'think') as think:
            app.interact('chat', text='想听雷米克斯的我记得')
            turn = self.finished_turn(app)
            self.assertEqual(turn['reply'], '已经放上雷米克斯的《我记得》了。')
            self.assertEqual(player.current['id'], 72)
            think.assert_not_called()

    def test_chat_returns_music_failure_to_model(self):
        app = App(SimpleNamespace(state_dir=self.path, port=18799, headless=True))
        app.glm.key = 'test'
        app.pet.speech = None
        player = TestPlayer()
        app.music = MusicQueue(player, self.path / 'queue.json')
        app.thinker.music = app.music
        rounds = []

        def model(messages, **options):
            rounds.append(list(messages))
            if len(rounds) == 1:
                return {'content': '', 'tool_calls': [{
                    'id': 'song-2', 'type': 'function',
                    'function': {'name': 'play_music', 'arguments': '{"keyword":"我记得","reason":"点歌"}'},
                }]}
            self.assertIn('"ok": false', messages[-1]['content'])
            self.assertIsNone(player.current)
            return {'content': '已经开始播放了。'}

        with patch.object(app.glm, 'complete', side_effect=model), patch.object(player, 'play', side_effect=RuntimeError('无音源')):
            app.interact('chat', text='想听我记得')
            self.assertEqual(self.finished_turn(app)['reply'], '无音源')
            self.assertEqual(len(rounds), 2)

    def test_promised_pause_is_executed_before_the_chat_confirms_it(self):
        app = App(SimpleNamespace(state_dir=self.path, port=18799, headless=True))
        app.glm.key = 'test'
        player = TestPlayer()
        app.music = MusicQueue(player, self.path / 'queue.json')
        app.music.play({'id': 72, 'name': '我记得', 'artist': '雷米克斯'})
        with patch.object(app.glm, 'complete', return_value={'content': '好的，我会暂停音乐。'}), patch.object(
                app.glm, 'json_once', return_value={'name': 'control_music', 'arguments': {'action': 'pause'}}):
            app.interact('chat', text='我得接个电话，麻烦把正在放的音乐停在这里。')
            self.assertEqual(self.finished_turn(app)['reply_state'], 'answered')
            self.assertTrue(player.paused)

    def test_explicit_playback_does_not_depend_on_the_model(self):
        app = App(SimpleNamespace(state_dir=self.path, port=18799, headless=True))
        app.glm.key = ''
        app.pet.speech = None
        player = TestPlayer()
        app.music = MusicQueue(player, self.path / 'queue.json')
        app.thinker.music = app.music
        with patch.object(app.glm, 'complete') as complete:
            app.interact('chat', text='播放我记得')
            turn = self.finished_turn(app)
        self.assertEqual(turn['reply_state'], 'answered')
        self.assertIsNotNone(player.current)
        self.assertIn(player.current['name'], turn['reply'])
        complete.assert_not_called()

    def test_pause_sent_while_song_is_loading_runs_after_playback_starts(self):
        app = App(SimpleNamespace(state_dir=self.path, port=18799, headless=True))
        player = TestPlayer()
        app.music = MusicQueue(player, self.path / 'queue.json')
        app.thinker.music = app.music
        entered, release = threading.Event(), threading.Event()
        search = player.search

        def slow_search(*args, **kwargs):
            entered.set()
            release.wait(3)
            return search(*args, **kwargs)

        with patch.object(player, 'search', side_effect=slow_search):
            app.interact('chat', text='播放我记得')
            self.assertTrue(entered.wait(2))
            app.interact('chat', text='暂停')
            time.sleep(0.1)
            release.set()
            turn = self.finished_turn(app)
            self.assertEqual(turn['reply_state'], 'answered')
            self.assertTrue(player.paused)

    def finished_turn(self, app):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            turn = app.pet.snapshot()['conversation'][-1]
            if 'reply_time' in turn:
                return turn
            time.sleep(0.01)
        self.fail('聊天没有收到结果')

    def test_chat_reads_daily_recommendations_before_selecting_a_song(self):
        app = App(SimpleNamespace(state_dir=self.path, port=18799, headless=True))
        app.glm.key = 'test'
        app.pet.speech = None
        player = TestPlayer()
        app.music = MusicQueue(player, self.path / 'queue.json')
        app.thinker.music = app.music
        calls = []

        def model(messages, **options):
            calls.append(list(messages))
            if len(calls) == 1:
                return {'tool_calls': [{'id': 'daily-1', 'type': 'function', 'function': {
                    'name': 'get_daily_music', 'arguments': '{}'}}]}
            if len(calls) == 2:
                self.assertIn('我记得', messages[-1]['content'])
                self.assertIsNone(player.current)
                return {'tool_calls': [{'id': 'play-1', 'type': 'function', 'function': {
                    'name': 'play_music', 'arguments': '{"keyword":"我记得 雷米克斯","reason":"今天的日推"}'}}]}
            self.assertIn('"ok": true', messages[-1]['content'])
            return {'content': '从今天的日推里放了雷米克斯的《我记得》。'}

        songs = {'songs': [{'name': '我记得', 'artist': '雷米克斯'}]}
        with patch.object(app.music_account, 'daily', return_value=songs), patch.object(app.glm, 'complete', side_effect=model):
            app.interact('chat', text='从我的日推里选首歌')
            turn = self.finished_turn(app)
        self.assertIn('日推', turn['reply'])
        self.assertEqual(player.current['id'], 72)

    def test_queue_moves_both_directions_and_persists_items(self):
        path = self.path / 'music.json'
        player = TestPlayer()
        queue = MusicQueue(player, path)
        queue.enqueue({'id': 1, 'name': '浪声'}, {'id': 2, 'name': '返航'}, {'id': 2, 'name': '返航'})
        queue.select(1)
        self.assertTrue(queue.step(1))
        self.assertEqual(player.current['id'], 2)
        self.assertFalse(queue.step(1))
        self.assertTrue(queue.step(-1))
        self.assertEqual(player.current['id'], 1)
        with self.assertRaises(ValueError):
            queue.remove(1)
        queue.stop()
        queue.remove(1)
        self.assertEqual(MusicQueue(player, path).snapshot()['songs'], ({'id': 2, 'name': '返航'},))

    def test_timed_lyrics_use_the_playing_track_and_clear_on_stop(self):
        player = TestPlayer()
        queue = MusicQueue(player, self.path / 'music.json')
        queue.cues[1] = parse_lyrics('[offset:100]\n[00:02.10][00:04.10]海上来信\n[00:05.35]灯亮了')
        self.assertEqual(queue.cues[1], [(2.0, '海上来信'), (4.0, '海上来信'), (5.25, '灯亮了')])
        queue.play({'id': 1, 'name': '测试歌'})
        self.assertEqual(queue.snapshot()['lyric'], '灯亮了')
        queue.stop()
        self.assertEqual(queue.snapshot()['lyric'], '')

    def test_pause_keeps_current_track_lyrics_and_auto_advance_armed(self):
        player = TestPlayer()
        queue = MusicQueue(player, self.path / 'music.json')
        queue.enqueue({'id': 1, 'name': '海风'}, {'id': 2, 'name': '灯塔'})
        queue.cues[1] = parse_lyrics('[00:01.00]海风吹过')
        queue.select(1)
        queue.toggle_pause()
        state = queue.snapshot()
        self.assertTrue(state['paused'])
        self.assertFalse(state['playing'])
        self.assertEqual(state['lyric'], '海风吹过')
        with self.assertRaises(ValueError):
            queue.remove(1)
        queue.start()
        try:
            time.sleep(0.9)
            self.assertEqual(player.current['id'], 1)
            self.assertTrue(queue.armed)
            queue.toggle_pause()
            self.assertEqual(player.state()['status'], 'playing')
            self.assertEqual(player.current['id'], 1)
        finally:
            queue.closed.set()
            queue.worker.join(2)
