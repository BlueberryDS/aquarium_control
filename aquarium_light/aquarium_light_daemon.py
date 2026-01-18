#!/usr/bin/env python3
import argparse
import signal
import time
from typing import Dict, Any
from datetime import datetime, date, timezone

import tinytuya

from suncurve import SunCurve
from simulate import simulated_time_hours, ascii_preview_string
from mooncurve import MoonCurve
from config_loader import load_runtime_config
from clouds import CloudDeltaWithShimmer  # clouds + shimmer


# ==========================
#  Helper functions
# ==========================

def init_device(tuya_cfg: Dict[str, Any]) -> tinytuya.BulbDevice:
    print("[tuya] Creating BulbDevice with auto-discovery...")
    dev = tinytuya.BulbDevice(
        dev_id=tuya_cfg["tuya_device_id"],
        address="Auto",
        local_key=tuya_cfg["tuya_local_key"],
        version=tuya_cfg["tuya_protocol_version"],
    )
    dev.set_socketPersistent(True)

    try:
        status = dev.status()
        print("[tuya] Initial status:", status)
    except Exception as e:
        print("[tuya] Initial status() failed:", e)

    return dev


def current_time_hours_local() -> float:
    tm = time.localtime()
    return (tm.tm_hour +
            tm.tm_min / 60.0 +
            tm.tm_sec / 3600.0) % 24.0


