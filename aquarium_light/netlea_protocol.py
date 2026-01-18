from __future__ import annotations

from typing import Dict, Iterable, List


def _crc8_sum(hex_str: str) -> str:
    total = 0
    for idx in range(0, len(hex_str), 2):
        total += int(hex_str[idx:idx + 2], 16)
    total %= 256
    return f"{total:02X}"


def frame_payload_hex(payload_hex: str) -> str:
    """
    Frame a Netlea BLE payload (which must start with 5A...) with length + CRC.
    """
    payload_hex = payload_hex.upper()
    if len(payload_hex) < 2 or not payload_hex.startswith("5A"):
        raise ValueError("payload_hex must start with 5A")
    if len(payload_hex) % 2 != 0:
        raise ValueError("payload_hex must be an even-length hex string")

    payload_len = len(payload_hex) // 2
    length = payload_len + 2
    framed = payload_hex[:2] + f"{length:02X}" + payload_hex[2:]
    return framed + _crc8_sum(framed)


def pwm_hex_from_channels(
    channel_order: Iterable[str],
    channel_values: Dict[str, int],
) -> str:
    """
    Build the 5-byte PWM hex string from the device's channel order.
    """
    parts: List[str] = []
    for name in channel_order:
        value = channel_values.get(name, 0)
        if not 0 <= value <= 255:
            raise ValueError(f"channel '{name}' value must be 0..255")
        parts.append(f"{value:02X}")
    return "".join(parts)


def build_main_control_payload(
    *,
    dev_type: int,
    pwm_hex: str,
    model_id: int,
    number: int,
    onoff: int = 1,
    forever: int = 1,
    restore_minutes: int = 0,
) -> str:
    """
    Build the raw payload (starting with 5A..) for direct main light control.
    """
    if len(pwm_hex) != 10:
        raise ValueError("pwm_hex must be exactly 10 hex chars (5 bytes)")

    restore_le = restore_minutes.to_bytes(2, "little").hex().upper()
    number_le = number.to_bytes(2, "little").hex().upper()

    return (
        f"5A{dev_type:02X}100000"
        f"{onoff:02X}{forever:02X}0000"
        f"{pwm_hex.upper()}"
        f"{restore_le}"
        f"{model_id:02X}"
        f"{number_le}"
        "0000"
    )


def build_main_control_packet(
    *,
    dev_type: int,
    pwm_hex: str,
    model_id: int,
    number: int,
    onoff: int = 1,
    forever: int = 1,
    restore_minutes: int = 0,
) -> bytes:
    """
    Build the full framed packet as bytes for writing to FF02.
    """
    payload = build_main_control_payload(
        dev_type=dev_type,
        pwm_hex=pwm_hex,
        model_id=model_id,
        number=number,
        onoff=onoff,
        forever=forever,
        restore_minutes=restore_minutes,
    )
    framed = frame_payload_hex(payload)
    return bytes.fromhex(framed)
