"""记忆、插件沙箱、音乐接入与思考循环的测试。"""

import json
from pathlib import Path
import tempfile
import unittest

from demo.companion import Companion
from demo.memory import Memory
from demo.netease import NetEase, NoPlayableSource
from demo.plugin import Plugin, PluginError
from demo.thinker import Thinker


class FakeGlm:
    def __init__(self, message=None, ready=True):
        self.message = message or {'content': '我在这儿。'}
        self.ready = ready
        self.calls = []
        self.replies = None

    def complete(self, messages, tools=None, **kwargs):
        self.calls.append({'messages': messages, 'tools': tools})
        if self.replies:
            index = min(len(self.calls) - 1, len(self.replies) - 1)
            return self.replies[index]
        return self.message


class FakeMusic:
    available = True

    def __init__(self, songs=None):
        self.songs = songs if songs is not None else [{'id': 108914, 'name': '江南', 'artist': '林俊杰'}]
        self.played = []

    def search(self, keyword, limit=5):
        self.keyword = keyword
        return self.songs

    def play(self, song):
        self.played.append(song)
        return song


class BrainTestCase(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).parent / '.runtime' / 'mind-tests'
        root.mkdir(parents=True, exist_ok=True)
        self.folder = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.pet = Companion(self.root / 'state.json')
        self.pet.speech = None
        self.memory = Memory(self.root / 'memory')


class MemoryTests(BrainTestCase):
    def test_note_files_stay_bounded(self):
        for index in range(200):
            self.memory.write_self(f'第 {index} 件小事，海风从北边吹过来。')
        self.assertLessEqual(len(self.memory.self_notes()), 1300)
        self.assertIn('第 199 件小事', self.memory.self_notes())

    def test_events_are_trimmed(self):
        for index in range(620):
            self.memory.record('thought', text=f'想法 {index}')
        kept = self.memory.recent(1000)
        self.assertLessEqual(len(kept), 500)
        self.assertIn('619', kept[-1]['text'].replace('想法 ', ''))

    def test_events_are_appended_and_read_back(self):
        self.memory.record('chat', text='今天很累')
        self.memory.record('music', title='江南', reason='想安静一下')
        recent = self.memory.recent(10)
        self.assertEqual([item['kind'] for item in recent], ['chat', 'music'])
        self.assertEqual(recent[0]['text'], '今天很累')

    def test_self_and_user_notes_accumulate(self):
        self.memory.write_self('我发现自己喜欢下雪天。')
        self.memory.write_self('擦灯的时候最安心。')
        self.memory.write_user('来访者常常在晚上来。')
        self.assertIn('喜欢下雪天', self.memory.self_notes())
        self.assertIn('擦灯的时候最安心', self.memory.self_notes())
        self.assertIn('晚上来', self.memory.user_profile())

    def test_behavior_is_persisted_and_bounded(self):
        self.memory.set_behavior('think_interval', 300)
        reopened = Memory(self.root / 'memory')
        self.assertEqual(reopened.behavior()['think_interval'], 300)
        with self.assertRaises(ValueError):
            self.memory.set_behavior('think_interval', 5)
        with self.assertRaises(ValueError):
            self.memory.set_behavior('unknown', 1)

    def test_music_switch_can_be_turned_off(self):
        self.memory.set_behavior('music_enabled', False)
        self.assertFalse(self.memory.behavior()['music_enabled'])


class PluginTests(BrainTestCase):
    def test_plugin_runs_and_sees_real_state(self):
        plugin = Plugin(self.root / 'observe.py')
        plugin.write('def observe(state):\n    return state["activity_label"]\n')
        self.assertEqual(plugin.run({'activity_label': '在擦信号灯'}), '在擦信号灯')

    def test_plugin_cannot_import_or_touch_files(self):
        plugin = Plugin(self.root / 'observe.py')
        for source in ('import os\ndef observe(s):\n    return 1\n',
                       'def observe(s):\n    return open("x").read()\n',
                       'def observe(s):\n    return __import__("os").getcwd()\n'):
            with self.subTest(source=source):
                with self.assertRaises(PluginError):
                    plugin.write(source)

    def test_broken_plugin_reports_instead_of_crashing(self):
        plugin = Plugin(self.root / 'observe.py')
        plugin.write('def observe(state):\n    return state["missing"]\n')
        self.assertIsNone(plugin.run({}))
        self.assertIn('KeyError', plugin.error)

    def test_plugin_without_hook_is_ignored(self):
        plugin = Plugin(self.root / 'observe.py')
        plugin.write('value = 1\n')
        self.assertIsNone(plugin.run({}))
        self.assertEqual(plugin.error, '')


