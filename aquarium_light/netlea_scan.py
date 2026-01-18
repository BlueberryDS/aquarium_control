import asyncio
from bleak import BleakClient

DEVICE = "50:78:7D:B7:46:62"  # replace with your device address (MAC on Linux/Win; UUID on macOS)

async def main():
    async with BleakClient(DEVICE) as client:
        svcs = await client.get_services()
        for s in svcs:
            print("SERVICE", s.uuid)
            for c in s.characteristics:
                props = ",".join(c.properties)
                print("  CHAR", c.uuid, f"[{props}]")
                for d in c.descriptors:
                    print("    DESC", d.uuid)

asyncio.run(main())