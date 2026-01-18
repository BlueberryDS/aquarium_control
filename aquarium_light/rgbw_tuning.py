import math


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def clamp01(x: float) -> float:
    return clamp(x, 0.0, 1.0)


def map_rgbw_linear(
    I: float,
    w: float,
    s: float,
    t: float,
    preserve_total: bool = True,
) -> tuple[float, float, float, float]:
    I = clamp01(I)
    w = clamp01(w)
    s = clamp(s, 0.0, 1.0)
    t = clamp(t, -1.0, 1.0)

    s_eff = s * (0.25 + 0.75 * w)
    s_eff = clamp01(s_eff)

    W = I * (1.0 - s_eff)

    R = I * s_eff * (0.70 + 0.25 * w)
    G = I * s_eff * (0.28 - 0.18 * w)
    B = I * s_eff * (0.12 * (1.0 - w))

    adj = 0.12 * abs(t) * I * s_eff
    if t > 0.0:
        G += adj
        R -= 0.7 * adj
        B -= 0.3 * adj
    elif t < 0.0:
        G -= adj
        R += 0.9 * adj
        B += 0.1 * adj * (1.0 - w)

    R = clamp01(R)
    G = clamp01(G)
    B = clamp01(B)
    W = clamp01(W)

    if preserve_total:
        S = R + G + B + W
        if S > 1e-6:
            scale = I / S
            R = clamp01(R * scale)
            G = clamp01(G * scale)
            B = clamp01(B * scale)
            W = clamp01(W * scale)

    return R, G, B, W


def _lut_interpolate(x: float, lut: list[float]) -> float:
    if not lut:
        return x
    if len(lut) == 1:
        return float(lut[0])
    pos = clamp01(x) * (len(lut) - 1)
    i = int(math.floor(pos))
    frac = pos - i
    j = min(i + 1, len(lut) - 1)
    return float(lut[i]) + (float(lut[j]) - float(lut[i])) * frac


def linear_to_pwm(
    x: float,
    *,
    lut: list[float] | None = None,
    gamma: float | None = None,
    pwm_max: int = 1000,
) -> int:
    x = clamp01(x)
    if lut is not None:
        y = _lut_interpolate(x, lut)
        if max(lut) <= 1.0:
            y *= pwm_max
        return int(round(clamp(y, 0.0, float(pwm_max))))

    if gamma is None:
        gamma = 1.0
    y = clamp01(x ** gamma)
    return int(round(y * pwm_max))