def kelvin_to_cct_dev(
    T_kelvin: float,
    T_min: float,
    T_max: float,
    dev_max: int,
) -> int:
    """
    Map a color temperature in Kelvin into the Tuya CCT device range [0..dev_max].
    Simple linear mapping between [T_min, T_max].
    """
    if T_max <= T_min:
        return int(dev_max // 2)

    frac = (T_kelvin - T_min) / (T_max - T_min)
    frac = max(0.0, min(1.0, frac))
    return int(round(frac * dev_max))


def turn_off_light(dev: tinytuya.BulbDevice | None, tuya_cfg: Dict[str, Any]) -> None:
    """
    Ensure the light is OFF.
    If an existing dev handle is provided, reuse it; otherwise init a new one.
    """
    print("[cleanup] Turning light OFF...")
    try:
        if dev is None:
            dev = init_device(tuya_cfg)
        dps_power = tuya_cfg["tuya_dps_id_power"]
        dev.set_value(dps_power, False)
        print("[cleanup] OFF command sent.")
    except Exception as e:
        print("[cleanup] Error while turning off light:", e)


def turn_off_light_once(tuya_cfg: Dict[str, Any]) -> None:
    """
    One-shot: connect and turn the light off, then exit.
    Intended for systemd ExecStop or manual use.
    """
    turn_off_light(dev=None, tuya_cfg=tuya_cfg)


# ==========================
#  Main
# ==========================

stop_requested = False


def _signal_handler(signum, frame):
    global stop_requested
    print(f"[signal] Received signal {signum}, requesting shutdown...")
    stop_requested = True


def main():
    global stop_requested

    parser = argparse.ArgumentParser(
        description="Aquarium Tuya light controller using SunCurve + MoonCurve + Clouds with JSON-config."
    )
    parser.add_argument(
        "--test-mode",
        action="store_true",
        help="Run 60-second accelerated daylight-window simulation (no off hours)."
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose loop logging."
    )
    parser.add_argument(
        "--off-once",
        action="store_true",
        help="Turn the light off once and exit (used by systemd ExecStop or manual)."
    )
    parser.add_argument(
        "--config",
        default="config.json",
        help="Path to JSON config file (default: config.json)."
    )
    parser.add_argument(
        "--print-curve",
        action="store_true",
        help="Print ASCII SunCurve + daily stats for the given date and exit (no Tuya I/O)."
    )
    parser.add_argument(
        "--date",
        type=str,
        help="Date (YYYY-MM-DD) whose versioned config should be used; defaults to today."
    )

    args = parser.parse_args()
    verbose = args.verbose

    # Optional date override for config interpolation / stats
    if args.date:
        try:
            override_datetime = datetime.fromisoformat(args.date)
        except ValueError:
            print(f"[config] Invalid --date '{args.date}', expected YYYY-MM-DD")
            return
    else:
        override_datetime = None

    # Load (possibly interpolated) config for the chosen date
    sun_cfg, moon_cfg, tuya_cfg, global_cfg = load_runtime_config(
        args.config,
        now=override_datetime,
    )

    # --- SunCurve config (versioned) ---

    curve = SunCurve(
        t_start=sun_cfg["day_start_hour_local"],
        t_end=sun_cfg["day_end_hour_local"],
        H_eq=sun_cfg["day_equivalent_full_brightness_hours"],
        B_peak_max=sun_cfg["day_peak_brightness_fraction"],
        tau_minutes=sun_cfg["day_smoothing_time_constant_minutes"],
        delta_T=sun_cfg["day_color_transition_range_kelvin"],
        T_min=sun_cfg["day_min_color_temp_kelvin"],
        T_max=sun_cfg["day_max_color_temp_kelvin"],
        T_blue=sun_cfg["day_blue_hour_temp_kelvin"],
    )

    if getattr(curve, "warning", None):
        print(curve.warning)

    # --- MoonCurve config (versioned) ---

    moon_max_brightness = float(moon_cfg.get("moon_max_brightness_fraction", 0.05))
    moon_cct_k = float(moon_cfg.get("moon_color_temp_kelvin", 6500.0))
    moon = MoonCurve(max_brightness=moon_max_brightness)

    # --- Clouds (always-on, applied to combined sky brightness) ---

    clouds = CloudDeltaWithShimmer(seed=None)

    # --- NEW MODE: just print curve + stats and exit ---

    if args.print_curve:
        # ASCII curve (same as preview file content)
        preview_text = ascii_preview_string(curve)
        print(preview_text)

        # Date we are describing
        if override_datetime is not None:
            day_str = override_datetime.date().isoformat()
            moon_dt = datetime(
                override_datetime.year,
                override_datetime.month,
                override_datetime.day,
                12, 0, 0,
                tzinfo=timezone.utc,
            )
        else:
            today = date.today()
            day_str = today.isoformat()
            moon_dt = datetime(
                today.year,
                today.month,
                today.day,
                12, 0, 0,
                tzinfo=timezone.utc,
            )

        moon_state_for_day = moon.get_state(moon_dt)

        print("=== Daily Stats ===")
        print(f"Date (config versioning): {day_str}")
        print(
            f"Day window (local hours): "
            f"{curve.t_start:.2f} -> {curve.t_end:.2f} (length {curve.D:.2f} h)"
        )
        print(
            f"Equivalent full-brightness hours: "
            f"{sun_cfg['day_equivalent_full_brightness_hours']:.2f}"
        )
        print(
            f"Peak brightness fraction (day): "
            f"{sun_cfg['day_peak_brightness_fraction']:.3f}"
        )
        print(
            f"Color temp range (day): "
            f"{sun_cfg['day_min_color_temp_kelvin']:.0f} K "
            f"→ {sun_cfg['day_max_color_temp_kelvin']:.0f} K "
            f"(blue hour ~{sun_cfg['day_blue_hour_temp_kelvin']:.0f} K)"
        )
        print("")
        print(f"Moon max brightness fraction: {moon_max_brightness:.3f}")
        print(
            f"Moon brightness fraction (around local 'day' noon UTC): "
            f"{moon_state_for_day['brightness']:.3f}"
        )
        print(
            f"Moon illumination fraction: "
            f"{moon_state_for_day['illumination']:.3f}"
        )
        print(
            f"Moon phase fraction (0=new, 0.5=full): "
            f"{moon_state_for_day['phase_fraction']:.3f}"
        )
        print(
            f"Moon age: {moon_state_for_day['age_days']:.2f} / "
            f"{moon_state_for_day['synodic_days']:.2f} days"
        )
        return

    # --- One-shot OFF mode: no loop (but we still needed the Tuya config) ---

    if args.off_once:
        turn_off_light_once(tuya_cfg)
        return

    # --- Tuya constants (non-versioned) ---

    brightness_dev_max = int(tuya_cfg.get("tuya_brightness_scale_max", 1000))
    cct_dev_max = int(tuya_cfg.get("tuya_cct_scale_max", 1000))

    dps_power = tuya_cfg["tuya_dps_id_power"]
    dps_mode = tuya_cfg["tuya_dps_id_mode"]
    dps_bright = tuya_cfg["tuya_dps_id_brightness"]
    dps_cct = tuya_cfg["tuya_dps_id_cct"]

    # --- Global constants (non-versioned) ---

    step_seconds_real = float(global_cfg.get("tick_interval_seconds", 5.0))
    step_seconds_test = float(global_cfg.get("tick_interval_seconds_test", 1.0))
    preview_path = global_cfg.get("ascii_preview_output_path", "/tmp/suncurve_preview.txt")

    # Precompute moon CCT in device units
    moon_cct_dev = kelvin_to_cct_dev(
        moon_cct_k,
        T_min=sun_cfg["day_min_color_temp_kelvin"],
        T_max=sun_cfg["day_max_color_temp_kelvin"],
        dev_max=cct_dev_max,
    )

    # ASCII preview of SunCurve written to a file on startup
    try:
        preview_text = ascii_preview_string(curve)
        with open(preview_path, "w") as f:
            f.write(preview_text + "\n")
        print(f"[preview] Wrote SunCurve ASCII preview to {preview_path}")
    except Exception as e:
        print(f"[preview] Failed to write preview file {preview_path}: {e}")

    # Install signal handlers for normal daemon mode
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    step_seconds = step_seconds_test if args.test_mode else step_seconds_real
    test_start_monotonic = time.monotonic() if args.test_mode else None

    dev: tinytuya.BulbDevice | None = None

    if args.test_mode:
        print("[mode] TEST MODE: 60s daylight-window simulation "
              f"(day_start={curve.t_start}, D={curve.D:.2f}h), step={step_seconds:.1f}s")
    else:
        print("[mode] REAL TIME: local clock, "
              f"step={step_seconds:.1f}s, day_length={curve.D:.2f}h")

    try:
        while not stop_requested:
            now_ts = time.time()

            if dev is None:
                dev = init_device(tuya_cfg)

            # Sun time source
            if args.test_mode:
                t_hours = simulated_time_hours(curve, test_start_monotonic)
            else:
                t_hours = current_time_hours_local()

            # --- Unclouded sunlight ---
            B_sun_raw_dev, C_sun_dev, sun_on = curve.sample(t_hours, raw=False)
            if not sun_on:
                B_sun_raw_dev = 0

            # --- Unclouded moonlight ---
            moon_state = moon.get_state()
            if moon_state["on"]:
                B_moon_raw_dev = int(round(moon_state["brightness"] * brightness_dev_max))
            else:
                B_moon_raw_dev = 0

            # If nothing is on, turn off and continue
            if B_sun_raw_dev <= 0 and B_moon_raw_dev <= 0:
                try:
                    dev.set_value(dps_power, False)
                except Exception as e:
                    print("[tuya] Error talking to device:", e)
                    dev = None
                time.sleep(step_seconds)
                continue

            # Determine which source dominates BEFORE clouds (for CCT)
            if B_sun_raw_dev >= B_moon_raw_dev and sun_on:
                dominant_source = "sun"
            else:
                dominant_source = "moon"

            # Combined unclouded brightness fraction
            base_dev = max(B_sun_raw_dev, B_moon_raw_dev)
            base_frac = max(0.0, min(1.0, base_dev / brightness_dev_max))

            # Apply clouds to combined sky brightness
            cloud_factor = clouds.get_multiplier(now_ts, base_frac)
            final_frac = max(0.0, min(1.0, base_frac * cloud_factor))
            B_combined = int(round(final_frac * brightness_dev_max))
            on_combined = B_combined > 0

            # Choose CCT based on dominant unclouded source
            if not on_combined:
                C_dev = C_sun_dev  # irrelevant; power off anyway
            elif dominant_source == "sun":
                C_dev = C_sun_dev
            else:
                C_dev = moon_cct_dev

            if verbose:
                print(
                    f"[loop] t={t_hours:5.2f}h "
                    f"sun_raw={B_sun_raw_dev:4d} moon_raw={B_moon_raw_dev:4d} "
                    f"base_frac={base_frac:5.3f} cloud_factor={cloud_factor:5.3f} "
                    f"-> on={on_combined} B={B_combined:4d} CCT={C_dev:4d} "
                    f"dom={dominant_source}"
                )

            try:
                if on_combined:
                    dev.set_value(dps_power, True)
                    dev.set_value(dps_mode, "white")
                    dev.set_value(dps_bright, B_combined)
                    dev.set_value(dps_cct, C_dev)
                else:
                    dev.set_value(dps_power, False)
            except Exception as e:
                print("[tuya] Error talking to device:", e)
                dev = None  # force re-init next loop

            time.sleep(step_seconds)

    except Exception as e:
        print("[main] Unexpected exception:", e)
    finally:
        turn_off_light(dev, tuya_cfg)


if __name__ == "__main__":
    main()
