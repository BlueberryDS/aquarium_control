from datetime import datetime

from mooncurve import MoonCurve
from rgbw_tuning import clamp, clamp01, linear_to_pwm, map_rgbw_linear


class MoonCurveRGB(MoonCurve):
    def __init__(
        self,
        max_brightness: float = 0.05,
        dark_start_hour: float = 2.0,
        dark_end_hour: float = 7.0,
        warmth: float = 0.0,
        saturation: float = 0.12,
        tint: float = 0.0,
        saturation_min: float = 0.05,
        saturation_max: float = 0.60,
        tint_min: float = -0.40,
        tint_max: float = 0.25,
        preserve_total: bool = True,
        pwm_max: int = 1000,
        channel_luts: dict[str, list[float]] | None = None,
        channel_gammas: dict[str, float] | None = None,
    ):
        super().__init__(
            max_brightness=max_brightness,
            dark_start_hour=dark_start_hour,
            dark_end_hour=dark_end_hour,
        )
        self.warmth = float(warmth)
        self.saturation = float(saturation)
        self.tint = float(tint)
        self.saturation_min = float(saturation_min)
        self.saturation_max = float(saturation_max)
        self.tint_min = float(tint_min)
        self.tint_max = float(tint_max)
        self.preserve_total = bool(preserve_total)
        self.pwm_max = int(pwm_max)
        self.channel_luts = channel_luts
        self.channel_gammas = channel_gammas

    def _resolve_knobs(
        self,
        warmth: float | None,
        saturation: float | None,
        tint: float | None,
    ) -> tuple[float, float, float]:
        w = self.warmth if warmth is None else float(warmth)
        s = self.saturation if saturation is None else float(saturation)
        t = self.tint if tint is None else float(tint)

        w = clamp01(w)
        s = clamp(s, self.saturation_min, self.saturation_max)
        t = clamp(t, self.tint_min, self.tint_max)
        return w, s, t

    def _to_pwm(
        self,
        r: float,
        g: float,
        b: float,
        w: float,
        pwm_max: int | None = None,
        channel_luts: dict[str, list[float]] | None = None,
        channel_gammas: dict[str, float] | None = None,
    ) -> tuple[int, int, int, int]:
        pwm_max = self.pwm_max if pwm_max is None else int(pwm_max)
        luts = self.channel_luts if channel_luts is None else channel_luts
        gammas = self.channel_gammas if channel_gammas is None else channel_gammas

        r_lut = luts.get("r") if luts else None
        g_lut = luts.get("g") if luts else None
        b_lut = luts.get("b") if luts else None
        w_lut = luts.get("w") if luts else None

        r_gamma = gammas.get("r") if gammas else None
        g_gamma = gammas.get("g") if gammas else None
        b_gamma = gammas.get("b") if gammas else None
        w_gamma = gammas.get("w") if gammas else None

        return (
            linear_to_pwm(r, lut=r_lut, gamma=r_gamma, pwm_max=pwm_max),
            linear_to_pwm(g, lut=g_lut, gamma=g_gamma, pwm_max=pwm_max),
            linear_to_pwm(b, lut=b_lut, gamma=b_gamma, pwm_max=pwm_max),
            linear_to_pwm(w, lut=w_lut, gamma=w_gamma, pwm_max=pwm_max),
        )

    def get_state(
        self,
        now: datetime | None = None,
        raw: bool = False,
        warmth: float | None = None,
        saturation: float | None = None,
        tint: float | None = None,
        pwm_max: int | None = None,
        channel_luts: dict[str, list[float]] | None = None,
        channel_gammas: dict[str, float] | None = None,
    ) -> dict:
        state = super().get_state(now)

        if not state["on"] or state["brightness"] <= 0.0:
            if raw:
                state.update({"r": 0.0, "g": 0.0, "b": 0.0, "w": 0.0})
            else:
                state.update({"r": 0, "g": 0, "b": 0, "w": 0})
            return state

        w, s, t = self._resolve_knobs(warmth, saturation, tint)
        r, g, b, w_ch = map_rgbw_linear(
            I=state["brightness"],
            w=w,
            s=s,
            t=t,
            preserve_total=self.preserve_total,
        )

        if raw:
            state.update({"r": r, "g": g, "b": b, "w": w_ch})
            return state

        r_i, g_i, b_i, w_i = self._to_pwm(
            r,
            g,
            b,
            w_ch,
            pwm_max=pwm_max,
            channel_luts=channel_luts,
            channel_gammas=channel_gammas,
        )
        state.update({"r": r_i, "g": g_i, "b": b_i, "w": w_i})
        return state
