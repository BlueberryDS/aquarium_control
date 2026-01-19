#!/usr/bin/env python3
import argparse
import asyncio
import signal
import time
from typing import Dict, Any
from datetime import datetime, date, timezone

import tinytuya

from suncurve import SunCurve
from suncurve_rgb import SunCurveRGB
from simulate import simulated_time_hours, ascii_preview_string, ascii_preview_rgbw_string
from mooncurve import MoonCurve
from mooncurve_rgb import MoonCurveRGB
from config_loader import load_runtime_config
from clouds import CloudDeltaWithShimmer  # clouds + shimmer
from netlea_set_pwm import NetleaN7


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


def _cfg_get(
    cfg: Dict[str, Any],
    key: str,
    fallback: Dict[str, Any] | None = None,
    default: Any | None = None,
) -> Any:
    if key in cfg:
        return cfg[key]
    if fallback is not None and key in fallback:
        return fallback[key]
    return default


async def init_netlea(netlea_cfg: Dict[str, Any]) -> NetleaN7 | None:
    if not netlea_cfg.get("netlea_enabled", True):
        return None

    mac = netlea_cfg.get("netlea_mac")
    if not mac:
        return None

    n7 = NetleaN7(
        mac,
        notify=bool(netlea_cfg.get("netlea_notify", False)),
        also_notify_fffd=bool(netlea_cfg.get("netlea_also_notify_fffd", False)),
        verbose=bool(netlea_cfg.get("netlea_verbose", True)),
    )
    await n7.connect()

    response = netlea_cfg.get("netlea_response", False)
    if netlea_cfg.get("netlea_send_hello", False):
        await n7.hello(response=response)
    if netlea_cfg.get("netlea_send_schedule_init", False):
        await n7.schedule_init(response=response)
    if netlea_cfg.get("netlea_send_cmd22", False):
        await n7.cmd22(response=response)

    return n7


async def netlea_send_off(
    dev: NetleaN7 | None,
    netlea_cfg: Dict[str, Any],
) -> None:
    if dev is None:
        return
    try:
        await dev.set_pwm(
            r=0,
            w=0,
            g=0,
            b=0,
            f=0,
            onoff=0,
            fade_s=int(netlea_cfg.get("netlea_fade_seconds", 0)),
            model_id=int(netlea_cfg.get("netlea_model_id", 0)),
            number=int(netlea_cfg.get("netlea_number", 1)),
            dev=int(netlea_cfg.get("netlea_device_id", 1)),
            response=netlea_cfg.get("netlea_response", False),
        )
    except Exception as e:
        print("[netlea] Error while turning off:", e)


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


