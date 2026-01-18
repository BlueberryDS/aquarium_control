import time
import math
import random
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class DayTypeConfig:
    """Configuration for a type of day (bright / cloudy / very_cloudy)."""
    name: str
    prob: float

    # Cloud drop process
    center_drop: float      # mean of OU process (fraction, can be +/-)
    volatility: float       # random walk strength
    min_drop: float         # lower bound on raw drop (usually negative)
    max_drop: float         # upper bound on raw drop (can be > 0)
    cloud_speed: float      # scales cloud timescale ( >1 faster, <1 slower )

    # “Bright hole” bursts
    burst_prob_per_min: float   # expected bursts per minute
    burst_strength: float       # how hard bursts pull drop toward 0

    # Shimmer
    shimmer_boost: float        # scales shimmer amplitude for this day type


class CloudDeltaWithShimmer:
    """
    Stateful cloud + shimmer simulator.

    Use as a multiplicative factor on top of your base brightness curve:

        clouds = CloudDeltaWithShimmer()
        ...
        base = sun_curve.brightness(now_ts)        # 0..1
        factor = clouds.get_multiplier(now_ts, base)
        final = base * factor

    Model details:

      - Cloud drop (slow):
            drop < 0  -> dim by -drop (e.g. drop = -0.3 => 30% dimmer)
            drop >= 0 -> treated as 0 dim (clouds never brighten)
        Per day type, `drop` is an OU random walk with:
            center_drop, volatility, min_drop, max_drop, cloud_speed.
        This produces slow changes over ~1 hour timescales.

      - Shimmer (fast):
        OU around 0, mapped to a multiplier around 1.0:
            shimmer_multiplier = 1 + (shimmer_amp * shimmer_boost) * s
        where s is clamped to [-1,1], so shimmer is ±shimmer_amp
        (scaled by shimmer_boost) and *can* slightly brighten.

      - Final factor:
            cloud_multiplier = 1 - effective_drop
            factor = cloud_multiplier * shimmer_multiplier
            factor is clamped to [0, 1 + (shimmer_amp * shimmer_boost)].
    """

    def __init__(
        self,
        day_types: Optional[List[DayTypeConfig]] = None,
        cloud_time_scale_sec: float = 3600.0,   # 1 hour base timescale
        shimmer_time_scale_sec: float = 25.0,   # shimmer ~25 s
        shimmer_amp: float = 0.04,              # ±4% shimmer
        shimmer_volatility: float = 0.30,
        seed: Optional[int] = None,
        max_dt_sec: float = 1.0,
    ):
        """
        cloud_time_scale_sec:
            Base OU timescale for clouds. Per-day `cloud_speed` scales this.
        shimmer_time_scale_sec:
            OU timescale for shimmer process (seconds).
        shimmer_amp:
            Base shimmer amplitude (fraction). Actual amplitude per day is
            shimmer_amp * day.shimmer_boost.
        shimmer_volatility:
            OU noise level for shimmer.
        max_dt_sec:
            Clamp on dt between updates so a long pause doesn't cause jumps.
        """

        # Default tuned day types
        if day_types is None:
            day_types = [
                DayTypeConfig(
                    name="bright",
                    prob=0.65,
                    center_drop=0.04,      # biased above 0 => almost no dimming
                    volatility=0.010,
                    min_drop=-0.05,        # at worst ~5% dim
                    max_drop=0.12,
                    cloud_speed=0.5,       # ~2h mean reversion
                    burst_prob_per_min=0.0,
                    burst_strength=0.0,
                    shimmer_boost=1.0,
                ),
                DayTypeConfig(
                    name="cloudy",
                    prob=0.25,
                    center_drop=0.06,      # even more biased above 0 => mostly bright
                    volatility=0.020,
                    min_drop=-0.25,        # up to 25% dim
                    max_drop=0.18,
                    cloud_speed=0.8,       # ~75min mean reversion
                    burst_prob_per_min=0.015,  # ~1 bright break per ~65min
                    burst_strength=0.5,
                    shimmer_boost=1.0,
                ),
                DayTypeConfig(
                    name="very_cloudy",
                    prob=0.10,
                    center_drop=-0.35,     # ~30–35% dim on average
                    volatility=0.030,
                    min_drop=-0.60,        # darkest ~60% dim
                    max_drop=0.15,         # can reach 0 dimming -> real bright holes
                    cloud_speed=1.2,       # ~50min mean reversion
                    burst_prob_per_min=0.08,   # a few bursts per hour
                    burst_strength=0.6,
                    shimmer_boost=1.0,
                ),
            ]

        # Normalize probabilities
        total_p = sum(dt.prob for dt in day_types)
        for dt in day_types:
            dt.prob = dt.prob / total_p

        self.day_types = day_types

        self.cloud_time_scale_sec = cloud_time_scale_sec
        self.shimmer_time_scale_sec = shimmer_time_scale_sec
        self.shimmer_amp = shimmer_amp
        self.shimmer_volatility = shimmer_volatility
        self.max_dt_sec = max_dt_sec

        self.rng = random.Random(seed)

        # Day-level state
        self.current_day_key = None
        self.current_day_type: Optional[DayTypeConfig] = None

        # Cloud drop state (fraction, negative = dimming)
        self.drop = 0.0

        # Shimmer state: OU around 0
        self._shimmer_state = 0.0

        self.last_ts: Optional[float] = None

    # ---------- internals ----------

    def _day_key_from_ts(self, ts: float):
        t = time.localtime(ts)
        return (t.tm_year, t.tm_mon, t.tm_mday)

    def _pick_day_type(self) -> DayTypeConfig:
        r = self.rng.random()
        acc = 0.0
        for dt in self.day_types:
            acc += dt.prob
            if r <= acc:
                return dt
        return self.day_types[-1]

    def _ensure_day_state(self, now_ts: float):
        """Ensure we have the right day type for the current calendar day."""
        key = self._day_key_from_ts(now_ts)
        if key != self.current_day_key:
            self.current_day_key = key
            self.current_day_type = self._pick_day_type()
            # Start cloud drop at the day's center, shimmer near 0
            self.drop = self.current_day_type.center_drop
            self._shimmer_state = 0.0

    def _step_cloud_drop(self, dt: float):
        if self.current_day_type is None:
            return

        dt = max(0.0, min(dt, self.max_dt_sec))
        if dt == 0.0:
            return

        cfg = self.current_day_type

        # OU process around center_drop
        theta = (1.0 / self.cloud_time_scale_sec) * cfg.cloud_speed
        mu = cfg.center_drop
        sigma = cfg.volatility

        n = self.rng.gauss(0.0, 1.0)
        self.drop += theta * (mu - self.drop) * dt + sigma * math.sqrt(dt) * n

        # Clamp to configured range
        self.drop = max(cfg.min_drop, min(cfg.max_drop, self.drop))

        # Occasional "bright hole" bursts: pull drop toward 0
        if cfg.burst_prob_per_min > 0.0:
            lam = cfg.burst_prob_per_min / 60.0  # per second
            p = 1.0 - math.exp(-lam * dt)
            if self.rng.random() < p:
                self.drop *= (1.0 - cfg.burst_strength)
                self.drop = max(cfg.min_drop, min(cfg.max_drop, self.drop))

    def _step_shimmer(self, dt: float):
        """OU process around 0 for shimmer_state, then mapped to multiplier."""
        dt = max(0.0, min(dt, self.max_dt_sec))
        if dt == 0.0:
            return

        theta = 1.0 / self.shimmer_time_scale_sec
        mu = 0.0
        sigma = self.shimmer_volatility

        n = self.rng.gauss(0.0, 1.0)
        self._shimmer_state += (
            theta * (mu - self._shimmer_state) * dt
            + sigma * math.sqrt(dt) * n
        )

        # Keep shimmer bounded
        self._shimmer_state = max(-1.0, min(1.0, self._shimmer_state))

    # ---------- public API ----------

    def get_multiplier(self, now_ts: float, base_brightness: float) -> float:
        """
        now_ts:
            Current time (seconds since epoch).
        base_brightness:
            Underlying brightness curve output (0..1).

        Returns:
            factor (>=0) such that:
                final_brightness = base_brightness * factor

        If base_brightness <= 0, factor is 1.0 (no effect).
        """
        self._ensure_day_state(now_ts)

        if self.last_ts is None:
            self.last_ts = now_ts

        dt = now_ts - self.last_ts
        self.last_ts = now_ts

        # If base is off or we don't have a day type yet, do nothing
        if base_brightness <= 0.0 or self.current_day_type is None:
            return 1.0

        # Step processes
        self._step_cloud_drop(dt)
        self._step_shimmer(dt)

        cfg = self.current_day_type

        # Clouds: only dim
        # raw drop can be negative or positive; positive means "no dimming"
        effective_drop = max(0.0, -self.drop)  # 0..1
        cloud_multiplier = 1.0 - effective_drop

        # Shimmer: can brighten or dim slightly
        local_amp = self.shimmer_amp * cfg.shimmer_boost
        shimmer_multiplier = 1.0 + local_amp * self._shimmer_state

        # Combine
        factor = cloud_multiplier * shimmer_multiplier

        # Clamp to [0, 1 + local_amp]
        max_factor = 1.0 + local_amp
        factor = max(0.0, min(max_factor, factor))

        return factor