class ThinkerTests(BrainTestCase):
    def build(self, message, music=None):
        glm = FakeGlm(message)
        thinker = Thinker(self.pet, self.memory, glm, music or FakeMusic())
        return thinker, glm

    def test_thought_is_recorded_and_spoken(self):
        thinker, _ = self.build({'content': '海面今天很亮。'})
        result = thinker.think()
        self.assertEqual(result['text'], '海面今天很亮。')
        self.assertIn('海面今天很亮', self.memory.self_notes() + self.pet.snapshot()['message']['text'])
        self.assertEqual(self.memory.recent(5)[-1]['kind'], 'thought')

    def test_music_tool_searches_and_plays(self):
        music = FakeMusic()
        call = {'function': {'name': 'play_music',
                             'arguments': json.dumps({'keyword': '江南 林俊杰', 'reason': '想起岸上'})}}
        thinker, _ = self.build({'content': '', 'tool_calls': [call]}, music)
        result = thinker.think()
        self.assertEqual(music.played[0]['id'], 108914)
        self.assertEqual(result['actions'][0]['title'], '江南 - 林俊杰')
        self.assertEqual(self.memory.recent(5)[-1]['kind'], 'music')
        self.assertIn('江南', json.dumps(self.pet.snapshot()['journal'], ensure_ascii=False))

    def test_music_respects_cooldown(self):
        music = FakeMusic()
        call = {'function': {'name': 'play_music', 'arguments': json.dumps({'keyword': '海', 'reason': '安静'})}}
        thinker, _ = self.build({'content': '', 'tool_calls': [call]}, music)
        thinker.think()
        thinker.think()
        self.assertEqual(len(music.played), 1)
        self.assertEqual(music.played[0]['id'], 108914)

    def test_music_switch_off_blocks_playback(self):
        music = FakeMusic()
        self.memory.set_behavior('music_enabled', False)
        call = {'function': {'name': 'play_music', 'arguments': json.dumps({'keyword': '海', 'reason': '安静'})}}
        thinker, _ = self.build({'content': '', 'tool_calls': [call]}, music)
        thinker.think()
        self.assertEqual(music.played, [])

    def test_requested_song_ignores_cooldown(self):
        music = FakeMusic([{'id': 10, 'name': '小星星', 'artist': '歌手'}])
        call = {'function': {'name': 'play_music', 'arguments': json.dumps({'keyword': '小星星', 'reason': '他想听'})}}
        thinker, _ = self.build({'content': '', 'tool_calls': [call]}, music)
        thinker.think()
        thinker.think('来访者刚点了歌：「放一首小星星」')
        self.assertEqual(len(music.played), 2)

    def test_song_word_detects_request(self):
        from demo.main import _song_trigger
        self.assertEqual(_song_trigger('放一首小星星', '随便'), '来访者刚点了歌：「放一首小星星」')
        self.assertEqual(_song_trigger('今天下雨了', '随便'), '随便')

    def test_tool_can_change_behavior(self):
        call = {'function': {'name': 'tune_behavior',
                             'arguments': json.dumps({'key': 'talkativeness', 'value': 1.6})}}
        thinker, _ = self.build({'content': '', 'tool_calls': [call]})
        thinker.think()
        self.assertEqual(self.memory.behavior()['talkativeness'], 1.6)

    def test_user_fact_and_self_note_tools_write_memory(self):
        calls = [
            {'function': {'name': 'remember_user', 'arguments': json.dumps({'fact': '来访者养猫'})}},
            {'function': {'name': 'write_self_note', 'arguments': json.dumps({'note': '我记住了猫'})}},
        ]
        thinker, _ = self.build({'content': '', 'tool_calls': calls})
        thinker.think()
        self.assertIn('养猫', self.memory.user_profile())
        self.assertIn('猫', self.memory.self_notes())

    def test_plugin_tool_stores_code(self):
        code = 'def observe(state):\n    return state["day"]\n'
        call = {'function': {'name': 'write_plugin', 'arguments': json.dumps({'code': code})}}
        thinker, _ = self.build({'content': '', 'tool_calls': [call]})
        thinker.think()
        self.assertIn('observe', (self.root / 'memory' / 'observe.py').read_text(encoding='utf-8'))

    def test_plugin_tool_documents_real_activity_keys(self):
        thinker, glm = self.build({'content': ''})
        thinker.think()
        description = json.dumps(glm.calls[0]['tools'], ensure_ascii=False)
        self.assertIn('lantern', description)
        self.assertIn('activity_label', description)

    def test_plugin_error_is_fed_back_on_next_thought(self):
        Plugin(self.root / 'memory' / 'observe.py').write(
            'def observe(state):\n    return state["visitor_present"]\n')
        thinker, glm = self.build({'content': ''})
        thinker.think()
        context = json.dumps(glm.calls[0]['messages'], ensure_ascii=False)
        self.assertIn('KeyError', context)

    def test_silent_tool_call_gets_a_spoken_line(self):
        call = {'function': {'name': 'tune_behavior',
                             'arguments': json.dumps({'key': 'curiosity', 'value': 0.9})}}
        thinker, glm = self.build({'content': '', 'tool_calls': [call]})
        glm.replies = [{'content': '', 'tool_calls': [call]}, {'content': '灯还亮着，你放心。'}]
        result = thinker.think()
        self.assertEqual(result['text'], '灯还亮着，你放心。')
        self.assertEqual(self.pet.snapshot()['message']['text'], '灯还亮着，你放心。')
        self.assertEqual(len(glm.calls), 2)

    def test_repeated_parameter_tuning_still_produces_a_line(self):
        call = {'function': {'name': 'tune_behavior',
                             'arguments': json.dumps({'key': 'curiosity', 'value': 1.0})}}
        thinker, glm = self.build({'content': '', 'tool_calls': [call]})
        glm.replies = [{'content': '海风有点凉。', 'tool_calls': [call]},
                       {'content': '海风有点凉。'}]
        result = thinker.think()
        self.assertEqual(result['text'], '海风有点凉。')
        self.assertEqual(len(glm.calls), 2)

    def test_context_leak_is_stripped_from_the_line(self):
        leaked = '灯擦亮了些。\n调用工具：{"index":0}\n当前状态：- 正在做的事：擦信号灯'
        thinker, _ = self.build({'content': leaked})
        result = thinker.think()
        self.assertEqual(result['text'], '灯擦亮了些。')

    def test_repeated_line_triggers_a_fresh_attempt(self):
        thinker, glm = self.build({'content': '灯擦亮了些。'})
        glm.replies = [{'content': '灯擦亮了些。'}, {'content': '灯擦亮了些。'},
                       {'content': '海雾薄了一点。'}]
        thinker.think()
        result = thinker.think()
        self.assertEqual(result['text'], '海雾薄了一点。')

    def test_glm_failure_keeps_penguin_quiet(self):
        class Broken(FakeGlm):
            def complete(self, messages, tools=None, **kwargs):
                from demo.glm import GlmError
                raise GlmError('连不上')
        thinker = Thinker(self.pet, self.memory, Broken(), FakeMusic())
        before = self.pet.snapshot()['message']['id']
        self.assertIsNone(thinker.think())
        self.assertEqual(thinker.last_error, '连不上')
        self.assertEqual(self.pet.snapshot()['message']['id'], before)


class NetEaseTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = folder.name

    def test_state_starts_stopped(self):
        music = NetEase(folder=self.folder)
        self.assertEqual(music.state(), {'status': 'stopped', 'title': ''})

    def test_play_without_audio_source_is_rejected(self):
        class NoSource(NetEase):
            def stream_url(self, song_id, bitrate=128000):
                return ''
        music = NoSource(ffmpeg='ffmpeg', folder=self.folder)
        with self.assertRaises(RuntimeError):
            music.play({'id': 1, 'name': '没有版权的歌'})

    def test_stop_is_safe_when_nothing_plays(self):
        music = NetEase(folder=self.folder)
        music.stop()
        self.assertEqual(music.state()['status'], 'stopped')


if __name__ == '__main__':
    unittest.main()


class AccountPlaybackTests(BrainTestCase):
    def test_explicit_collection_commands_are_distinct_from_questions_and_song_selection(self):
        from demo.netease_account import requested_collection

        for text in ('放我今天的日推', '放那个日推的歌曲', '帮我播放网易云每日推荐'):
            self.assertEqual(requested_collection(text), 'daily')
        self.assertEqual(requested_collection('放我的红心歌曲。'), 'liked')
        self.assertIsNone(requested_collection('你能播放日推吗？'))
        self.assertIsNone(requested_collection('放日推里的第二首'))

    def test_explicit_netease_song_requests_keep_the_actual_keyword(self):
        from demo.netease_account import requested_song

        self.assertEqual(requested_song('请在网易云放一首小星星，直接播放'), '小星星')
        self.assertEqual(requested_song('帮我播放周杰伦的《晴天》'), '晴天 周杰伦')
        self.assertEqual(requested_song('播放《晴天》'), '晴天')
        self.assertEqual(requested_song('放网易云的歌曲'), '')
        self.assertIsNone(requested_song('你能播放日推吗？'))
        self.assertIsNone(requested_song('播放红心歌单里的第二首'))
        self.assertIsNone(requested_song('放下手机'))

    def test_chat_recognizes_generic_music_requests_and_plain_artist_title(self):
        from demo.netease_account import requested_song
        for request in ('放一首歌吧', '随便来首歌', '帮我放点音乐'):
            self.assertEqual(requested_song(request), '')
        self.assertEqual(requested_song('放一首赵雷的我记得吧'), '赵雷的我记得')
        self.assertIsNone(requested_song('播放下一首'))
        self.assertIsNone(requested_song('放一首你刚刚提到的歌'))

    def test_daily_playback_uses_original_ids_and_queues_remaining_tracks(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from demo.main import App
        from demo.music_queue import MusicQueue

        songs = [{'id': 10, 'name': '第一首', 'artist': '甲'},
                 {'id': 20, 'name': '第二首', 'artist': '乙'},
                 {'id': 30, 'name': '第三首', 'artist': '丙'}]
        player = Mock()
        player.play.side_effect = [NoPlayableSource('没有音源'), True]
        player.lyrics.return_value = ''
        music = MusicQueue(player, self.root / 'queue.json')
        music.enqueue({'id': 40, 'name': '原来的歌', 'artist': ''}, songs[1])
        account = Mock()
        account.daily.return_value = {'songs': songs}
        app = SimpleNamespace(music_account=account, music=music, memory=self.memory, pet=self.pet)
        app.play_account_music = lambda load, song_id: App.play_account_music(app, load, song_id)
        result = App.chat_tool(app, 'play_daily_music', {})
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['title'], '第二首 - 乙')
        self.assertEqual([call.args[0]['id'] for call in player.play.call_args_list], [10, 20])
        self.assertEqual([song['id'] for song in music.songs], [20, 30, 40])
        player.search.assert_not_called()

    def test_liked_playback_respects_selected_id(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from demo.main import App

        account = Mock()
        track = {'id': 20, 'name': '想听的歌', 'artist': '乙'}
        account.liked.return_value = {'songs': [{'id': 10, 'name': '别的歌', 'artist': '甲'}, track]}
        music = Mock()
        music.play_collection.return_value = track
        app = SimpleNamespace(music_account=account, music=music, memory=self.memory, pet=self.pet)
        app.play_account_music = lambda load, song_id: App.play_account_music(app, load, song_id)
        result = App.chat_tool(app, 'play_liked_music', {'song_id': 20})
        self.assertTrue(result['ok'], result)
        music.play_collection.assert_called_once_with([track])
