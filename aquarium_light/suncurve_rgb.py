from suncurve import SunCurve
from rgbw_tuning import clamp, clamp01, linear_to_pwm, map_rgbw_linear


class SunCurveRGB(SunCurve):
    def __init__(
        self,
        t_start: float,
        t_end: float,
        H_eq: float,
        B_peak_max: float,
        tau_minutes: float = 5.0,
        delta_T: float = 800.0,
        T_min: float = 2700.0,
        T_max: float = 6700.0,
        T_blue: float = 6500.0,
        saturation: float = 0.28,
        tint: float = -0.15,
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
            t_start=t_start,
            t_end=t_end,
            H_eq=H_eq,
            B_peak_max=B_peak_max,
            tau_minutes=tau_minutes,
            delta_T=delta_T,
            T_min=T_min,
            T_max=T_max,
            T_blue=T_blue,
        )
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

    def _warmth_progress(self, T: float) -> float:
        if T <= 0.0:
            return 0.0
        denom = self.T_max - self.T_min
        if denom <= 0.0:
            return 0.0
        w = (self.T_max - T) / denom
        return clamp01(w)

    def _resolve_knobs(
        self,
        saturation: float | None,
        tint: float | None,
    ) -> tuple[float, float]:
        s = self.saturation if saturation is None else float(saturation)
        t = self.tint if tint is None else float(tint)
        s = clamp(s, self.saturation_min, self.saturation_max)
        t = clamp(t, self.tint_min, self.tint_max)
        return s, t

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

    def sample(
        self,
        t_hours: float,
        raw: bool = False,
        saturation: float | None = None,
        tint: float | None = None,
        pwm_max: int | None = None,
        channel_luts: dict[str, list[float]] | None = None,
        channel_gammas: dict[str, float] | None = None,
    ):
        B = self._brightness_float(t_hours)
        _offset, _u, inside = self._local_offset_and_phase(t_hours)
        is_on = inside and (B > 0.0)

        if not is_on:
            if raw:
                return 0.0, 0.0, 0.0, 0.0, False
            return 0, 0, 0, 0, False

        T = self._cct_float(t_hours, B)
        w = self._warmth_progress(T)
        s, t = self._resolve_knobs(saturation, tint)

        r, g, b, w_ch = map_rgbw_linear(
            I=B,
            w=w,
            s=s,
            t=t,
            preserve_total=self.preserve_total,
        )

        if raw:
            return r, g, b, w_ch, True

        return (*self._to_pwm(
            r,
            g,
            b,
            w_ch,
            pwm_max=pwm_max,
            channel_luts=channel_luts,
            channel_gammas=channel_gammas,
        ), True)