async def main():
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
        "--light",
        choices=("bridgelux", "netlea", "both"),
        default="both",
        help="Which light(s) to control: bridgelux, netlea, or both (default: both)."
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
    sun_cfg, moon_cfg, bridgelux_cfg, netlea_cfg, netlea_curve_cfg, global_cfg = load_runtime_config(
        args.config,
        now=override_datetime,
    )
    run_bridgelux = args.light in ("bridgelux", "both")
    run_netlea = args.light in ("netlea", "both")

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

    # --- Netlea Sun/Moon config (versioned) ---

    netlea_curve_cfg = netlea_curve_cfg or {}
    netlea_sun_cfg = netlea_curve_cfg.get("sun", {})
    netlea_moon_cfg = netlea_curve_cfg.get("moon", {})

    netlea_channel_luts = netlea_curve_cfg.get("channel_luts")
    netlea_channel_gammas = netlea_curve_cfg.get("channel_gammas")

    rgb_curve = SunCurveRGB(
        t_start=_cfg_get(netlea_sun_cfg, "day_start_hour_local", sun_cfg, 0.0),
        t_end=_cfg_get(netlea_sun_cfg, "day_end_hour_local", sun_cfg, 0.0),
        H_eq=_cfg_get(netlea_sun_cfg, "day_equivalent_full_brightness_hours", sun_cfg, 0.0),
        B_peak_max=_cfg_get(netlea_sun_cfg, "day_peak_brightness_fraction", sun_cfg, 0.0),
        tau_minutes=_cfg_get(netlea_sun_cfg, "day_smoothing_time_constant_minutes", sun_cfg, 0.0),
        delta_T=_cfg_get(netlea_sun_cfg, "day_color_transition_range_kelvin", sun_cfg, 0.0),
        T_min=_cfg_get(netlea_sun_cfg, "day_min_color_temp_kelvin", sun_cfg, 0.0),
        T_max=_cfg_get(netlea_sun_cfg, "day_max_color_temp_kelvin", sun_cfg, 0.0),
        T_blue=_cfg_get(netlea_sun_cfg, "day_blue_hour_temp_kelvin", sun_cfg, 0.0),
        saturation=float(netlea_sun_cfg.get("rgb_saturation", 0.28)),
        tint=float(netlea_sun_cfg.get("rgb_tint", -0.15)),
        saturation_min=float(netlea_sun_cfg.get("rgb_saturation_min", 0.05)),
        saturation_max=float(netlea_sun_cfg.get("rgb_saturation_max", 0.60)),
        tint_min=float(netlea_sun_cfg.get("rgb_tint_min", -0.40)),
        tint_max=float(netlea_sun_cfg.get("rgb_tint_max", 0.25)),
        preserve_total=bool(netlea_sun_cfg.get("rgb_preserve_total", True)),
        pwm_max=int(netlea_cfg.get("netlea_pwm_scale_max", 255)),
        channel_luts=netlea_channel_luts,
        channel_gammas=netlea_channel_gammas,
    )

    rgb_moon = MoonCurveRGB(
        max_brightness=float(netlea_moon_cfg.get("moon_max_brightness_fraction", 0.02)),
        dark_start_hour=float(netlea_moon_cfg.get("moon_dark_start_hour", 2.0)),
        dark_end_hour=float(netlea_moon_cfg.get("moon_dark_end_hour", 7.0)),
        warmth=float(netlea_moon_cfg.get("moon_warmth", 0.0)),
        saturation=float(netlea_moon_cfg.get("moon_saturation", 0.12)),
        tint=float(netlea_moon_cfg.get("moon_tint", 0.0)),
        pwm_max=int(netlea_cfg.get("netlea_pwm_scale_max", 255)),
        channel_luts=netlea_channel_luts,
        channel_gammas=netlea_channel_gammas,
    )

    netlea_enabled = (
        run_netlea
        and bool(netlea_cfg.get("netlea_enabled", True))
        and bool(netlea_cfg.get("netlea_mac"))
    )
    netlea_sim_curve = rgb_curve if run_netlea else None

    # --- Clouds (always-on, applied to combined sky brightness) ---

    clouds = CloudDeltaWithShimmer(seed=None)

    # --- NEW MODE: just print curve + stats and exit ---

    if args.print_curve:
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

        if run_bridgelux:
            print("=== Bridgelux SunCurve Preview ===")
            # ASCII curve (same as preview file content)
            preview_text = ascii_preview_string(curve)
            print(preview_text)

            moon_state_for_day = moon.get_state(moon_dt)

            print("=== Bridgelux Daily Stats ===")
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

        if run_netlea:
            print("=== Netlea SunCurve Preview ===")
            preview_text = ascii_preview_rgbw_string(rgb_curve)
            print(preview_text)

            netlea_moon_state = rgb_moon.get_state(moon_dt)

            print("=== Netlea Daily Stats ===")
            print(f"Date (config versioning): {day_str}")
            print(
                f"Day window (local hours): "
                f"{rgb_curve.t_start:.2f} -> {rgb_curve.t_end:.2f} "
                f"(length {rgb_curve.D:.2f} h)"
            )
            print(
                f"Equivalent full-brightness hours: "
                f"{_cfg_get(netlea_sun_cfg, 'day_equivalent_full_brightness_hours', sun_cfg, 0.0):.2f}"
            )
            print(
                f"Peak brightness fraction (day): "
                f"{_cfg_get(netlea_sun_cfg, 'day_peak_brightness_fraction', sun_cfg, 0.0):.3f}"
            )
            print(
                f"Color temp range (day): "
                f"{_cfg_get(netlea_sun_cfg, 'day_min_color_temp_kelvin', sun_cfg, 0.0):.0f} K "
                f"→ {_cfg_get(netlea_sun_cfg, 'day_max_color_temp_kelvin', sun_cfg, 0.0):.0f} K "
                f"(blue hour ~{_cfg_get(netlea_sun_cfg, 'day_blue_hour_temp_kelvin', sun_cfg, 0.0):.0f} K)"
            )
            print("")
            print(
                f"Moon max brightness fraction: "
                f"{netlea_moon_cfg.get('moon_max_brightness_fraction', 0.0):.3f}"
            )
            print(
                f"Moon brightness fraction (around local 'day' noon UTC): "
                f"{netlea_moon_state['brightness']:.3f}"
            )
            print(
                f"Moon illumination fraction: "
                f"{netlea_moon_state['illumination']:.3f}"
            )
            print(
                f"Moon phase fraction (0=new, 0.5=full): "
                f"{netlea_moon_state['phase_fraction']:.3f}"
            )
            print(
                f"Moon age: {netlea_moon_state['age_days']:.2f} / "
                f"{netlea_moon_state['synodic_days']:.2f} days"
            )
        return

    # --- One-shot OFF mode: no loop (but we still needed the Tuya config) ---

    if args.off_once:
        if run_bridgelux:
            turn_off_light_once(bridgelux_cfg)
        if netlea_enabled:
            try:
                netlea_dev = await init_netlea(netlea_cfg)
                await netlea_send_off(netlea_dev, netlea_cfg)
                if netlea_dev is not None:
                    await netlea_dev.disconnect()
            except Exception as e:
                print("[netlea] Off-once failed:", e)
        return

    # --- Tuya constants (non-versioned) ---

    brightness_dev_max = int(bridgelux_cfg.get("tuya_brightness_scale_max", 1000))
    cct_dev_max = int(bridgelux_cfg.get("tuya_cct_scale_max", 1000))

    dps_power = bridgelux_cfg["tuya_dps_id_power"]
    dps_mode = bridgelux_cfg["tuya_dps_id_mode"]
    dps_bright = bridgelux_cfg["tuya_dps_id_brightness"]
    dps_cct = bridgelux_cfg["tuya_dps_id_cct"]

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
    netlea_dev: NetleaN7 | None = None

    if netlea_enabled:
        print(f"[netlea] Enabled for {netlea_cfg.get('netlea_mac')}")
    elif run_netlea:
        print("[netlea] Disabled (no MAC or netlea_enabled=false)")
    else:
        print("[netlea] Disabled by --light flag")

    if args.test_mode:
        sim_curve = netlea_sim_curve if run_netlea and not run_bridgelux else curve
        print("[mode] TEST MODE: 60s daylight-window simulation "
              f"(day_start={sim_curve.t_start}, D={sim_curve.D:.2f}h), step={step_seconds:.1f}s")
    else:
        print("[mode] REAL TIME: local clock, "
              f"step={step_seconds:.1f}s, day_length={curve.D:.2f}h")

    try:
        while not stop_requested:
            now_ts = time.time()

            if run_bridgelux and dev is None:
                try:
                    dev = init_device(bridgelux_cfg)
                except Exception as e:
                    print("[tuya] Init failed:", e)
                    dev = None

            if netlea_enabled and netlea_dev is None:
                try:
                    netlea_dev = await init_netlea(netlea_cfg)
                except Exception as e:
                    print("[netlea] Init failed:", e)
                    netlea_dev = None

            # Sun time source
            if args.test_mode:
                sim_curve = netlea_sim_curve if run_netlea and not run_bridgelux else curve
                t_hours = simulated_time_hours(sim_curve, test_start_monotonic)
            else:
                t_hours = current_time_hours_local()

            if run_bridgelux:
                # --- Unclouded sunlight (Bridgelux/CCT) ---
                B_sun_raw_dev, C_sun_dev, sun_on = curve.sample(t_hours, raw=False)
                if not sun_on:
                    B_sun_raw_dev = 0

                # --- Unclouded moonlight (Bridgelux/CCT) ---
                moon_state = moon.get_state()
                if moon_state["on"]:
                    B_moon_raw_dev = int(round(moon_state["brightness"] * brightness_dev_max))
                else:
                    B_moon_raw_dev = 0
            else:
                B_sun_raw_dev = 0
                B_moon_raw_dev = 0
                C_sun_dev = 0
                sun_on = False

            if run_netlea:
                # --- Unclouded sunlight (RGBW) ---
                r_sun, g_sun, b_sun, w_sun, rgb_sun_on = rgb_curve.sample(t_hours, raw=True)
                if not rgb_sun_on:
                    r_sun = g_sun = b_sun = w_sun = 0.0

                # --- Unclouded moonlight (RGBW) ---
                rgb_moon_state = rgb_moon.get_state(raw=True)
                if rgb_moon_state["on"]:
                    r_moon = float(rgb_moon_state["r"])
                    g_moon = float(rgb_moon_state["g"])
                    b_moon = float(rgb_moon_state["b"])
                    w_moon = float(rgb_moon_state["w"])
                else:
                    r_moon = g_moon = b_moon = w_moon = 0.0
            else:
                r_sun = g_sun = b_sun = w_sun = 0.0
                r_moon = g_moon = b_moon = w_moon = 0.0

            # Determine which source dominates BEFORE clouds (for CCT)
            if B_sun_raw_dev >= B_moon_raw_dev and sun_on:
                dominant_source = "sun"
            else:
                dominant_source = "moon"

            # Combined unclouded brightness fraction (Tuya)
            base_dev = max(B_sun_raw_dev, B_moon_raw_dev)
            base_frac = max(0.0, min(1.0, base_dev / brightness_dev_max))

            # Combined unclouded brightness fraction (RGBW)
            r_base = max(r_sun, r_moon)
            g_base = max(g_sun, g_moon)
            b_base = max(b_sun, b_moon)
            w_base = max(w_sun, w_moon)
            rgb_base_frac = max(0.0, min(1.0, r_base + g_base + b_base + w_base))

            # Apply clouds to combined sky brightness (shared factor)
            cloud_base = max(base_frac, rgb_base_frac)
            cloud_factor = clouds.get_multiplier(now_ts, cloud_base)

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

            r_final = max(0.0, min(1.0, r_base * cloud_factor))
            g_final = max(0.0, min(1.0, g_base * cloud_factor))
            b_final = max(0.0, min(1.0, b_base * cloud_factor))
            w_final = max(0.0, min(1.0, w_base * cloud_factor))

            r_pwm, g_pwm, b_pwm, w_pwm = rgb_curve._to_pwm(r_final, g_final, b_final, w_final)
            rgb_on = any(v > 0 for v in (r_pwm, g_pwm, b_pwm, w_pwm))

            if verbose:
                print(
                    f"[loop] t={t_hours:5.2f}h "
                    f"sun_raw={B_sun_raw_dev:4d} moon_raw={B_moon_raw_dev:4d} "
                    f"base_frac={base_frac:5.3f} rgb_base={rgb_base_frac:5.3f} "
                    f"cloud_factor={cloud_factor:5.3f} "
                    f"-> on={on_combined} B={B_combined:4d} CCT={C_dev:4d} "
                    f"RGBW={r_pwm:3d}/{g_pwm:3d}/{b_pwm:3d}/{w_pwm:3d} "
                    f"dom={dominant_source}"
                )

            if run_bridgelux and dev is not None:
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
            elif run_bridgelux and dev is None and verbose:
                print("[tuya] Skipping update (device not initialized)")

            if netlea_enabled and netlea_dev is not None:
                try:
                    if netlea_cfg.get("netlea_adaptive_fan", False):
                        f_pwm = max(r_pwm, g_pwm, b_pwm, w_pwm)
                    else:
                        f_pwm = int(netlea_cfg.get("netlea_f_channel", 0))
                    await netlea_dev.set_pwm(
                        r=r_pwm,
                        w=w_pwm,
                        g=g_pwm,
                        b=b_pwm,
                        f=f_pwm,
                        onoff=1 if rgb_on else 0,
                        fade_s=int(netlea_cfg.get("netlea_fade_seconds", 0)),
                        model_id=int(netlea_cfg.get("netlea_model_id", 0)),
                        number=int(netlea_cfg.get("netlea_number", 1)),
                        dev=int(netlea_cfg.get("netlea_device_id", 1)),
                        response=netlea_cfg.get("netlea_response", False),
                    )
                except Exception as e:
                    print("[netlea] Error talking to device:", e)
                    try:
                        await netlea_dev.disconnect()
                    except Exception:
                        pass
                    netlea_dev = None

            await asyncio.sleep(step_seconds)

    except Exception as e:
        print("[main] Unexpected exception:", e)
    finally:
        if run_bridgelux:
            turn_off_light(dev, bridgelux_cfg)
        await netlea_send_off(netlea_dev, netlea_cfg)
        if netlea_dev is not None:
            try:
                await netlea_dev.disconnect()
            except Exception as e:
                print("[netlea] Disconnect failed:", e)


if __name__ == "__main__":
    asyncio.run(main())
