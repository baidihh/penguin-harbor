"""Bounded local OPPO telemetry session using Windows RFCOMM."""

import argparse
import os
import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import time
from uuid import UUID

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / 'deps'))
from winrt.windows.devices.bluetooth import BluetoothCacheMode, BluetoothDevice
from winrt.windows.networking.sockets import SocketProtectionLevel, StreamSocket
from winrt.windows.storage.streams import DataReader, DataWriter, InputStreamOptions

from oppo_protocol import FrameDecoder, build_frame, decode_battery, decode_position, decode_features, decode_subscription, decode_touch_event

SERVICE_UUID = UUID('0000079a-d102-11e1-9b23-00025b00a5a5')
TARGET_ADDRESS = int(os.environ.get('OPPO_BLUETOOTH_ADDRESS', '0').replace(':', '').replace('-', ''), 16)
LOCAL_TIME = timezone(timedelta(hours=8))


class Session:
    def __init__(self, log_path):
        self.log = log_path.open('a', encoding='utf-8')
        self.label = 'initial_unclassified'
        self.sequence = 0
        self.pending = {}
        self.decoder = FrameDecoder()
        self.socket = StreamSocket()
        self.reader = None
        self.writer = None
        self.device = None
        self.service = None
        self.read_task = None
        self.running = True
        self.received_frames = 0
        self.timeouts = 0
        self.last_stdout_position = None

    def report(self, kind, **fields):
        record = {'time': datetime.now(LOCAL_TIME).isoformat(timespec='milliseconds'), 'label': self.label, 'kind': kind, **fields}
        line = json.dumps(record, ensure_ascii=False)
        self.log.write(line + '\n')
        self.log.flush()
        if kind == 'tx' and fields.get('command') == '0x0109':
            return
        if kind == 'rx' and fields.get('command') == '0x8109':
            position = fields.get('payload_hex')
            if position == self.last_stdout_position:
                return
            self.last_stdout_position = position
        print(line, flush=True)

    async def connect(self):
        self.device = await asyncio.wait_for(BluetoothDevice.from_bluetooth_address_async(TARGET_ADDRESS), 15)
        if self.device is None:
            raise RuntimeError('Windows returned no Bluetooth device handle')
        self.report('device', name=self.device.name, connection_status=self.device.connection_status.name)
        services = await asyncio.wait_for(self.device.get_rfcomm_services_async(BluetoothCacheMode.UNCACHED), 15)
        if services.error.value != 0:
            raise RuntimeError(f'SDP failed: {services.error.name}')
        self.service = next((service for service in services.services if service.service_id.uuid == SERVICE_UUID), None)
        if self.service is None:
            raise RuntimeError('OPPO control service absent from current SDP result')
        await asyncio.wait_for(self.socket.connect_async(self.service.connection_host_name, self.service.connection_service_name, SocketProtectionLevel.BLUETOOTH_ENCRYPTION_ALLOW_NULL_AUTHENTICATION), 15)
        self.reader = DataReader(self.socket.input_stream)
        self.reader.input_stream_options = InputStreamOptions.PARTIAL
        self.writer = DataWriter(self.socket.output_stream)
        self.read_task = asyncio.create_task(self.receive())
        self.report('control_connected', service=str(SERVICE_UUID))

    async def receive(self):
        try:
            while self.running:
                count = await self.reader.load_async(4096)
                if count == 0:
                    self.report('remote_closed')
                    self.running = False
                    break
                data = bytearray(count)
                self.reader.read_bytes(data)
                for frame in self.decoder.feed(data):
                    self.received_frames += 1
                    command = frame['command']
                    payload = frame['payload']
                    record = {'command': f'0x{command:04X}' if command is not None else None, 'payload_hex': payload.hex(' '), 'frame_hex': frame['raw'].hex(' ')}
                    if command == 0x8106:
                        record['decoded'] = decode_battery(payload)
                    elif command == 0x8109:
                        record['decoded'] = decode_position(payload[1:]) if payload[:1] == b'\x00' else {'status': payload[0] if payload else None}
                    elif command in (0x8200, 0x8203):
                        record['decoded'] = {'status': payload[0], 'event_ids': list(payload[2:])} if len(payload) >= 2 else {}
                    elif command == 0x8100 and payload[:1] == b'\x00':
                        record['decoded'] = {'capability_bits': [index for index in range(len(payload[1:]) * 8) if payload[1 + index // 8] & (1 << (index % 8))]}
                    elif command == 0x0204 and payload:
                        record['event_id'] = payload[0]
                        if payload[0] == 2:
                            record['decoded'] = decode_position(payload[1:])
                        elif payload[0] == 1:
                            record['decoded'] = decode_battery(b'\x00' + payload[1:])
                        elif payload[0] == 6:
                            record = {'command': f'0x{command:04X}', 'event_id': 6, 'payload_length': len(payload), 'omitted': 'paired_device_list'}
                        elif payload[0] == 0xF1:
                            record['decoded'] = decode_touch_event(payload)
                    elif command == 0x810D:
                        record['decoded'] = decode_features(payload)
                    elif command == 0x8205:
                        record['decoded'] = decode_subscription(payload)
                    self.report('rx', sequence=frame.get('sequence'), **record)
                    key = (command, frame.get('sequence'))
                    if command == 0x8203:
                        key = (0x8200, frame.get('sequence'))
                    future = self.pending.get(key)
                    if future is not None and not future.done():
                        future.set_result(payload)
        except asyncio.CancelledError:
            pass
        except Exception as error:
            self.report('read_error', error_type=type(error).__name__, error=str(error))
            self.running = False

    async def query(self, command, payload=b'', timeout=3):
        self.sequence = (self.sequence + 1) % 256
        sequence = self.sequence
        key = (command | 0x8000, sequence)
        future = asyncio.get_running_loop().create_future()
        self.pending[key] = future
        wire = build_frame(command, sequence, payload)
        self.report('tx', command=f'0x{command:04X}', sequence=sequence, payload_hex=payload.hex(' '), frame_hex=wire.hex(' '))
        try:
            async with asyncio.timeout(timeout):
                self.writer.write_bytes(wire)
                await self.writer.store_async()
                return await future
        except asyncio.TimeoutError:
            self.timeouts += 1
            self.report('query_timeout', command=f'0x{command:04X}')
            return None
        finally:
            self.pending.pop(key, None)

    async def initialize(self, subscribe):
        await self.query(0x0100)
        await self.query(0x0103)
        await self.query(0x0105)
        await self.query(0x0106)
        await self.query(0x0109)
        capabilities = await self.query(0x0200)
        await self.query(0x010D, bytes([5, 4, 17, 27, 58, 59]))
        await self.query(0x010D, bytes([1, 59]))
        await self.query(0x012A)
        await self.query(0x011C)
        await self.query(0x010F)
        if subscribe and capabilities and capabilities[0] == 0:
            events = [event for event in capabilities[2:] if event in (1, 2, 3, 11, 0xF1)]
            if events:
                response = await self.query(0x0205, bytes([len(events), *events]))
                self.report('subscription_result', decoded=decode_subscription(response or b''))
        self.report('baseline_ready')

    async def monitor(self, duration, poll_interval):
        deadline = time.monotonic() + duration
        next_poll = time.monotonic()
        control_path = ROOT / 'control.json'
        last_control = None
        while self.running and time.monotonic() < deadline:
            if control_path.exists():
                try:
                    control = control_path.read_text(encoding='utf-8-sig')
                    if control != last_control:
                        request = json.loads(control)
                        last_control = control
                        self.label = request.get('label', self.label)
                        self.report('action_marker', action=request.get('action', 'mark'))
                        if request.get('action') == 'stop':
                            break
                        if request.get('action') == 'queries':
                            allowed_queries = {0x0106, 0x0109, 0x010C, 0x010D, 0x010F, 0x011C, 0x012A, 0x0202}
                            for query in request.get('queries', []):
                                command = int(query['command'], 0)
                                if command not in allowed_queries:
                                    self.report('query_rejected', command=query['command'])
                                    continue
                                await self.query(command, bytes.fromhex(query.get('payload', '')))
                except (OSError, ValueError) as error:
                    self.report('control_file_error', error=str(error))
            if time.monotonic() >= next_poll:
                await self.query(0x0109)
                next_poll = time.monotonic() + poll_interval
            await asyncio.sleep(0.25)

    async def close(self):
        self.running = False
        if self.read_task:
            self.read_task.cancel()
            await asyncio.gather(self.read_task, return_exceptions=True)
        for resource in (self.writer, self.reader, self.socket, self.service, self.device):
            if resource is not None:
                try:
                    resource.close()
                except Exception:
                    pass
        self.report('session_closed', received_frames=self.received_frames, query_timeouts=self.timeouts)
        self.log.close()


async def main(args):
    stamp = datetime.now(LOCAL_TIME).strftime('%Y%m%d-%H%M%S')
    session = Session(ROOT / f'session-{stamp}.jsonl')
    try:
        await session.connect()
        await session.initialize(args.subscribe)
        await session.monitor(args.duration, args.poll_interval)
    finally:
        await session.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--duration', type=int, default=180)
    parser.add_argument('--poll-interval', type=float, default=2)
    parser.add_argument('--subscribe', action='store_true')
    options = parser.parse_args()
    try:
        asyncio.run(main(options))
    except Exception as error:
        print(json.dumps({'kind': 'fatal', 'error_type': type(error).__name__, 'error': str(error)}), flush=True)
        raise SystemExit(1)
