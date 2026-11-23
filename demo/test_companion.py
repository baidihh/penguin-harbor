"""Behavior tests for saved memories, independent life, and earbud input."""

import json
from pathlib import Path
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor

try:
    from demo.companion import Companion
except ModuleNotFoundError:
    Companion = None


class CompanionTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(Companion, 'The companion life and memory engine is missing')
        test_root = Path(__file__).parent / '.runtime' / 'tests'
        test_root.mkdir(parents=True, exist_ok=True)
        self.folder = tempfile.TemporaryDirectory(dir=test_root)
        self.assertTrue(Path(self.folder.name).resolve().is_relative_to(test_root.resolve()))
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / 'state.json'
        self.clock = 1000.0
        self.pet = Companion(self.path, clock=lambda: self.clock)

    def test_room_choice_survives_restart(self):
        self.pet.act('choose', value='pier')
        loaded = Companion(self.path, clock=lambda: self.clock)
        self.assertEqual(loaded.snapshot()['room_choice'], 'pier')
        self.assertTrue(any('码头' in item['text'] for item in loaded.snapshot()['journal']))

    def test_life_advances_while_application_is_closed(self):
        self.clock += 310
        loaded = Companion(self.path, clock=lambda: self.clock)
        self.assertGreater(loaded.snapshot()['project_stage'], 0)
        self.assertTrue(any(item['kind'] == 'project' for item in loaded.snapshot()['journal']))

    def test_story_fast_forward_is_saved(self):
        self.pet.act('advance')
        self.assertGreater(self.pet.snapshot()['project_stage'], 0)
        loaded = Companion(self.path, clock=lambda: self.clock)
        self.assertEqual(loaded.snapshot()['project_stage'], self.pet.snapshot()['project_stage'])

    def test_chat_remembers_user_words(self):
        self.pet.act('chat', text='今天终于把那个产品做出来了')
        loaded = Companion(self.path, clock=lambda: self.clock)
        self.assertEqual(loaded.snapshot()['memories'][-1]['text'], '今天终于把那个产品做出来了')
        result = loaded.act('recall')
        self.assertIn('今天终于把那个产品做出来了', result['text'])

    def test_one_timeline_interleaves_a_thought_with_a_delayed_reply_after_restart(self):
        draft = self.pet.act('chat', text='在想什么？', await_reply=True)
        self.clock += 1
        self.pet.say_from_mind('潮水快涨了。')
        self.clock += 1
        self.pet.finish_chat(draft, '想去看海。')
        loaded = Companion(self.path, clock=lambda: self.clock)
        rows = loaded.snapshot().get('timeline', [])
        self.assertEqual([(r['label'], r['text']) for r in rows], [
            ('你', '在想什么？'), ('企鹅心声', '潮水快涨了。'), ('企鹅', '想去看海。')])

    def test_blank_chat_is_rejected_without_a_memory(self):
        with self.assertRaises(ValueError):
            self.pet.act('chat', text='   ')
        self.assertEqual(self.pet.snapshot()['memories'], [])

    def test_invalid_choice_does_not_change_room(self):
        with self.assertRaises(ValueError):
            self.pet.act('choose', value='unknown')
        self.assertIsNone(self.pet.snapshot()['room_choice'])

    def test_repeated_wearing_reports_do_not_repeat_greeting(self):
        position = {'left': {'in_ear': True, 'out_of_case': True}, 'estimated_lid': 'unknown'}
        self.pet.earbud_position(position)
        first = self.pet.snapshot()['message']['id']
        self.pet.earbud_position(position)
        self.assertEqual(self.pet.snapshot()['message']['id'], first)
        self.assertTrue(self.pet.snapshot()['device']['worn'])

    def test_left_and_right_touches_trigger_different_interactions(self):
        self.pet.earbud_touch({'ear': 'left', 'gesture': 'double_tap'})
        left = self.pet.snapshot()['message']['text']
        self.pet.earbud_touch({'ear': 'right', 'gesture': 'double_tap'})
        self.assertNotEqual(self.pet.snapshot()['message']['text'], left)
        self.assertEqual(self.pet.snapshot()['device']['last_touch'], '右耳双击')

    def test_disconnect_clears_live_wearing_status(self):
        self.pet.earbud_position({'left': {'in_ear': True, 'out_of_case': True}})
        self.pet.device_status('disconnected', '耳机已断开')
        device = self.pet.snapshot()['device']
        self.assertIsNone(device['worn'])
        self.assertEqual(device['status'], 'disconnected')

    def test_concurrent_notes_are_preserved_in_valid_json(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda number: self.pet.act('chat', text=f'想法 {number}'), range(12)))
        saved = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(len(saved['memories']), 12)
        self.assertEqual(len({item['text'] for item in saved['memories']}), 12)

    def test_routine_does_not_repeat_identical_journal_notes(self):
        for _ in range(12):
            self.clock += 90
            self.pet.snapshot()
        notes = [item['text'] for item in self.pet.snapshot()['journal'] if item['kind'] == 'life']
        self.assertEqual(len(notes), len(set(notes)))

    def test_pet_size_is_saved_and_restored(self):
        self.pet.act('scale', value=1.5)
        loaded = Companion(self.path, clock=lambda: self.clock)
        self.assertEqual(loaded.snapshot()['pet_scale'], 1.5)

    def test_pet_size_accepts_both_limits(self):
        self.pet.act('scale', value=0.5)
        self.assertEqual(self.pet.snapshot()['pet_scale'], 0.5)
        self.pet.act('scale', value=2)
        self.assertEqual(self.pet.snapshot()['pet_scale'], 2)

    def test_invalid_pet_size_keeps_the_saved_size(self):
        for value in (0, 2.1, -1, None, 'large', True, float('nan'), float('inf')):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.pet.act('scale', value=value)
        self.assertEqual(self.pet.snapshot()['pet_scale'], 1)

    def test_lighthouse_ending_reflects_the_chosen_signal_site(self):
        self.pet.act('choose', value='cliff')
        for _ in range(4):
            result = self.pet.act('advance')
        self.assertIn('风崖', result['text'])
        self.assertEqual(self.pet.snapshot()['project_progress'], 100)

    def test_fishing_records_a_catch_and_respects_the_tide(self):
        self.pet.act('fish')
        self.pet.act('fish')
        self.assertEqual(self.pet.snapshot()['catches'], 1)
        self.clock += 90
        self.pet.act('fish')
        self.assertEqual(self.pet.snapshot()['catches'], 2)

    def test_voice_selection_sticks_and_recall_uses_a_fixed_spoken_line(self):
        self.assertEqual(self.pet.snapshot()['voice_id'], 'hsiaochen')
        with self.assertRaises(ValueError):
            self.pet.act('voice_select', value='unknown')
        self.pet.act('voice_select', value='hsiaoyu')
        self.pet.act('chat', text='私人的留言')
        restored = Companion(self.path, clock=lambda: self.clock)
        self.assertEqual(restored.snapshot()['voice_id'], 'hsiaoyu')
        recall = restored.act('recall')
        self.assertIn('私人的留言', recall['text'])
        self.assertNotIn('私人的留言', recall['speech'])

    def test_old_save_file_gets_a_default_pet_size(self):
        original = json.loads(self.path.read_text(encoding='utf-8'))
        original.pop('pet_scale', None)
        self.path.write_text(json.dumps(original), encoding='utf-8')
        loaded = Companion(self.path, clock=lambda: self.clock)
        self.assertEqual(loaded.snapshot()['pet_scale'], 1)


if __name__ == '__main__':
    unittest.main()
