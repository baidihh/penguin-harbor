"""OPPO ENCO_LINK framing and explicitly tentative state interpretation."""

import struct


def build_frame(command, sequence, payload=b''):
    body = b'\x00\x00' + struct.pack('<HBH', command, sequence, len(payload)) + payload
    remaining = len(body)
    encoded_length = bytearray()
    while remaining > 127:
        encoded_length.append((remaining & 127) | 128)
        remaining >>= 7
    encoded_length.append(remaining)
    return b'\xAA' + encoded_length + body


class FrameDecoder:
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, data):
        self.buffer.extend(data)
        frames = []
        while self.buffer:
            start = self.buffer.find(b'\xAA')
            if start < 0:
                self.buffer.clear()
                break
            del self.buffer[:start]
            if len(self.buffer) < 2:
                break
            length = self.buffer[1] & 127
            header_size = 2
            if self.buffer[1] & 128:
                if len(self.buffer) < 3:
                    break
                length |= (self.buffer[2] & 127) << 7
                header_size = 3
            if length < 7 or length > 4096:
                del self.buffer[0]
                continue
            total = header_size + length
            if len(self.buffer) < total:
                break
            body = bytes(self.buffer[header_size:total])
            if body[:2] != b'\x00\x00':
                frames.append({'command': None, 'payload': body, 'raw': bytes(self.buffer[:total]), 'unparsed': 'fragmented_or_unknown_control'})
                del self.buffer[:total]
                continue
            command, sequence, payload_length = struct.unpack('<HBH', body[2:7])
            if payload_length != length - 7:
                del self.buffer[0]
                continue
            frames.append({'command': command, 'sequence': sequence, 'payload': body[7:], 'raw': bytes(self.buffer[:total])})
            del self.buffer[:total]
        return frames


def decode_battery(payload):
    if len(payload) < 2:
        return {'raw': payload.hex()}
    names = {1: 'left', 2: 'right', 3: 'case'}
    count = payload[1]
    if len(payload) != 2 + count * 2:
        return {'status': payload[0], 'raw': payload.hex()}
    result = {'status': payload[0]}
    for index in range(count):
        component, value = payload[2 + index * 2:4 + index * 2]
        result[names.get(component, str(component))] = {'percent': value & 127, 'charging': bool(value & 128)}
    return result


def decode_position(payload):
    if not payload or len(payload) != 1 + payload[0] * 2:
        return {'raw': payload.hex()}
    names = {1: 'left', 2: 'right', 3: 'case'}
    result = {'interpretation': 'community_mapping_pending_action_validation'}
    for index in range(payload[0]):
        component, flags = payload[1 + index * 2:3 + index * 2]
        result[names.get(component, str(component))] = {
            'raw': flags,
            'out_of_case': bool(flags & 1),
            'in_ear': bool(flags & 2),
            'bit2_available_or_lid_related': bool(flags & 4),
        }
    docked = [result[name] for name in ('left', 'right') if name in result and not result[name]['out_of_case']]
    result['estimated_lid'] = 'unknown'
    result['lid_inference_basis'] = 'correlation_of_docked_ear_bit2_with_user_case_actions'
    if docked and all(ear['bit2_available_or_lid_related'] for ear in docked):
        result['estimated_lid'] = 'open'
    elif docked and all(not ear['bit2_available_or_lid_related'] for ear in docked):
        result['estimated_lid'] = 'closed'
    return result


def decode_subscription(payload):
    features = decode_features(payload)
    values = features['values']
    return {'status': features.get('status'), 'accepted': [int(event) for event, status in values.items() if status == 0], 'event_status': values}


def decode_features(payload):
    result = {'status': payload[0] if payload else None, 'values': {}}
    if len(payload) < 2 or len(payload) != 2 + payload[1] * 2:
        result['raw'] = payload.hex(' ')
        return result
    result['values'] = {str(payload[index]): payload[index + 1] for index in range(2, len(payload), 2)}
    return result


TOUCH_GESTURES = {1: 'single_tap', 2: 'double_tap', 7: 'swipe_up', 8: 'swipe_down'}


def decode_touch_event(payload):
    result = {'raw': payload.hex(' ')}
    if len(payload) == 6 and payload[0] == 0xF1:
        result.update({
            'ear': {1: 'left', 2: 'right'}.get(payload[1], f'unknown_{payload[1]}'),
            'gesture': TOUCH_GESTURES.get(payload[3], f'unclassified_{payload[3]}'),
            'unclassified_fields': [payload[2], payload[4], payload[5]],
            'interpretation': 'ear_and_single_double_validated_by_user_actions',
        })
        if payload[3] in (7, 8):
            # 07/08 由单变量真机实验确认：只上滑 3 次连续收到 07，只下滑 3 次连续收到 08。
            result['interpretation'] = 'swipe_direction_confirmed_by_single_variable_trial'
    return result
