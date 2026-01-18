#!/usr/bin/env python3
"""
Helpers for fast-forward simulation and ASCII preview of SunCurve.
"""

import time
from typing import List, Tuple

from suncurve import SunCurve

CYCLE_SECONDS_DEFAULT = 60.0


def simulated_time_hours(
    curve: SunCurve,
    start_monotonic: float,
    cycle_seconds: float = CYCLE_SECONDS_DEFAULT,
) -> float:
    elapsed = (time.monotonic() - start_monotonic) % cycle_seconds
    frac = elapsed / cycle_seconds  # 0..1
    t_hours = (curve.t_start + frac * curve.D) % 24.0
    return t_hours


def _sample_curve(
    curve: SunCurve,
    num_samples: int,
) -> Tuple[List[float], List[float], List[bool]]:
    if num_samples <= 0:
        num_samples = 1

    B_vals: List[float] = []
    C_vals: List[float] = []
    on_flags: List[bool] = []

    if curve.D <= 0:
        t_list = [curve.t_start]
    else:
        t_list = [
            curve.t_start + (curve.D * i / (num_samples - 1 if num_samples > 1 else 1))
            for i in range(num_samples)
        ]

    for t_h in t_list:
        B_i, C_i, is_on = curve.sample(t_h, raw=False)
        B_vals.append(float(B_i if is_on else 0.0))
        C_vals.append(float(C_i if is_on else 0.0))
        on_flags.append(bool(is_on))

    return B_vals, C_vals, on_flags


def _build_ascii_block(
    values: List[float],
    on_flags: List[bool],
    height: int,
    title: str,
    char_on: str = "#",
) -> str:
    lines: List[str] = []

    lines.append(f"--- {title} ---")
    if not values or max(values) <= 0:
        lines.append("(no data)")
        return "\n".join(lines)

    max_v = max(values)
    scaled = [v / max_v for v in values]

    if height < 2:
        height = 2

    for row in reversed(range(height)):
        thresh = row / (height - 1)
        row_chars = []
        for v, is_on in zip(scaled, on_flags):
            if v >= thresh:
                if is_on:
                    row_chars.append(char_on)
                else:
                    row_chars.append(".")
            else:
                row_chars.append(" ")
        lines.append("".join(row_chars))

    return "\n".join(lines)


def ascii_preview_string(
    curve: SunCurve,
    width: int = 80,
    height_brightness: int = 10,
    height_cct: int = 6,
) -> str:
    """
    Build an ASCII preview of the brightness and CCT curves over one daylight
    window [t_start, t_end], and return it as a single string.
    """
    lines: List[str] = []
    lines.append("")
    lines.append("====================================")
    lines.append(" SunCurve ASCII preview (one 'day') ")
    lines.append("====================================")
    lines.append(
        f"t_start={curve.t_start:.2f}h, "
        f"t_end={curve.t_end:.2f}h, "
        f"D={curve.D:.2f}h"
    )

    B_vals, C_vals, on_flags = _sample_curve(curve, num_samples=width)

    # Brightness graph
    lines.append("")
    lines.append(_build_ascii_block(
        values=B_vals,
        on_flags=on_flags,
        height=height_brightness,
        title="Brightness (relative device units)",
        char_on="#",
    ))

    # CCT graph
    lines.append("")
    lines.append(_build_ascii_block(
        values=C_vals,
        on_flags=on_flags,
        height=height_cct,
        title="CCT (relative device units)",
        char_on="*",
    ))

    lines.append("")
    lines.append(
        f"time axis: left={curve.t_start:.1f}h  "
        f"mid={(curve.t_start + curve.D / 2.0):.1f}h  "
        f"right={curve.t_start + curve.D:.1f}h"
    )
    lines.append("====================================")
    lines.append("")

    return "\n".join(lines)


def _sample_rgbw_curve(
    curve,
    num_samples: int,
) -> Tuple[List[float], List[float], List[float], List[float], List[bool]]:
    if num_samples <= 0:
        num_samples = 1

    r_vals: List[float] = []
    g_vals: List[float] = []
    b_vals: List[float] = []
    w_vals: List[float] = []
    on_flags: List[bool] = []

    if getattr(curve, "D", 0.0) <= 0:
        t_list = [curve.t_start]
    else:
        t_list = [
            curve.t_start + (curve.D * i / (num_samples - 1 if num_samples > 1 else 1))
            for i in range(num_samples)
        ]

    for t_h in t_list:
        r, g, b, w, is_on = curve.sample(t_h, raw=True)
        r_vals.append(float(r if is_on else 0.0))
        g_vals.append(float(g if is_on else 0.0))
        b_vals.append(float(b if is_on else 0.0))
        w_vals.append(float(w if is_on else 0.0))
        on_flags.append(bool(is_on))

    return r_vals, g_vals, b_vals, w_vals, on_flags


def ascii_preview_rgbw_string(
    curve,
    width: int = 80,
    height_channel: int = 6,
) -> str:
    """
    Build an ASCII preview of RGBW channels over one daylight window [t_start, t_end],
    using the curve's raw RGBW output (linear 0..1).
    """
    lines: List[str] = []
    lines.append("")
    lines.append("====================================")
    lines.append(" RGBW Curve ASCII preview (one 'day') ")
    lines.append("====================================")
    lines.append(
        f"t_start={curve.t_start:.2f}h, "
        f"t_end={curve.t_end:.2f}h, "
        f"D={curve.D:.2f}h"
    )

    r_vals, g_vals, b_vals, w_vals, on_flags = _sample_rgbw_curve(curve, num_samples=width)

    lines.append("")
    lines.append(_build_ascii_block(
        values=r_vals,
        on_flags=on_flags,
        height=height_channel,
        title="Red (relative)",
        char_on="R",
    ))

    lines.append("")
    lines.append(_build_ascii_block(
        values=g_vals,
        on_flags=on_flags,
        height=height_channel,
        title="Green (relative)",
        char_on="G",
    ))

    lines.append("")
    lines.append(_build_ascii_block(
        values=b_vals,
        on_flags=on_flags,
        height=height_channel,
        title="Blue (relative)",
        char_on="B",
    ))

    lines.append("")
    lines.append(_build_ascii_block(
        values=w_vals,
        on_flags=on_flags,
        height=height_channel,
        title="White (relative)",
        char_on="W",
    ))

    lines.append("")
    lines.append(
        f"time axis: left={curve.t_start:.1f}h  "
        f"mid={(curve.t_start + curve.D / 2.0):.1f}h  "
        f"right={curve.t_start + curve.D:.1f}h"
    )
    lines.append("====================================")
    lines.append("")

    return "\n".join(lines)


def print_ascii_preview(
    curve: SunCurve,
    width: int = 80,
    height_brightness: int = 10,
    height_cct: int = 6,
) -> None:
    """
    Backwards-compatible helper: just print the string to stdout.
    """
    print(ascii_preview_string(curve, width, height_brightness, height_cct))
