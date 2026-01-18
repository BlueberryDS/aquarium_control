import math

class SunCurve:
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
    ):
        self.t_start = float(t_start) % 24.0
        self.t_end = float(t_end) % 24.0
        self.H_eq = float(H_eq)
        self.B_peak_max = float(B_peak_max)
        self.tau_minutes = float(tau_minutes)
        self.delta_T = float(delta_T)
        self.T_min = float(T_min)
        self.T_max = float(T_max)
        self.T_blue = float(T_blue)

        D = (self.t_end - self.t_start) % 24.0
        if D == 0:
            D = 24.0
        self.D = D

        self.a_unclipped = 2.0 * self.H_eq / self.D
        self.B_peak_eff = min(self.a_unclipped, self.B_peak_max)
        self.tau = max(0.0, min(self.tau_minutes / 60.0, self.D / 4.0))

        if self.a_unclipped < self.B_peak_max:
            self.warning = (
                f"[SunCurve] Peak cap {self.B_peak_max:.3f} is not reached; "
                f"actual peak will be {self.a_unclipped:.3f}."
            )
        elif self.a_unclipped > self.B_peak_max:
            self.warning = (
                f"[SunCurve] Brightness will be clipped at {self.B_peak_max:.3f}; "
                f"requested H_eq={self.H_eq} will be reduced slightly."
            )
        else:
            self.warning = None

    def _local_offset_and_phase(self, t_hours: float):
        t_mod = float(t_hours) % 24.0
        offset = (t_mod - self.t_start) % 24.0
        inside = offset <= self.D
        if not inside:
            return offset, 0.0, False
        u = offset / self.D
        return offset, u, True

    def _shape(self, u: float) -> float:
        return 0.5 * (1.0 - math.cos(2.0 * math.pi * u))

    def _brightness_float(self, t_hours: float) -> float:
        offset, u, inside = self._local_offset_and_phase(t_hours)
        if not inside:
            return 0.0
        s = self._shape(u)
        B_raw = self.a_unclipped * s
        if self.B_peak_max < 1.0:
            B = max(0.0, min(B_raw, self.B_peak_max))
        else:
            B = max(0.0, B_raw)
        return B

    def _cct_base_from_B(self, B: float) -> float:
        if self.B_peak_eff <= 0.0 or B <= 0.0:
            return 0.0
        b = max(0.0, min(B / self.B_peak_eff, 1.0))

        if b <= 0.0:
            return 0.0

        if b <= 0.10:
            return 6000.0 + (6500.0 - 6000.0) * (b / 0.10)

        if b <= 0.25:
            x = (b - 0.10) / (0.25 - 0.10)
            return 6500.0 + (3000.0 - 6500.0) * x

        if b <= 0.85:
            x = (b - 0.25) / (0.85 - 0.25)
            return 3000.0 + (6000.0 - 3000.0) * x

        x = (b - 0.85) / (1.0 - 0.85)
        return 6000.0 + (6500.0 - 6000.0) * x

    def _cct_float(self, t_hours: float, B: float) -> float:
        if B <= 0.0:
            return 0.0

        offset, u, inside = self._local_offset_and_phase(t_hours)
        if not inside:
            return 0.0

        T = self._cct_base_from_B(B)
        if T <= 0.0:
            return 0.0

        if self.delta_T != 0.0:
            bias = self.delta_T * (0.5 - u)
            T += bias

        T = max(self.T_min, min(T, self.T_max))

        if self.tau > 0.0:
            if 0.0 <= offset <= self.tau:
                alpha = offset / self.tau
                T = (1.0 - alpha) * self.T_blue + alpha * T
            elif (self.D - self.tau) <= offset <= self.D:
                beta = (self.D - offset) / self.tau
                T = (1.0 - beta) * self.T_blue + beta * T

        return T

    def _cct_to_0_1000(self, T: float) -> int:
        if T <= 0.0:
            return 0
        frac = (T - self.T_min) / (self.T_max - self.T_min)
        frac = max(0.0, min(frac, 1.0))
        return int(round(1000.0 * frac))

    def sample(self, t_hours: float, raw: bool = False):
        B = self._brightness_float(t_hours)
        offset, u, inside = self._local_offset_and_phase(t_hours)
        is_on = inside and (B > 0.0)

        if not is_on:
            if raw:
                return 0.0, 0.0, False
            return 0, 0, False

        T = self._cct_float(t_hours, B)

        if raw:
            return B, T, True

        B_i = int(round(1000.0 * max(0.0, min(B, 1.0))))
        C_i = self._cct_to_0_1000(T)
        return B_i, C_i, True
