import asyncio
from bleak import BleakClient

DEVICE = "50:78:7D:B7:46:62" # your N7 MAC
WRITE_UUID = "0000ff02-0000-1000-8000-00805f9b34fb"

def hexb(s: str) -> bytes:
    return bytes.fromhex(s.replace(" ", "").replace("\n", ""))

# Candidate frame A (your earlier “5A 01 10 ...” style; may or may not match your firmware)
def frame_a(B, G, R, W, F):
    return hexb(
        "5A 01 10 00 00 "
        "01 00 00 00 "
        f"{B:02X} {G:02X} {R:02X} {W:02X} {F:02X} "
        "84 03 00 "
        "01 00 "
        "00 00"
    )

async def main():
    # Try “white-ish”: W high, others low
    plain = frame_a(B=0, G=0, R=0, W=200, F=0)

    async with BleakClient(DEVICE) as client:
        await client.write_gatt_char(WRITE_UUID, plain, response=False)
        print("Wrote RAW (no encoding):", plain.hex().upper())

asyncio.run(main())