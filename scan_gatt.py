"""Read-only advertisement and GATT discovery for the paired OPPO earbuds."""

import os
import asyncio
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent / "deps"))
from bleak import BleakClient, BleakScanner


KNOWN_ADDRESSES = {os.environ.get('OPPO_BLUETOOTH_ADDRESS', '').upper().replace('-', ':')}


def report(stage, **fields):
    print(json.dumps({"stage": stage, **fields}, ensure_ascii=False), flush=True)


async def main(duration):
    observations = await BleakScanner.discover(timeout=duration, return_adv=True)
    model_identifiers = (bytes.fromhex('10c406'), bytes.fromhex('06c410'))
    model_candidates = []
    for device, advertisement in observations.values():
        if any(identifier in data for data in advertisement.manufacturer_data.values() for identifier in model_identifiers):
            model_candidates.append((device, advertisement))
    matches = [
        (device, advertisement)
        for device, advertisement in observations.values()
        if device.address.upper() in KNOWN_ADDRESSES
        or advertisement.local_name == "OPPO Enco Air5 Pro"
    ]
    report("scan", duration_seconds=duration, observed_count=len(observations), matched_count=len(matches), model_candidate_count=len(model_candidates))
    for index, (device, advertisement) in enumerate(model_candidates, start=1):
        report('model_candidate', candidate=index, name=advertisement.local_name, known_address=device.address.upper() in KNOWN_ADDRESSES, service_uuids=advertisement.service_uuids, manufacturer_ids=list(advertisement.manufacturer_data))
    for index, (device, advertisement) in enumerate(matches, start=1):
        target = f"oppo-{index}"
        report(
            "advertisement",
            target=target,
            name=advertisement.local_name,
            known_address=device.address.upper() in KNOWN_ADDRESSES,
            rssi=advertisement.rssi,
            service_uuids=advertisement.service_uuids,
            manufacturer_ids=list(advertisement.manufacturer_data),
        )
        try:
            async with BleakClient(device, timeout=12) as client:
                services = list(client.services)
                report("gatt", target=target, connected=client.is_connected, service_count=len(services))
                for service in services:
                    report(
                        "service",
                        target=target,
                        uuid=service.uuid,
                        description=service.description,
                        characteristics=[
                            {"uuid": characteristic.uuid, "description": characteristic.description, "properties": characteristic.properties}
                            for characteristic in service.characteristics
                        ],
                    )
        except Exception as error:
            report("gatt_error", target=target, error_type=type(error).__name__, error=str(error))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--duration', type=int, default=12)
    args = parser.parse_args()
    try:
        asyncio.run(main(args.duration))
    except Exception as error:
        report("probe_error", error_type=type(error).__name__, error=str(error))
        raise SystemExit(1)
