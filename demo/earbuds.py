"""Connect the existing OPPO read-only telemetry session to the companion."""

import asyncio
from pathlib import Path
import threading


POSITION_INTERVAL = 0.5
QUERY_TIMEOUT = 1.2
MAX_MISSED_POSITIONS = 3
RECONNECT_INTERVAL = 2
WAKE_RECONNECT_INTERVAL = 0.75


class EarbudBridge:
    def __init__(self, pet, runtime):
        self.pet = pet
        self.runtime = Path(runtime)
        self.stop = threading.Event()
        self.retry = threading.Event()

    def start(self):
        threading.Thread(target=self.run, name='oppo-events', daemon=True).start()

    def run(self):
        try:
            from probe_session import Session
        except ImportError as error:
            self.pet.device_status('unavailable', '蓝牙依赖暂不可用，仍可用页面中的体验动作。' + str(error))
            return

        pet = self.pet

        class LiveSession(Session):
            position_reports = 0

            def report(self, kind, **fields):
                if kind == 'control_connected':
                    pet.device_status('syncing', '正在读取左右耳和盒盖状态…')
                elif kind in ('remote_closed', 'read_error'):
                    pet.device_status('disconnected', '耳机已断开，开机后可重新连接。')
                elif kind == 'rx':
                    decoded = fields.get('decoded', {})
                    command = fields.get('command')
                    if command == '0x8109' or (command == '0x0204' and fields.get('event_id') == 2):
                        if any(ear in decoded for ear in ('left', 'right')):
                            self.position_reports += 1
                            pet.earbud_position(decoded)
                    elif command == '0x8106' or (command == '0x0204' and fields.get('event_id') == 1):
                        pet.earbud_battery(decoded)
                    elif command == '0x0204' and fields.get('event_id') == 0xF1:
                        super().report(kind, **fields)
                        pet.earbud_touch(decoded)

        async def session_once():
            session = LiveSession(self.runtime / 'earbuds.jsonl')
            try:
                self.pet.device_status('connecting', '正在查找耳机控制通道…')
                await session.connect()
                await session.query(0x0100, timeout=QUERY_TIMEOUT)
                await session.query(0x0109, timeout=QUERY_TIMEOUT)
                await session.query(0x0205, bytes([3, 1, 2, 0xF1]), timeout=QUERY_TIMEOUT)
                await session.query(0x0106, timeout=QUERY_TIMEOUT)
                last_report = session.position_reports
                missed_positions = 0
                while session.running and not self.stop.is_set():
                    await session.query(0x0109, timeout=QUERY_TIMEOUT)
                    if session.position_reports == last_report:
                        missed_positions += 1
                        if missed_positions >= MAX_MISSED_POSITIONS:
                            self.pet.device_status('disconnected', '耳机位置暂时没有更新，正在重连。')
                            break
                    else:
                        missed_positions = 0
                        last_report = session.position_reports
                    await asyncio.sleep(POSITION_INTERVAL)
            finally:
                await session.close()
            return session.position_reports > 0

        while not self.stop.is_set():
            self.retry.clear()
            was_online = False
            try:
                was_online = asyncio.run(session_once())
            except Exception as error:
                self.pet.device_status('disconnected', '暂未连上耳机，可重新连接。' + type(error).__name__)
            if not self.stop.is_set():
                self.retry.wait(WAKE_RECONNECT_INTERVAL if was_online else RECONNECT_INTERVAL)
