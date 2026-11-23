import unittest

from oppo_protocol import FrameDecoder, build_frame, decode_battery, decode_position, decode_subscription, decode_features, decode_touch_event


class ProtocolTests(unittest.TestCase):
    def test_battery_query_matches_public_capture(self):
        self.assertEqual(build_frame(0x0106, 0xF0), bytes.fromhex('AA0700000601F00000'))

    def test_decoder_handles_chunk_boundaries_and_coalesced_frames(self):
        wire = bytes.fromhex('AA0F00000681F00800000301C602C60346')
        decoder = FrameDecoder()
        self.assertEqual(decoder.feed(wire[:5]), [])
        frames = decoder.feed(wire[5:] + build_frame(0x810F, 2, b'\x00\x01'))
        self.assertEqual([frame['command'] for frame in frames], [0x8106, 0x810F])
        self.assertEqual(frames[0]['payload'], bytes.fromhex('000301C602C60346'))

    def test_long_payload_uses_variable_length_header(self):
        payload = bytes(range(150))
        frame = build_frame(0x8100, 3, payload)
        self.assertEqual(FrameDecoder().feed(frame)[0]['payload'], payload)

    def test_battery_separates_percentage_and_charging(self):
        data = decode_battery(bytes.fromhex('000301C602C60346'))
        self.assertEqual(data['left'], {'percent': 70, 'charging': True})
        self.assertEqual(data['case'], {'percent': 70, 'charging': False})

    def test_position_preserves_raw_flags_with_tentative_interpretation(self):
        data = decode_position(bytes.fromhex('0201070205'))
        self.assertEqual(data['left']['raw'], 7)
        self.assertTrue(data['left']['in_ear'])
        self.assertFalse(data['right']['in_ear'])

    def test_subscription_tracks_each_event_result(self):
        data = decode_subscription(bytes.fromhex('00050100020003000b02f100'))
        self.assertEqual(data['accepted'], [1, 2, 3, 241])
        self.assertEqual(data['event_status']['11'], 2)

    def test_features_preserve_missing_values_as_unreported(self):
        data = decode_features(bytes.fromhex('0003040111011b00'))
        self.assertEqual(data['values'], {'4': 1, '17': 1, '27': 0})
        self.assertNotIn('59', data['values'])

    def test_observed_touch_packets_distinguish_ear_and_click_count(self):
        left = decode_touch_event(bytes.fromhex('f10101010102'))
        right = decode_touch_event(bytes.fromhex('f10201020602'))
        self.assertEqual((left.get('ear'), left.get('gesture')), ('left', 'single_tap'))
        self.assertEqual((right.get('ear'), right.get('gesture')), ('right', 'double_tap'))

    def test_confirmed_swipe_packets_map_to_direction(self):
        up = decode_touch_event(bytes.fromhex('f10201070b02'))
        down = decode_touch_event(bytes.fromhex('f10201080c02'))
        self.assertEqual((up.get('ear'), up.get('gesture')), ('right', 'swipe_up'))
        self.assertEqual((down.get('ear'), down.get('gesture')), ('right', 'swipe_down'))
        self.assertEqual(up.get('interpretation'), 'swipe_direction_confirmed_by_single_variable_trial')

    def test_unknown_touch_code_stays_unclassified(self):
        other = decode_touch_event(bytes.fromhex('f10101040102'))
        self.assertEqual(other.get('gesture'), 'unclassified_4')

    def test_lid_inference_uses_docked_ear_even_when_case_value_is_stale(self):
        data = decode_position(bytes.fromhex('03010702000304'))
        self.assertEqual(data.get('estimated_lid'), 'closed')

    def test_empty_case_lid_is_unknown_while_both_earbuds_are_out(self):
        data = decode_position(bytes.fromhex('03010702070304'))
        self.assertEqual(data.get('estimated_lid'), 'unknown')


if __name__ == '__main__':
    unittest.main()
