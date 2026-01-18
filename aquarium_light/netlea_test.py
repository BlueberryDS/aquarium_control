import asyncio
from bleak import BleakClient

# -------------------------
# BLE UUIDs (from APK)
# -------------------------
SERVICE_UUID = "0003CBBB-0000-1000-8000-00805F9BFFF0"
WRITE_CHAR_UUID = "0003CBBE-0000-1000-8000-00805F9BFFF0"

DEVICE_MAC = "50:78:7D:B7:46:62"  # <-- replace with your N7 MAC

# -------------------------
# Netlea encode layer
# -------------------------
SEED = b"Leds&Fun"

def calc_check_byte(payload: bytes) -> int:
    s = 0
    for i, b in enumerate(payload):
        s += b ^ SEED[i % len(SEED)]
    return s & 0xFF

def encode_payload(plaintext: bytes) -> bytes:
    chk = calc_check_byte(plaintext)
    encoded = bytes([chk]) + bytes([b ^ chk for b in plaintext])
    return encoded

# -------------------------
# Build PWM command
# -------------------------
def build_pwm_frame(B, G, R, W, F):
    """
    Channel order is DEVICE order:
    [B, G, R, W, F]
    Values: 0–255
    """

    frame = bytes.fromhex(
        "5A 01 10 00 00 "    # header
        "01 00 00 00 "       # mode flags
    )

    frame += bytes([B, G, R, W, F])

    frame += bytes.fromhex(
        "84 03 00 "          # fixed trailer
        "01 00 "             # frame counter / seq (safe = 1)
        "00 00"              # padding
    )

    return frame

# -------------------------
# Send one command
# -------------------------
async def send_pwm():
    pwm_plain = build_pwm_frame(
        B=0,    # Blue
        G=0,    # Green
        R=80,   # Red
        W=120,  # White
        F=30    # F channel (likely violet)
    )

    pwm_encoded = encode_payload(pwm_plain)

    async with BleakClient(DEVICE_MAC) as client:
        await client.write_gatt_char(
            WRITE_CHAR_UUID,
            pwm_encoded,
            response=False   # IMPORTANT: write without response
        )

        print("PWM command sent")

asyncio.run(send_pwm())
