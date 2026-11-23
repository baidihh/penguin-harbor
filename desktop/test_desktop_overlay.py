import unittest
from desktop.desktop_overlay import CaptionState, caption_origin

class CaptionTests(unittest.TestCase):
    def test_reply_yields_to_music_after_seven_seconds(self):
        caption = CaptionState()
        state = {'conversation': [{'user': 'hello', 'reply': '你好呀', 'reply_state': 'done'}]}
        music = {'playing': True, 'paused': False, 'lyric': '海风吹过', 'current': {'name': '海边'}}
        self.assertEqual(caption.text(state, music, True, False, now=10), '你好呀')
        self.assertEqual(caption.text(state, music, True, False, now=18), '海风吹过')
        self.assertEqual(caption.text(state, music, True, True, now=19), '你好呀')

    def test_pending_reply_and_paused_track_remain_visible(self):
        caption = CaptionState()
        state = {'conversation': [{'reply': '想一想…', 'reply_state': 'pending'}]}
        music = {'playing': False, 'paused': True, 'lyric': '', 'current': {'name': '海边'}}
        self.assertEqual(caption.text(state, music, True, False, now=1), '想一想…')
        self.assertEqual(caption.text(state, music, True, False, now=99), '想一想…')
        self.assertEqual(caption.text(state, music, False, False, now=100), '海边')

    def test_caption_stays_inside_screen_when_dragged_or_anchored(self):
        self.assertEqual(caption_origin((900, 20, 160, 180), (1024, 768), (400, 160)), (616, 208))
        self.assertEqual(caption_origin((0, 0, 160, 180), (1024, 768), (400, 160), (-50, 900)), (8, 568))
