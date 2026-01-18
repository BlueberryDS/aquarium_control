import asyncio
from bleak import BleakClient

DEVICE = "50:78:7D:B7:46:62"  # replace with your device address (MAC on Linux/Win; UUID on macOS)
#DEVICE = "0x1A0111162B2C830150787DB74662"  # replace with your device address (MAC on Linux/Win; UUID on macOS)

async def main():
    async with BleakClient(DEVICE) as client:
        services = client.services
        for s in services:
            print("SERVICE", s.uuid)
            for c in s.characteristics:
                print(
                    "  CHAR",
                    c.uuid,
                    "props=",
                    ",".join(c.properties)
                )

asyncio.run(main())
