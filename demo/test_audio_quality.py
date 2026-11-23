"""Music quality and microphone routing regressions."""

import unittest
import json
from types import SimpleNamespace
from unittest.mock import patch

from demo.desktop_voice import DesktopVoice
from demo.netease import NetEase
from demo.netease_account import NetEaseAccount


class AudioQualityTests(unittest.TestCase):
    def test_account_reads_liked_track_names_and_total_without_exposing_account_details(self):
        responses = [
            {'code': 200, 'data': {'id': 'encrypted-playlist', 'trackCount': 151}},
            {'code': 200, 'data': [{'originalId': 12, 'name': '下一个天亮',
                                   'artists': [{'name': '郭静'}], 'privateField': 'omit'}]},
        ]
        replies = [SimpleNamespace(returncode=0, stdout=json.dumps(data), stderr='') for data in responses]
        account = NetEaseAccount()
        account.command = ('ncm-cli.cmd',)
        with patch('demo.netease_account.subprocess.run', side_effect=replies):
            result = account.liked()
        self.assertEqual(result['total'], 151)
        self.assertEqual(result['songs'], [{'id': 12, 'name': '下一个天亮', 'artist': '郭静'}])
        self.assertNotIn('encrypted-playlist', json.dumps(result))

    def test_account_rejects_expired_login_instead_of_inventing_recommendations(self):
        reply = SimpleNamespace(returncode=0, stdout='{"code":301,"message":"login required"}', stderr='')
        account = NetEaseAccount()
        account.command = ('ncm-cli.cmd',)
        with patch('demo.netease_account.subprocess.run', return_value=reply):
            with self.assertRaisesRegex(RuntimeError, '登录'):
                account.daily()

    def test_account_track_keeps_official_playback_identity_and_permission(self):
        tracks = NetEaseAccount._songs([{'originalId': '12', 'id': 'ab' * 16,
            'name': '歌', 'artists': [], 'duration': 180000, 'playFlag': True}])
        self.assertEqual(tracks[0].get('account_id'), 'ab' * 16)
        self.assertIs(tracks[0].get('account_playable'), True)
        self.assertEqual(tracks[0].get('duration'), 180)

    def test_official_pause_keeps_the_track_when_cli_reports_stopped_with_position(self):
        music = NetEase()
        music.current = {'id': 12, 'name': '雪'}
        import threading
        music._official_stop = threading.Event()
        music._official_state = {'status': 'playing', 'title': '雪 - 甲', 'position': 3}
        from unittest.mock import Mock
        music.account = Mock()
        music.account.playback.side_effect = [None, {'status': 'stopped', 'title': '雪 - 甲', 'position': 4}]
        music.pause()
        self.assertEqual(music.state()['status'], 'paused')
        self.assertEqual(music.position(), 4)

    def test_music_requests_high_bitrate(self):
        with patch.object(NetEase, '_get', return_value={'data': [{'url': 'https://audio.example/song'}]}) as get:
            self.assertEqual(NetEase().stream_url(123), 'https://audio.example/song')
        self.assertIn('br=320000', get.call_args.args[0])

    def test_search_preserves_provider_relevance_including_original_artist(self):
        data = {'result': {'songs': [
            {'id': 1, 'name': '我记得', 'artists': [{'name': '赵雷'}], 'fee': 1},
            {'id': 2, 'name': '我记得', 'artists': [{'name': '其他歌手'}], 'fee': 0},
        ]}}
        with patch.object(NetEase, '_get', return_value=data):
            self.assertEqual(NetEase().search('我记得 赵雷')[0]['artist'], '赵雷')

    def test_voice_uses_computer_microphone_when_default_is_bluetooth(self):
        devices = [
            {'name': '耳机 (OPPO Enco Air5 Pro)', 'hostapi': 0, 'max_input_channels': 1},
            {'name': '麦克风 (Realtek(R) Audio)', 'hostapi': 0, 'max_input_channels': 2},
            {'name': '耳机 (OPPO Enco Air5 Pro)', 'hostapi': 1, 'max_input_channels': 2},
        ]
        self.assertEqual(DesktopVoice.input_device(devices, 0), 1)
        self.assertEqual(DesktopVoice.input_device(devices, 1), 1)
        self.assertEqual(DesktopVoice.input_device(devices[:1], 0), 0)
