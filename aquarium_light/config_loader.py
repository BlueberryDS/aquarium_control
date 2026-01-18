#!/usr/bin/env python3
import json
from datetime import datetime, date
from typing import Any, Dict, List, Tuple


def _is_number(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _interp_values(a: Any, b: Any, alpha: float) -> Any:
    """
    Interpolate between two values:

    - numbers: linear interpolation
    - dicts: recurse
    - everything else: pick a or b depending on alpha (before/after midpoint)
    """
    if _is_number(a) and _is_number(b):
        return a + (b - a) * alpha

    if isinstance(a, dict) and isinstance(b, dict):
        return _interp_dict(a, b, alpha)

    # Non-interpolable (str, lists, etc.): choose nearer endpoint
    return a if alpha < 0.5 else b


def _interp_dict(a: Dict[str, Any], b: Dict[str, Any], alpha: float) -> Dict[str, Any]:
    """
    Interpolate between two nested dict configs.
    Numeric fields get linear interpolation; everything else falls back to
    a or b depending on alpha, or whichever side has the key.
    """
    result: Dict[str, Any] = {}
    keys = set(a.keys()) | set(b.keys())

    for key in keys:
        if key in a and key in b:
            result[key] = _interp_values(a[key], b[key], alpha)
        elif key in a:
            result[key] = a[key]
        else:
            result[key] = b[key]

    return result


def _merge_dict_shallow_inherit(old: Dict[str, Any], new: Dict[str, Any]) -> Dict[str, Any]:
    """
    Inherit structure from 'old' and override with 'new'.
    If both sides have a dict for a key, recurse; otherwise 'new' wins.
    """
    result: Dict[str, Any] = dict(old)
    for k, v in new.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _merge_dict_shallow_inherit(result[k], v)
        else:
            result[k] = v
    return result


def load_runtime_config(
    path: str,
    now: datetime | None = None,
) -> tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    """
    Load the JSON config and return interpolated (sun, moon, tuya, global) dicts
    for the given datetime (default: today).

    JSON format:

    {
      "constants": {
        "tuya":   { ... non-versioned hardware / protocol constants ... },
        "global": { ... non-versioned daemon behavior constants ... }
      },
      "versions": [
        {
          "date": "YYYY-MM-DD",
          "sun":  { ... versioned sunlight curve params ... },
          "moon": { ... versioned moonlight curve params ... }
        },
        ...
      ]
    }

    Behavior:

    - Versions are sorted by date.
    - Each version INHERITS from prior ones (nested dict merge).
      So later entries only need to specify fields that changed.
    - Between two dates, we linearly interpolate numeric fields.
    - Before the first date: clamp to the earliest version.
    - After the last date: clamp to the latest version.
    """
    if now is None:
        now_date = date.today()
    else:
        now_date = now.date()

    with open(path, "r") as f:
        data = json.load(f)

    constants = data.get("constants", {})
    versions = data.get("versions", [])

    if not versions:
        raise ValueError(f"No 'versions' entries found in {path}")

    # 1) Parse raw versions (partial configs)
    raw_versions: List[Tuple[date, Dict[str, Any]]] = []
    for entry in versions:
        if "date" not in entry:
            raise ValueError("Each version entry must have a 'date' field (YYYY-MM-DD).")
        entry_date = date.fromisoformat(entry["date"])
        cfg_partial = {k: v for k, v in entry.items() if k != "date"}
        raw_versions.append((entry_date, cfg_partial))

    raw_versions.sort(key=lambda x: x[0])

    # 2) Build cumulative (inherited) snapshots
    inherited_versions: List[Tuple[date, Dict[str, Any]]] = []
    running_cfg: Dict[str, Any] = {}

    for d, partial_cfg in raw_versions:
        running_cfg = _merge_dict_shallow_inherit(running_cfg, partial_cfg)
        # store a copy so later updates don't mutate previous snapshots
        inherited_versions.append((d, json.loads(json.dumps(running_cfg))))

    # 3) Find appropriate config for 'now_date', with interpolation if between two dates

    if now_date <= inherited_versions[0][0]:
        cfg = inherited_versions[0][1]
    elif now_date >= inherited_versions[-1][0]:
        cfg = inherited_versions[-1][1]
    else:
        cfg = inherited_versions[-1][1]  # fallback
        for (d0, c0), (d1, c1) in zip(inherited_versions, inherited_versions[1:]):
            if d0 <= now_date <= d1:
                if d0 == d1:
                    cfg = c0
                else:
                    total_days = (d1 - d0).days
                    if total_days <= 0:
                        alpha = 0.0
                    else:
                        alpha = (now_date - d0).days / total_days
                    cfg = _interp_dict(c0, c1, alpha)
                break

    # Extract versioned parts (may be interpolated)
    sun_cfg = cfg.get("sun", {})
    moon_cfg = cfg.get("moon", {})

    # Extract constants (non-versioned)
    tuya_cfg = constants.get("tuya", {})
    global_cfg = constants.get("global", {})

    return sun_cfg, moon_cfg, tuya_cfg, global_cfg
