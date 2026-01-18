#!/usr/bin/env python3
"""
netlea_n7.py — Netlea/N7 BLE protocol helper (BlueZ + Bleak)

- Import as a library:
    from netlea_n7 import NetleaN7
    async with NetleaN7(mac) as n7:
        await n7.hello()
        await n7.set_pwm(r=255, w=0, g=0, b=0, f=0)

- Or run as a CLI:
    python netlea_n7.py --mac 50:78:7D:B7:46:62 --hello --send-pwm --w 255

Notes:
- Protocol framing is: 5A LEN ... CHK(sum)
- Many devices default to MTU=23 on BlueZ; the PWM control frame is typically >20 bytes,
  so it often requires write-with-response (response=True) when MTU is small.
- The "schedule init" packet (0x04 ...) appears to flip the device into scheduled mode.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional, Union

from bleak import BleakClient

# UUIDs (service is 0000fffa..., but Bleak can write by characteristic UUID directly)
CHAR_FF02_WRITE = "0000ff02-0000-1000-8000-00805f9b34fb"
CHAR_FF01_NOTIFY = "0000ff01-0000-1000-8000-00805f9b34fb"
CHAR_FFFD_NOTIFY = "0000fffd-0000-1000-8000-00805f9b34fb"  # optional, some firmwares use it

# "Base" (pre-framed) packets you identified
BASE_HELLO_06 = bytes.fromhex("5a010600000169671d3d")  # wraps to: 5a0c0106...98
BASE_SCHEDULE_INIT_04 = bytes.fromhex("5a010400000001881a0112070701")  # flips scheduled mode for your unit
BASE_CMD22 = bytes.fromhex("5a01220000011401")  # optional

# Status probe bases (pre-framed)
def _base_probe(cmd: int, payload: bytes = b"") -> bytes:
    return bytes([0x5A, 0x01, cmd & 0xFF]) + payload


@dataclass
class NotifyFrame:
    ok: bool
    raw: bytes
    dev: Optional[int] = None
    cmd: Optional[int] = None
    payload: bytes = b""
    reason: str = ""


NotifyCallback = Callable[[NotifyFrame], None]


def _clamp8(x: int) -> int:
    return max(0, min(255, int(x)))


def _le16(n: int) -> bytes:
    n &= 0xFFFF
    return bytes([n & 0xFF, (n >> 8) & 0xFF])


def wrap_len_sum(base: bytes) -> bytes:
    """
    Frame a base packet that starts with 0x5A:

      out = 5A LEN base[1:] CHK

    LEN includes 5A and CHK.
    CHK = sum(all bytes except CHK) & 0xFF
    """
    if not base or base[0] != 0x5A:
        raise ValueError("base must start with 0x5A")
    out = bytearray([0x5A, len(base) + 2])  # add LEN + CHK
    out.extend(base[1:])
    out.append(sum(out) & 0xFF)
    return bytes(out)


def parse_notify_frame(data: Union[bytes, bytearray]) -> NotifyFrame:
    b = bytes(data)
    if len(b) < 5:
        return NotifyFrame(ok=False, raw=b, reason="too short")
    if b[0] != 0x5A:
        return NotifyFrame(ok=False, raw=b, reason="bad preamble")
    if b[1] != len(b):
        return NotifyFrame(ok=False, raw=b, reason=f"len mismatch field={b[1]} actual={len(b)}")
    chk = sum(b[:-1]) & 0xFF
    if chk != b[-1]:
        return NotifyFrame(ok=False, raw=b, reason=f"bad checksum want={chk:02x} got={b[-1]:02x}")
    dev = b[2]
    cmd = b[3]
    payload = b[4:-1]
    return NotifyFrame(ok=True, raw=b, dev=dev, cmd=cmd, payload=payload)


class NetleaN7:
    """
    Async BLE client for Netlea/N7 protocol.

    PWM byte order (your current hypothesis):
      [R, W, G, B, F]  where F often behaves like fan/aux.

    You can change mapping by editing pwm_bytes().
    """

    def __init__(
        self,
        mac: str,
        *,
        notify: bool = True,
        notify_char: str = CHAR_FF01_NOTIFY,
        also_notify_fffd: bool = False,
        notify_callback: Optional[NotifyCallback] = None,
        verbose: bool = True,
    ):
        self.mac = mac
        self.notify = notify
        self.notify_char = notify_char
        self.also_notify_fffd = also_notify_fffd
        self._cb = notify_callback
        self.verbose = verbose
        self.client: Optional[BleakClient] = None

    # -------- lifecycle --------

    async def __aenter__(self) -> "NetleaN7":
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.disconnect()

    async def connect(self) -> None:
        if self.client is not None:
            return
        self.client = BleakClient(self.mac)
        await self.client.connect()

        if self.verbose:
            mtu = getattr(self.client, "mtu_size", None)
            print(f"[n7] connected mtu={mtu}")

        if self.notify:
            await self._start_notify()

    async def disconnect(self) -> None:
        if self.client is None:
            return
        try:
            if self.notify:
                await self._stop_notify()
        finally:
            await self.client.disconnect()
            self.client = None
            if self.verbose:
                print("[n7] disconnected")

    # -------- notify --------

    async def _start_notify(self) -> None:
        assert self.client is not None
        await self.client.start_notify(self.notify_char, self._on_notify)
        if self.verbose:
            print(f"[n7] notify enabled on {self.notify_char}")

        if self.also_notify_fffd:
            try:
                await self.client.start_notify(CHAR_FFFD_NOTIFY, self._on_notify)
                if self.verbose:
                    print(f"[n7] notify enabled on {CHAR_FFFD_NOTIFY}")
            except Exception as e:
                if self.verbose:
                    print(f"[n7] notify enable failed on {CHAR_FFFD_NOTIFY}: {e!r}")

    async def _stop_notify(self) -> None:
        assert self.client is not None
        try:
            await self.client.stop_notify(self.notify_char)
        except Exception:
            pass
        if self.also_notify_fffd:
            try:
                await self.client.stop_notify(CHAR_FFFD_NOTIFY)
            except Exception:
                pass

    def _on_notify(self, sender, data: bytearray) -> None:
        frame = parse_notify_frame(data)

        if self._cb is not None:
            self._cb(frame)
            return

        # Default: print only valid frames (or if verbose, print invalid too)
        if frame.ok:
            print(
                f"[notify] dev=0x{frame.dev:02x} cmd=0x{frame.cmd:02x} "
                f"payload={frame.payload.hex()} raw={frame.raw.hex()}"
            )
        elif self.verbose:
            print(f"[notify] INVALID({frame.reason}) raw={frame.raw.hex()}")

    # -------- low-level send --------

    def _choose_response(self, framed_len: int, *, force: Optional[bool] = None) -> bool:
        """
        Choose response=True if needed for > (MTU-3) payload constraints, unless forced.
        """
        if force is not None:
            return force
        mtu = getattr(self.client, "mtu_size", None) if self.client else None
        max_wo_resp = 20
        if isinstance(mtu, int) and mtu >= 23:
            max_wo_resp = mtu - 3
        # If the framed packet won't fit as a write command, prefer write-with-response
        return framed_len > max_wo_resp

    async def send_base(
        self,
        base: bytes,
        *,
        response: Optional[bool] = None,
        label: str = "",
    ) -> bytes:
        """
        Send a base packet (starts with 0x5A). Returns framed bytes.
        """
        assert self.client is not None
        framed = wrap_len_sum(base)
        use_resp = self._choose_response(len(framed), force=response)

        if self.verbose:
            tag = f" {label}" if label else ""
            mtu = getattr(self.client, "mtu_size", None)
            print(f"[tx]{tag} len={len(framed)} mtu={mtu} response={use_resp}  {framed.hex()}")

        await self.client.write_gatt_char(CHAR_FF02_WRITE, framed, response=use_resp)
        return framed

    # -------- protocol helpers --------

    async def hello(self, *, response: Optional[bool] = False) -> bytes:
        # small enough for response=False
        return await self.send_base(BASE_HELLO_06, response=response, label="hello(0x06)")

    async def schedule_init(self, *, response: Optional[bool] = False) -> bytes:
        # flips schedule/program mode on your unit
        return await self.send_base(BASE_SCHEDULE_INIT_04, response=response, label="schedule-init(0x04)")

    async def cmd22(self, *, response: Optional[bool] = False) -> bytes:
        return await self.send_base(BASE_CMD22, response=response, label="cmd22(0x22)")

    async def status_probes(
        self,
        *,
        include_1d: bool = True,
        delay_s: float = 0.10,
        response: Optional[bool] = False,
    ) -> None:
        probes = [
            ("q15", _base_probe(0x15, b"\x00\x00")),
            ("q1b", _base_probe(0x1B, b"\x00\x00")),
        ]
        if include_1d:
            probes.append(("q1d", _base_probe(0x1D, b"\x00\x00")))
        probes += [
            ("q21", _base_probe(0x21, b"\x00\x00")),
            ("q25", _base_probe(0x25, b"\x00\x00\x07")),  # 3-byte payload
        ]
        for name, base in probes:
            await self.send_base(base, response=response, label=name)
            await asyncio.sleep(delay_s)

    # -------- PWM control --------

    def pwm_bytes(self, *, r: int, w: int, g: int, b: int, f: int) -> bytes:
        """
        PWM byte order (current best guess):
          [R, W, G, B, F]
        """
        return bytes([_clamp8(r), _clamp8(w), _clamp8(g), _clamp8(b), _clamp8(f)])

    def build_pwm_control_base(
        self,
        *,
        r: int,
        w: int,
        g: int,
        b: int,
        f: int,
        onoff: Optional[int] = None,
        forever: int = 1,
        fade_s: int = 0,
        model_id: int = 0,
        number: int = 1,
        dev: int = 0x01,
    ) -> bytes:
        """
        Base (pre-wrap) matching app sendControlParas:

          5A dev 10 00 00 onoff forever 00 00
             pwm5
             fadeLE(2)
             modelId(1)
             numberLE(2)
             00 00
        """
        pwm5 = self.pwm_bytes(r=r, w=w, g=g, b=b, f=f)

        if onoff is None:
            onoff = 1 if any(pwm5) else 0

        # App behavior: if off, PWM bytes are forced to 00..00
        if onoff == 0:
            pwm5 = b"\x00\x00\x00\x00\x00"

        base = (
            bytes([0x5A, dev & 0xFF, 0x10, 0x00, 0x00, onoff & 0xFF, forever & 0xFF, 0x00, 0x00])
            + pwm5
            + _le16(fade_s)
            + bytes([model_id & 0xFF])
            + _le16(number)
            + b"\x00\x00"
        )
        return base

    async def set_pwm(
        self,
        *,
        r: int,
        w: int,
        g: int,
        b: int,
        f: int,
        onoff: Optional[int] = None,
        forever: int = 1,
        fade_s: int = 0,
        model_id: int = 0,
        number: int = 1,
        dev: int = 0x01,
        response: Optional[bool] = None,
    ) -> bytes:
        base = self.build_pwm_control_base(
            r=r, w=w, g=g, b=b, f=f,
            onoff=onoff,
            forever=forever,
            fade_s=fade_s,
            model_id=model_id,
            number=number,
            dev=dev,
        )
        return await self.send_base(base, response=response, label=f"pwm(0x10) R={r} W={w} G={g} B={b} F={f}")


# ---------------- CLI ----------------

def _make_argparser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Netlea/N7 BLE controller (library + CLI)")
    ap.add_argument("--mac", required=True, help="BLE MAC, e.g. 50:78:7D:B7:46:62")

    # Optional packets
    ap.add_argument("--hello", action="store_true", help="Send hello packet (0x06 ...)")
    ap.add_argument("--schedule-init", action="store_true", help="Send schedule init packet (0x04 ...070701)")
    ap.add_argument("--cmd22", action="store_true", help="Send cmd22 packet (0x22 ...)")
    ap.add_argument("--status", action="store_true", help="Send status probe burst (0x15/0x1b/0x1d/0x21/0x25)")
    ap.add_argument("--no-1d", action="store_true", help="Skip the 0x1D status probe")
    ap.add_argument("--status-delay", type=float, default=0.10, help="Delay between status probes (seconds)")

    # PWM send is optional now
    ap.add_argument("--send-pwm", action="store_true", help="Actually send the PWM control packet (0x10)")
    ap.add_argument("--r", type=int, default=0)
    ap.add_argument("--w", type=int, default=0)
    ap.add_argument("--g", type=int, default=0)
    ap.add_argument("--b", type=int, default=0)
    ap.add_argument("--f", type=int, default=0, help="fan/aux guess")
    ap.add_argument("--fade", type=int, default=0, help="fade seconds (LE16)")
    ap.add_argument("--forever", type=int, default=1, choices=[0, 1], help="mainForever: 1=stick, 0=temporary")
    ap.add_argument("--onoff", type=int, choices=[0, 1], default=None, help="Force onoff field (default inferred)")

    # IO behavior
    ap.add_argument("--quiet", action="store_true", help="Less printing")
    ap.add_argument("--no-notify", action="store_true", help="Do not subscribe to notifications")
    ap.add_argument("--also-notify-fffd", action="store_true", help="Also subscribe to 0000fffd notifications")

    # Force write type (optional)
    ap.add_argument("--force-response", action="store_true", help="Force response=True for all writes")
    ap.add_argument("--force-no-response", action="store_true", help="Force response=False for all writes")

    ap.add_argument("--wait", type=float, default=1.0, help="Seconds to wait for notifications before exit")
    return ap


async def _cli_main() -> None:
    args = _make_argparser().parse_args()

    force_resp: Optional[bool] = None
    if args.force_response and args.force_no_response:
        raise SystemExit("Pick only one of --force-response or --force-no-response")
    if args.force_response:
        force_resp = True
    if args.force_no_response:
        force_resp = False

    async with NetleaN7(
        args.mac,
        notify=(not args.no_notify),
        also_notify_fffd=args.also_notify_fffd,
        verbose=(not args.quiet),
    ) as n7:
        # optional packets
        if args.hello:
            await n7.hello(response=force_resp if force_resp is not None else False)
            await asyncio.sleep(0.05)

        if args.schedule_init:
            await n7.schedule_init(response=force_resp if force_resp is not None else False)
            await asyncio.sleep(0.10)

        if args.cmd22:
            await n7.cmd22(response=force_resp if force_resp is not None else False)
            await asyncio.sleep(0.10)

        if args.status:
            await n7.status_probes(
                include_1d=(not args.no_1d),
                delay_s=args.status_delay,
                response=force_resp if force_resp is not None else False,
            )

        if args.send_pwm:
            await n7.set_pwm(
                r=args.r, w=args.w, g=args.g, b=args.b, f=args.f,
                fade_s=args.fade,
                forever=args.forever,
                onoff=args.onoff,
                response=force_resp,  # None => auto based on MTU
            )

        # wait a bit to receive any notifications
        if args.wait > 0:
            await asyncio.sleep(args.wait)


if __name__ == "__main__":
    asyncio.run(_cli_main())
