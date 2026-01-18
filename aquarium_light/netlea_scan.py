import asyncio
from bleak import BleakClient

DEVICE = "50:78:7D:B7:46:62"  # replace with your device address (MAC on Linux/Win; UUID on macOS)
#DEVICE = "0x1A0111162B2C830150787DB74662"  # replace with your device address (MAC on Linux/Win; UUID on macOS)

NOTIFY_UUIDS = [
    "0000ff01-0000-1000-8000-00805f9b34fb",
    "0000fffd-0000-1000-8000-00805f9b34fb",
]
WRITE_UUIDS = [
    "0000ff02-0000-1000-8000-00805f9b34fb",  # preferred
    "0000fffc-0000-1000-8000-00805f9b34fb",  # fallback
    "0000ff01-0000-1000-8000-00805f9b34fb",  # some firmwares allow writing here too
    "0000fffd-0000-1000-8000-00805f9b34fb",
]

SEED = b"Leds&Fun"

def calc_check_byte(payload: bytes) -> int:
    s = 0
    for i, b in enumerate(payload):
        s += b ^ SEED[i % len(SEED)]
    return s & 0xFF

def encode_payload(plaintext: bytes) -> bytes:
    chk = calc_check_byte(plaintext)
    return bytes([chk]) + bytes([b ^ chk for b in plaintext])

def decode_payload(encoded: bytes) -> bytes:
    if not encoded:
        return b""
    chk = encoded[0]
    plain = bytes([b ^ chk for b in encoded[1:]])
    if calc_check_byte(plain) != chk:
        # not necessarily fatal: some notify frames might be unwrapped / different
        raise ValueError("check-byte mismatch")
    return plain

def hexdump(b: bytes) -> str:
    return b.hex().upper()

async def main():
    async with BleakClient(DEVICE) as client:

        def mk_cb(label):
            def on_notify(sender, data: bytearray):
                raw = bytes(data)
                print(f"[{label}] raw    {hexdump(raw)}")
                try:
                    plain = decode_payload(raw)
                    print(f"[{label}] decoded {hexdump(plain)}")
                except Exception as e:
                    print(f"[{label}] (decode failed: {e})")
            return on_notify

        # Subscribe to both notifies (whichever actually emits, you'll see it)
        for nu in NOTIFY_UUIDS:
            try:
                await client.start_notify(nu, mk_cb(nu[-4:]))
                print("notify enabled:", nu)
            except Exception as e:
                print("notify failed:", nu, e)

        # --- send a tiny “probe” write ---
        # NOTE: This is just a placeholder. Use your real frame builder here.
        # If the device requires the app’s exact frame format, an arbitrary payload won’t do anything.
        probe_plain = bytes.fromhex("00")  # harmless placeholder
        probe_enc = encode_payload(probe_plain)

        sent = False
        for wu in WRITE_UUIDS:
            try:
                await client.write_gatt_char(wu, probe_enc, response=False)
                print("wrote probe to:", wu)
                sent = True
                break
            except Exception as e:
                print("write failed:", wu, e)

        if not sent:
            print("Could not write to any candidate characteristic.")

        await asyncio.sleep(8)

        # Clean up
        for nu in NOTIFY_UUIDS:
            try:
                await client.stop_notify(nu)
            except:
                pass

asyncio.run(main())
