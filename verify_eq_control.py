"""Verify one reversible EQ change and restore the observed original value."""

import asyncio
from datetime import datetime
import json

from probe_session import LOCAL_TIME, ROOT, Session


async def main():
    stamp = datetime.now(LOCAL_TIME).strftime('%Y%m%d-%H%M%S')
    session = Session(ROOT / f'eq-check-{stamp}.jsonl')
    session.label = 'eq_round_trip'
    original = None
    attempted_change = False
    restore_file = ROOT / 'eq-restore-state.json'
    try:
        await session.connect()
        await session.initialize(subscribe=True)
        original = await session.query(0x010F)
        if original is None or len(original) != 2 or original[0] != 0:
            raise RuntimeError('Current EQ value could not be read reliably')
        target = 1 if original[1] == 0 else 0
        restore_file.write_text(json.dumps({'original_eq': original[1], 'test_eq': target, 'restored': False}), encoding='utf-8')
        attempted_change = True
        acknowledgement = await session.query(0x0406, bytes([target]))
        observed = await session.query(0x010F)
        if acknowledgement is None or acknowledgement[:1] != b'\x00' or observed != bytes([0, target]):
            raise RuntimeError('EQ write could not be verified by readback')
        session.report('eq_change_verified', original=original[1], test_value=target, observed=observed[1])
    finally:
        try:
            if attempted_change:
                restored = False
                for attempt in range(1, 4):
                    await session.query(0x0406, original[1:2])
                    observed = await session.query(0x010F)
                    if observed == original:
                        restored = True
                        restore_file.write_text(json.dumps({'original_eq': original[1], 'restored': True}), encoding='utf-8')
                        session.report('eq_restored', value=original[1], attempt=attempt)
                        break
                if not restored:
                    raise RuntimeError('Original EQ restoration remains unverified')
        finally:
            await session.close()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except Exception as error:
        print(json.dumps({'kind': 'fatal', 'error_type': type(error).__name__, 'error': str(error)}), flush=True)
        raise SystemExit(1)
