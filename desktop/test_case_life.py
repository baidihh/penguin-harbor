"""Earbud actions must drive the companion's home and shared rituals."""

import json
from io import BytesIO
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from desktop.companion import Companion
from desktop.main import Handler
from oppo_protocol import decode_position, decode_touch_event


def position(left, right, lid='open'):
    return {
        'left': {'out_of_case': left != 'docked', 'in_ear': left == 'worn'},
        'right': {'out_of_case': right != 'docked', 'in_ear': right == 'worn'},
        'estimated_lid': lid,
    }


class CaseCompanionTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).parent / '.runtime' / 'tests'
        root.mkdir(parents=True, exist_ok=True)
        self.folder = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / 'state.json'
        self.now = 1000.0
        self.pet = Companion(self.path, clock=lambda: self.now)

    def life(self):
        return self.pet.snapshot().get('case_companion', {})

    def test_open_take_wear_remove_return_close_form_a_homecoming(self):
        steps = [
            ('docked', 'docked', 'closed', 'sleeping'),
            ('docked', 'docked', 'open', 'peeking'),
            ('out', 'docked', 'open', 'ready'),
            ('worn', 'docked', 'open', 'together'),
            ('out', 'docked', 'open', 'waiting'),
            ('docked', 'docked', 'open', 'home'),
            ('docked', 'docked', 'closed', 'sleeping'),
        ]
        for left, right, lid, phase in steps:
            with self.subTest(phase=phase):
                self.pet.earbud_position(position(left, right, lid))
                self.assertEqual(self.life().get('phase'), phase)
        self.assertFalse(self.pet.snapshot()['message']['audible'])

    def test_repeated_position_packets_do_not_repeat_rituals(self):
        packet = position('docked', 'docked')
        self.pet.earbud_position(packet)
        message_id = self.pet.snapshot()['message']['id']
        event_id = self.life().get('event_id')
        self.assertIsNotNone(event_id)
        for _ in range(4):
            self.pet.earbud_position(packet)
        self.assertEqual(self.life()['event_id'], event_id)
        self.assertEqual(self.pet.snapshot()['message']['id'], message_id)

    def test_returning_from_rest_brings_a_persisted_collectible(self):
        self.pet.earbud_position(position('docked', 'docked', 'closed'))
        self.now += 180
        restarted = Companion(self.path, clock=lambda: self.now)
        restarted.earbud_position(position('docked', 'docked', 'open'))
        life = restarted.snapshot().get('case_companion', {})
        self.assertEqual(len(life.get('postcards', [])), 1)
        self.assertTrue(life['selected_postcard']['text'])
        restarted.earbud_position(position('docked', 'docked', 'open'))
        self.assertEqual(len(restarted.snapshot()['case_companion']['postcards']), 1)

    def test_partial_position_keeps_the_other_ears_wearing_state(self):
        self.pet.earbud_position(position('worn', 'out', 'unknown'))
        self.pet.earbud_position({'right': {'out_of_case': False, 'in_ear': False}, 'estimated_lid': 'open'})
        self.assertTrue(self.pet.snapshot()['device']['worn'])
        self.assertEqual(self.life().get('phase'), 'together')

    def test_initial_single_ear_snapshot_replaces_simulation_and_uses_docked_ear_for_lid(self):
        self.pet.simulate_case('lid_open')
        self.pet.device_status('syncing', '等待第一帧')
        self.pet.earbud_position(decode_position(bytes.fromhex('03 01 07 02 00 03 04')))
        state = self.pet.snapshot()
        self.assertEqual(state['device']['status'], 'connected')
        self.assertTrue(state['device']['worn'])
        self.assertEqual(state['case_companion']['source'], 'device')
        self.assertEqual(state['case_companion']['ears'], {'left': 'worn', 'right': 'docked'})
        self.assertEqual(state['case_companion']['lid'], 'closed')

    def test_unknown_other_ear_does_not_certify_that_neither_is_worn(self):
        self.pet.device_status('syncing', '等待第一帧')
        self.pet.earbud_position(decode_position(bytes.fromhex('01 02 04')))
        self.assertIsNone(self.pet.snapshot()['device']['worn'])
        self.assertEqual(self.life()['ears'], {'left': 'unknown', 'right': 'docked'})

    def test_live_updates_publish_lid_and_wearing_changes_but_skip_identical_reports(self):
        closed = decode_position(bytes.fromhex('03 01 07 02 00 03 04'))
        opened = decode_position(bytes.fromhex('03 01 07 02 04 03 04'))
        initial = self.pet.snapshot().get('earbud_revision', 0)
        self.pet.earbud_position(closed)
        current = self.pet.snapshot().get('earbud_revision', 0)
        self.assertGreater(current, initial)
        self.pet.earbud_position(closed)
        self.assertIsNone(self.pet.wait_for_earbuds(current, timeout=0))
        self.pet.earbud_position(opened)
        update = self.pet.wait_for_earbuds(current, timeout=0)
        self.assertEqual(update['case_companion']['lid'], 'open')
        self.assertEqual(update['case_companion']['ears'], {'left': 'worn', 'right': 'docked'})

    def test_fast_take_and_wear_delivers_collectible_when_open_lid_packet_was_missed(self):
        self.pet.earbud_position(position('docked', 'docked', 'closed'))
        self.pet.device_status('disconnected', 'case closed')
        self.now += 180
        self.pet.earbud_position(position('worn', 'out', 'unknown'))
        self.assertEqual(self.life().get('phase'), 'together')
        self.assertEqual(len(self.life().get('postcards', [])), 1)

    def test_reconnect_with_both_ears_taken_out_starts_a_visit_before_wearing(self):
        self.pet.earbud_position(position('docked', 'docked', 'closed'))
        self.pet.device_status('disconnected', 'case closed')
        self.pet.earbud_position(position('out', 'out', 'unknown'))
        self.assertEqual(self.life().get('last_event', {}).get('kind'), 'taken_out')
        self.assertEqual(len(self.life().get('postcards', [])), 1)

    def test_partial_closed_report_after_reconnect_does_not_wake_sleeping_companion(self):
        self.pet.earbud_position(position('docked', 'docked', 'closed'))
        self.pet.device_status('disconnected', 'case closed')
        self.pet.earbud_position(decode_position(bytes.fromhex('01 01 00')))
        self.assertEqual(self.life().get('phase'), 'sleeping')
        self.pet.say_from_mind('明天再给小灯换一块玻璃。')
        self.assertFalse(self.pet.snapshot()['message'].get('audible', True))

    def test_empty_case_lid_unknown_does_not_send_the_companion_to_bed(self):
        self.pet.earbud_position(position('out', 'out', 'unknown'))
        self.pet.device_status('connected', 'test')
        self.pet.device_status('disconnected', 'link lost')
        self.assertEqual(self.life().get('lid'), 'unknown')
        self.assertNotEqual(self.life().get('phase'), 'sleeping')
        self.assertEqual(self.life().get('phase'), 'unknown')

    def test_real_single_tap_packets_pet_and_show_a_collectible(self):
        self.pet.earbud_position(position('docked', 'docked', 'closed'))
        self.pet.earbud_position(position('docked', 'docked', 'open'))
        self.pet.earbud_touch(decode_touch_event(bytes.fromhex('f1 01 01 01 01 02')))
        self.assertEqual(self.life().get('last_event', {}).get('kind'), 'pet')
        self.assertGreater(self.life()['affection'], 0)
        left_message = self.pet.snapshot()['message']['id']
        self.pet.earbud_touch(decode_touch_event(bytes.fromhex('f1 02 01 01 01 02')))
        self.assertEqual(self.life()['last_event']['kind'], 'collection')
        self.assertGreater(self.pet.snapshot()['message']['id'], left_message)

    def test_real_position_decoder_reaches_the_home_lifecycle(self):
        self.pet.earbud_position(decode_position(bytes.fromhex('03 01 00 02 00 03 00')))
        self.assertEqual(self.life().get('phase'), 'sleeping')
        self.pet.earbud_position(decode_position(bytes.fromhex('03 01 04 02 04 03 01')))
        self.assertEqual(self.life()['phase'], 'peeking')
        self.pet.earbud_position(decode_position(bytes.fromhex('03 01 01 02 04 03 01')))
        self.assertEqual(self.life()['last_event']['kind'], 'taken_out')

    def test_case_rituals_reply_immediately_without_model_generation(self):
        class UnavailableSpeech:
            def line(self, *args):
                raise AssertionError('A physical gesture must not wait for the model')

        self.pet.speech = UnavailableSpeech()
        self.pet.earbud_position(position('docked', 'docked'))
        self.pet.earbud_touch({'ear': 'left', 'gesture': 'single_tap'})
        self.pet.earbud_touch({'ear': 'right', 'gesture': 'double_tap'})
        self.assertTrue(self.pet.snapshot()['message']['text'])

    def test_simulation_does_not_change_live_hardware_or_persisted_collection(self):
        self.assertTrue(callable(getattr(self.pet, 'simulate_case', None)), 'Full case simulation is missing')
        self.pet.earbud_position(position('worn', 'out', 'unknown'))
        before = json.loads(self.path.read_text(encoding='utf-8'))
        for action in ('lid_close', 'lid_open', 'take_left', 'wear', 'remove', 'return_both', 'lid_close'):
            self.pet.simulate_case(action)
        self.assertEqual(self.life()['source'], 'simulation')
        self.assertEqual(self.life()['phase'], 'sleeping')
        self.assertTrue(self.pet.snapshot()['device']['worn'])
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8')), before)
        self.pet.earbud_position(position('worn', 'out', 'unknown'))
        self.assertEqual(self.life()['phase'], 'sleeping')
        self.pet.simulate_case('live')
        self.assertEqual(self.life()['source'], 'device')
        self.assertEqual(self.life()['phase'], 'together')

    def test_swiping_adjusts_only_the_virtual_homes_light_with_bounds(self):
        self.assertTrue(callable(getattr(self.pet, 'simulate_case', None)), 'Swipe interaction is missing')
        self.pet.simulate_case('lid_open')
        before = self.life()['warmth']
        self.pet.simulate_case('swipe_up')
        self.assertGreater(self.life()['warmth'], before)
        for _ in range(15):
            self.pet.simulate_case('swipe_down')
        self.assertEqual(self.life()['warmth'], 10)

    def test_real_touch_resumes_live_feedback_after_trying_simulation(self):
        self.assertTrue(callable(getattr(self.pet, 'simulate_case', None)))
        self.pet.earbud_position(position('worn', 'out', 'unknown'))
        self.pet.simulate_case('lid_close')
        previous_message = self.pet.snapshot()['message']['id']
        self.pet.earbud_touch({'ear': 'left', 'gesture': 'single_tap'})
        self.assertEqual(self.life()['source'], 'device')
        self.assertEqual(self.life()['affection'], 1)
        self.assertEqual(self.life()['last_event']['kind'], 'pet')
        self.assertGreater(self.pet.snapshot()['message']['id'], previous_message)
        self.assertTrue(self.pet.snapshot()['message']['audible'])

    def test_real_swipe_leaves_simulation_and_changes_the_real_homes_light(self):
        self.pet.earbud_position(position('worn', 'out', 'unknown'))
        self.pet.simulate_case('swipe_down')
        self.assertEqual(self.life()['warmth'], 50)
        self.pet.earbud_touch(decode_touch_event(bytes.fromhex('f1 02 01 07 0b 02')))
        self.assertEqual(self.life()['source'], 'device')
        self.assertEqual(self.life()['warmth'], 70)
        self.assertIn('上滑', self.pet.snapshot()['message']['topic'])

    def test_unclassified_hardware_touch_is_visible_without_guessing_a_swipe(self):
        self.pet.earbud_position(position('worn', 'out', 'unknown'))
        before = self.pet.snapshot()['message']['id']
        touch = decode_touch_event(bytes.fromhex('f1 01 01 04 01 02'))
        self.pet.earbud_touch(touch)
        device = self.pet.snapshot()['device']
        self.assertEqual(device.get('unclassified_touch', {}).get('raw'), 'f1 01 01 04 01 02')
        self.assertEqual(self.pet.snapshot()['message']['id'], before)

    def test_real_swipe_packets_drive_the_homes_light(self):
        """07/08 是单变量真机实验确认的上下滑，要直接作用到小屋灯光。"""
        self.pet.earbud_position(position('worn', 'out', 'open'))
        before = self.life()['warmth']
        self.pet.earbud_touch(decode_touch_event(bytes.fromhex('f1 02 01 07 0b 02')))
        self.assertEqual(self.life()['warmth'], before + 10)
        self.assertEqual(self.life()['last_event'].get('light_percent'), before + 10)
        self.pet.earbud_touch(decode_touch_event(bytes.fromhex('f1 02 01 08 0c 02')))
        self.assertEqual(self.life()['warmth'], before)
        changes = [event['light_percent'] for event in self.life()['events'] if event['kind'] == 'light']
        self.assertEqual(changes, [70, 60])
        device = self.pet.snapshot()['device']
        self.assertEqual(device.get('last_touch'), '右耳下滑')
        self.assertNotIn('unclassified_touch', device)

    def test_sleeping_keeps_autonomous_thoughts_visually_available_and_silent(self):
        self.pet.earbud_position(position('docked', 'docked', 'closed'))
        self.pet.say_from_mind('明天想去捡一枚蓝色贝壳。')
        message = self.pet.snapshot()['message']
        self.assertEqual(message['text'], '明天想去捡一枚蓝色贝壳。')
        self.assertFalse(message.get('audible', True))

    def test_confirmed_sleep_stays_quiet_when_closing_the_case_disconnects_bluetooth(self):
        self.pet.earbud_position(position('docked', 'docked', 'closed'))
        self.pet.device_status('disconnected', 'case closed')
        self.assertEqual(self.life().get('lid'), 'unknown')
        self.assertEqual(self.life().get('phase'), 'sleeping')
        self.pet.say_from_mind('我想在小屋里画一张明信片。')
        self.assertFalse(self.pet.snapshot()['message'].get('audible', True))

    def test_returned_ears_keep_autonomous_thoughts_quiet_before_the_lid_closes(self):
        self.pet.earbud_position(position('worn', 'out', 'unknown'))
        self.pet.earbud_position(position('docked', 'docked', 'open'))
        self.pet.say_from_mind('我先整理一下收藏架。')
        self.assertFalse(self.pet.snapshot()['message'].get('audible', True))

    def test_explicit_voice_preview_still_works_while_the_companion_sleeps(self):
        self.pet.earbud_position(position('docked', 'docked', 'closed'))
        message = self.pet.act('voice_preview')
        self.assertTrue(message.get('audible', False))

    def test_simulation_http_action_uses_the_same_case_lifecycle(self):
        body = json.dumps({'action': 'simulate', 'value': 'lid_open'}).encode('utf-8')
        request = object.__new__(Handler)
        request.app = SimpleNamespace(pet=self.pet, url='http://127.0.0.1:18765')
        request.path = '/api/action'
        request.headers = {'Content-Length': str(len(body))}
        request.rfile = BytesIO(body)
        replies = []
        request.send_json = lambda data, status=200: replies.append((status, data))
        request.do_POST()
        self.assertEqual(replies[0][0], 200)
        self.assertEqual(replies[0][1]['case_companion']['phase'], 'peeking')
        self.assertEqual(replies[0][1]['case_companion']['source'], 'simulation')


if __name__ == '__main__':
    unittest.main()
