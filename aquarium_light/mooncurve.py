import math
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class MoonPhaseInfo:
    phase_fraction: float   # 0.0=new, 0.5=full, 1.0=new again
    illumination: float     # 0.0..1.0 (fraction of disc illuminated)
    age_days: float         # days since last new moon
    synodic_days: float     # length of synodic month (~29.53 days)


class MoonCurve:
    """
    Simple lunar phase model:

      - Uses a mean synodic month and a fixed reference new moon.
      - Only turns on during the ~top 10 brightest days of the cycle.
      - Has a *dark window* every day (local time) where moonlight is forced off.

    There is NO explicit “night” start/end; only:
      - phase gate (bright enough?)
      - dark gate (inside dark window? then off)

    Defaults: dark from 02:00–07:00 local time.

    Example:

        moon = MoonCurve(
            max_brightness=0.05,   # 5% cap
            # dark_start_hour=2.0, # these are already the defaults
            # dark_end_hour=7.0,
        )

        state = moon.get_state()
        if state["on"]:
            brightness = state["brightness"]  # 0..0.05
    """

    # Mean synodic month length (days)
    SYNODIC_MONTH_DAYS = 29.530588853

    # Reference new moon (approx): 2000-01-06 18:14 UT, JD ~2451550.1
    REF_NEW_MOON_JD = 2451550.1

    # Illumination threshold so ~10 brightest days have light ON
    ILLUM_THRESHOLD = 0.72

    def __init__(
        self,
        max_brightness: float = 0.05,
        dark_start_hour: float = 2.0,
        dark_end_hour: float = 7.0,
    ):
        """
        :param max_brightness: Max output brightness (0..1), e.g. 0.05 = 5%.
        :param dark_start_hour: Local hour when pure-dark window starts (0..24).
        :param dark_end_hour: Local hour when pure-dark window ends (0..24).
        """
        self.max_brightness = float(max_brightness)
        self.dark_start_hour = float(dark_start_hour)
        self.dark_end_hour = float(dark_end_hour)

    # ---------- Public API ----------

    def get_state(self, now: datetime | None = None) -> dict:
        """
        Returns:
            {
              "on": bool,             # True => actually turn the light ON
              "brightness": float,    # 0..max_brightness
              "phase_fraction": float,
              "illumination": float,
              "age_days": float,
              "synodic_days": float,
              "local_hour": float,    # for logging / debugging
            }
        """
        # Local time for dark-window check
        if now is None:
            now_local = datetime.now().astimezone()
        else:
            if now.tzinfo is None:
                now_local = now.astimezone()
            else:
                now_local = now.astimezone()

        # UTC for lunar phase
        now_utc = now_local.astimezone(timezone.utc)

        local_hour = (
            now_local.hour
            + now_local.minute / 60.0
            + now_local.second / 3600.0
        )

        # Compute phase, always
        phase_info = self.phase_info(now_utc)
        illum = phase_info.illumination

        # ---- Dark window gate (pure darkness) ----
        if self._in_window(local_hour, self.dark_start_hour, self.dark_end_hour):
            return self._off_state(phase_info, local_hour)

        # ---- Lunar-phase gate (only on bright ~10 days) ----
        if illum < self.ILLUM_THRESHOLD:
            return self._off_state(phase_info, local_hour)

        # ---- Map illumination -> brightness ----
        rel = (illum - self.ILLUM_THRESHOLD) / (1.0 - self.ILLUM_THRESHOLD)
        rel = max(0.0, min(1.0, rel))

        brightness = self.max_brightness * rel

        return {
            "on": True,
            "brightness": brightness,
            "phase_fraction": phase_info.phase_fraction,
            "illumination": phase_info.illumination,
            "age_days": phase_info.age_days,
            "synodic_days": phase_info.synodic_days,
            "local_hour": local_hour,
        }

    def phase_info(self, t_utc: datetime) -> MoonPhaseInfo:
        """
        Simple lunar "clock" using mean synodic month + reference new moon.
        """
        if t_utc.tzinfo is None:
            t_utc = t_utc.replace(tzinfo=timezone.utc)
        else:
            t_utc = t_utc.astimezone(timezone.utc)

        jd = self._datetime_to_julian_day(t_utc)

        # Days since reference new moon
        days_since = jd - self.REF_NEW_MOON_JD

        # Phase fraction in [0, 1)
        phase = (days_since / self.SYNODIC_MONTH_DAYS) % 1.0

        # Illumination fraction using simple phase-angle model:
        #   illum = 0.5 * (1 - cos(2π * phase))
        phase_angle = 2.0 * math.pi * phase
        illumination = 0.5 * (1.0 - math.cos(phase_angle))

        age_days = phase * self.SYNODIC_MONTH_DAYS

        return MoonPhaseInfo(
            phase_fraction=phase,
            illumination=illumination,
            age_days=age_days,
            synodic_days=self.SYNODIC_MONTH_DAYS,
        )

    # ---------- Internals ----------

    @staticmethod
    def _datetime_to_julian_day(dt: datetime) -> float:
        """
        Convert UTC datetime -> Julian Day.
        """
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)

        year = dt.year
        month = dt.month
        day = dt.day + (dt.hour + (dt.minute + dt.second / 60.0) / 60.0) / 24.0

        if month <= 2:
            year -= 1
            month += 12

        A = year // 100
        B = 2 - A + (A // 4)

        jd = (
            math.floor(365.25 * (year + 4716))
            + math.floor(30.6001 * (month + 1))
            + day + B - 1524.5
        )
        return jd

    @staticmethod
    def _in_window(hour: float, start: float, end: float) -> bool:
        """
        True if 'hour' lies in [start, end), respecting wrap at midnight.

        Examples:
          start=2, end=7    => 02:00..07:00
          start=22, end=2   => 22:00..24:00 and 00:00..02:00
        """
        start = start % 24.0
        end = end % 24.0
        hour = hour % 24.0

        if start < end:
            return start <= hour < end
        else:
            # Wrap around midnight
            return hour >= start or hour < end

    def _off_state(self, phase_info: MoonPhaseInfo, local_hour: float) -> dict:
        return {
            "on": False,
            "brightness": 0.0,
            "phase_fraction": phase_info.phase_fraction,
            "illumination": phase_info.illumination,
            "age_days": phase_info.age_days,
            "synodic_days": phase_info.synodic_days,
            "local_hour": local_hour,
        }
