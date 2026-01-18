import asyncio

from bleak import BleakClient

from netlea_protocol import (
    RGBWF_CHANNEL_ORDER,
    WRITE_UUID_FF02,
    build_main_control_packet,
    encode_payload,
    pwm_hex_from_channels,
)

DEVICE = "50:78:7D:B7:46:62"  # your N7 MAC
WRITE_UUID = WRITE_UUID_FF02
USE_ENCODING = False  # FFFA/FF02 typically uses raw framed payloads

DEV_TYPE = 0x01
MODEL_ID = 0x00
NUMBER = 0x0001
RESTORE_MINUTES = 0

async def main():
    # Try “white-ish”: W high, others low
    pwm_hex = pwm_hex_from_channels(
        RGBWF_CHANNEL_ORDER,
        {"R": 0, "G": 0, "B": 0, "W": 200, "F": 0},
    )
    plain = build_main_control_packet(
        dev_type=DEV_TYPE,
        pwm_hex=pwm_hex,
        model_id=MODEL_ID,
        number=NUMBER,
        restore_minutes=RESTORE_MINUTES,
        onoff=1,
        forever=1,
    )

    async with BleakClient(DEVICE) as client:
        payload = encode_payload(plain) if USE_ENCODING else plain
        await client.write_gatt_char(WRITE_UUID, payload, response=False)
        print("Wrote:", payload.hex().upper())

asyncio.run(main())
