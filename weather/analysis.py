"""
Weather analysis for a vehicle-mounted weather station.

The central problem: a moving vehicle sees pressure changes from elevation
(~12 hPa per 100 m) that dwarf weather changes (a strong storm is ~1-2 hPa
per hour). So before any trend analysis the station pressure is turned
into a weather-only pressure series:

  1. Outliers are removed with a Hampel filter.
  2. The series is "stitched": changes while the vehicle is moving, or
     faster than weather can plausibly move (> 0.35 hPa/min), are treated
     as elevation and replaced by the prevailing weather trend. Across
     power-off gaps the change is kept only if it is plausible for weather
     at the same spot (<= 3 hPa/h); otherwise the vehicle was moved and the
     jump is treated as elevation.
  3. Trends use Theil-Sen regression (median of pairwise slopes), which
     ignores remaining noise and spikes.

On top of that:
  - WMO pressure tendency (amount over 3 h) and characteristic (shape)
  - sea-level pressure (hypsometric formula, or a self-calibrating
    estimate when the elevation is unknown)
  - Zambretti forecaster
  - storm probability model, thunderstorm gust-front detection, fog and
    icing risk

All functions are pure: they take minute samples and return plain data,
so they can be tested with synthetic weather.
"""

import math
import statistics
import time
from dataclasses import dataclass, field


# -------------------------------------------------------------
# Tunables
# -------------------------------------------------------------

# Faster than this between consecutive minutes = elevation change, not
# weather. Thunderstorm gust fronts reach ~0.2-0.3 hPa/min.
MAX_WEATHER_RATE_HPA_PER_MIN = 0.35

# Across a power-off gap, keep the change only if weather could explain it
MAX_WEATHER_RATE_HPA_PER_H = 3.0

# Minutes without samples that count as a gap (Pi was off / sensor out)
GAP_MIN = 10

# Stop extrapolating the weather trend after this long without real data
MAX_EXTRAPOLATE_MIN = 180

# Weather-trend estimate used for extrapolation (EMA time constant)
TREND_EMA_ALPHA = 0.02

STANDARD_PRESSURE = 1013.25


@dataclass
class Sample:
    """One minute of averaged readings."""
    ts: float                 # unix time (s)
    pressure: float = None    # station pressure, hPa
    temp_c: float = None      # outside temperature
    humidity: float = None    # outside relative humidity, %
    lux: float = None
    moving: bool = None       # None = unknown (no IMU)


@dataclass
class WeatherPoint:
    ts: float
    pressure: float           # weather-only pressure (hPa)
    estimated: bool           # True where elevation changes were bridged


# =============================================================
# Basic physics
# =============================================================

def dew_point_c(temp_c, humidity):
    """Magnus formula, Alduchov & Eskridge constants (±0.35 °C, -45..60 °C)."""
    if temp_c is None or humidity is None or humidity <= 0:
        return None
    a, b = 17.625, 243.04
    gamma = math.log(humidity / 100.0) + a * temp_c / (b + temp_c)
    return b * gamma / (a - gamma)


def c_to_f(c):
    return None if c is None else c * 9 / 5 + 32


def heat_index_f(temp_f, humidity):
    """Full NWS heat index algorithm, including its adjustments."""
    if temp_f is None or humidity is None:
        return None

    simple = 0.5 * (temp_f + 61.0 + (temp_f - 68.0) * 1.2 + humidity * 0.094)
    if (simple + temp_f) / 2 < 80:
        return simple

    t, rh = temp_f, humidity
    hi = (-42.379 + 2.04901523 * t + 10.14333127 * rh - 0.22475541 * t * rh
          - 0.00683783 * t * t - 0.05481717 * rh * rh + 0.00122874 * t * t * rh
          + 0.00085282 * t * rh * rh - 0.00000199 * t * t * rh * rh)

    if rh < 13 and 80 <= t <= 112:
        hi -= ((13 - rh) / 4) * math.sqrt((17 - abs(t - 95)) / 17)
    elif rh > 85 and 80 <= t <= 87:
        hi += ((rh - 85) / 10) * ((87 - t) / 5)

    return hi


def sea_level_pressure(station_hpa, elevation_m, temp_c):
    """Hypsometric reduction to sea level (what weather maps use)."""
    if station_hpa is None or elevation_m is None:
        return None
    t = 15.0 if temp_c is None else temp_c
    return station_hpa * (1 - (0.0065 * elevation_m) / (t + 0.0065 * elevation_m + 273.15)) ** -5.257


# =============================================================
# Robust statistics
# =============================================================

def hampel(values, window=7, k=3.0):
    """
    Replace outliers with the local median. A point is an outlier when it
    is more than k scaled-MADs from the median of its neighbourhood.
    """
    n = len(values)
    half = window // 2
    out = list(values)

    for i in range(n):
        lo, hi = max(0, i - half), min(n, i + half + 1)
        neighbourhood = values[lo:hi]
        if len(neighbourhood) < 3:
            continue
        med = statistics.median(neighbourhood)
        mad = statistics.median(abs(v - med) for v in neighbourhood) * 1.4826
        if mad > 0 and abs(values[i] - med) > k * mad:
            out[i] = med

    return out


def theil_sen(points, max_points=240):
    """
    Robust slope (y per x) and intercept: median of all pairwise slopes.
    Long series are thinned evenly to keep it O(n^2) cheap.
    """
    if len(points) < 2:
        return None, None

    if len(points) > max_points:
        step = len(points) / max_points
        points = [points[int(i * step)] for i in range(max_points)]

    slopes = []
    for i in range(len(points)):
        xi, yi = points[i]
        for j in range(i + 1, len(points)):
            xj, yj = points[j]
            if xj != xi:
                slopes.append((yj - yi) / (xj - xi))

    if not slopes:
        return None, None

    slope = statistics.median(slopes)
    intercept = statistics.median(y - slope * x for x, y in points)
    return slope, intercept


# =============================================================
# Weather-only pressure
# =============================================================

def weather_pressure_series(samples):
    """
    Turn station pressure into a weather-only series (see module doc).
    samples: Sample list sorted by time.
    """
    raw = [s for s in samples if s.pressure is not None]
    if not raw:
        return []

    cleaned = hampel([s.pressure for s in raw])

    series = []
    trend = 0.0            # weather trend estimate, hPa/min
    extrapolated_min = 0.0
    w = cleaned[0]
    series.append(WeatherPoint(raw[0].ts, w, False))

    for i in range(1, len(raw)):
        prev, cur = raw[i - 1], raw[i]
        dt = (cur.ts - prev.ts) / 60.0
        if dt <= 0:
            continue

        dp = cleaned[i] - cleaned[i - 1]
        moving = bool(cur.moving) or bool(prev.moving)

        if dt > GAP_MIN:
            # Power-off gap: same spot if weather can explain the change
            limit = MAX_WEATHER_RATE_HPA_PER_H * dt / 60 + 0.5
            if not moving and abs(dp) <= limit:
                w += dp
                estimated = False
                extrapolated_min = 0.0
            else:
                w += trend * min(dt, MAX_EXTRAPOLATE_MIN)
                estimated = True

        elif moving or abs(dp) / dt > MAX_WEATHER_RATE_HPA_PER_MIN:
            # Elevation change: carry the weather trend across it
            if extrapolated_min < MAX_EXTRAPOLATE_MIN:
                w += trend * dt
            extrapolated_min += dt
            estimated = True

        else:
            w += dp
            estimated = False
            extrapolated_min = 0.0
            trend += TREND_EMA_ALPHA * (dp / dt - trend)

        series.append(WeatherPoint(cur.ts, w, estimated))

    return series


def window(series, now, minutes):
    return [p for p in series if now - p.ts <= minutes * 60]


def change_over(series, now, minutes, min_coverage=0.5):
    """
    Pressure change over the last `minutes`, from a robust trend line.
    None when there is too little real (non-estimated) data.
    """
    pts = window(series, now, minutes)
    real = [p for p in pts if not p.estimated]

    if len(pts) < 3 or len(real) < max(3, min_coverage * minutes * 0.6):
        return None

    span = (pts[-1].ts - pts[0].ts) / 60.0
    if span < minutes * min_coverage:
        return None

    slope, _ = theil_sen([((p.ts - now) / 60.0, p.pressure) for p in pts])
    return None if slope is None else slope * minutes


# =============================================================
# Atmospheric tide
# =============================================================
#
# Solar heating makes surface pressure oscillate every day: a 12-hour wave
# (S2, peaks ~10:00 and ~22:00 local solar time, ~1.2*cos^3(lat) hPa) plus a
# smaller 24-hour wave (S1). At 30°N that's a +/-1 hPa swing that a naive
# 3-hour tendency reads as "rising"/"falling" weather. It is fitted from the
# station's own history (so local effects and clock-vs-solar time offset are
# captured), and removed before tendencies and storm risk are computed.

DAY_S = 86400.0
TIDE_MIN_SPAN_S = 36 * 3600


def _solve_linear(matrix, vector):
    n = len(vector)
    a = [row[:] + [v] for row, v in zip(matrix, vector)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-12:
            return None
        a[col], a[pivot] = a[pivot], a[col]
        for row in range(col + 1, n):
            f = a[row][col] / a[col][col]
            for k in range(col, n + 1):
                a[row][k] -= f * a[col][k]
    x = [0.0] * n
    for row in reversed(range(n)):
        x[row] = (a[row][n] - sum(a[row][k] * x[k] for k in range(row + 1, n))) / a[row][row]
    return x


def _tide_basis(ts):
    w = 2 * math.pi * (ts % DAY_S) / DAY_S
    return [math.cos(w), math.sin(w), math.cos(2 * w), math.sin(2 * w)]


@dataclass
class Tide:
    coefficients: list          # [S1 cos, S1 sin, S2 cos, S2 sin], hPa
    fitted: bool                # False = climatological fallback

    def at(self, ts):
        return sum(c * b for c, b in zip(self.coefficients, _tide_basis(ts)))

    @property
    def amplitude(self):
        """Largest deviation from the daily mean, hPa."""
        return max(abs(self.at(k * 600)) for k in range(144))


def climatological_tide(latitude_deg=30.6, utc_offset_s=None):
    """Textbook S2 only: 1.16*cos^3(lat) hPa, maxima ~10:00/22:00 local time."""
    utc_offset_s = -time.timezone if utc_offset_s is None else utc_offset_s
    amp = 1.16 * math.cos(math.radians(latitude_deg)) ** 3
    # Basis uses UTC time of day; peak at 10:00 local = 10 h - offset UTC
    peak_utc_h = (10.0 - utc_offset_s / 3600.0) % 12
    phase = 2 * (2 * math.pi * peak_utc_h / 24)
    return Tide([0.0, 0.0, amp * math.cos(phase), amp * math.sin(phase)], fitted=False)


def fit_tide(series, now, latitude_deg=30.6):
    """
    Least-squares fit of the S1 + S2 waves to the last 7 days of weather
    pressure, after removing slow weather changes with a 24-hour running
    mean. Needs ~1.5 days of real data; otherwise the textbook tide.
    """
    pts = [(p.ts, p.pressure) for p in series if not p.estimated and now - p.ts <= 7 * DAY_S]
    if len(pts) < 600 or pts[-1][0] - pts[0][0] < TIDE_MIN_SPAN_S:
        return climatological_tide(latitude_deg)

    # 15-minute means keep the fit fast and even-weighted
    buckets = {}
    for t, v in pts:
        buckets.setdefault(int(t // 900), []).append(v)
    grid = [(k * 900 + 450, statistics.fmean(v)) for k, v in sorted(buckets.items())]

    # Remove synoptic (multi-day) weather: subtract a centred 24 h mean
    residual = []
    j0 = j1 = 0
    running = 0.0
    for t, v in grid:
        while j1 < len(grid) and grid[j1][0] <= t + DAY_S / 2:
            running += grid[j1][1]
            j1 += 1
        while grid[j0][0] < t - DAY_S / 2:
            running -= grid[j0][1]
            j0 += 1
        n = j1 - j0
        # Only where the 24 h window is well covered
        if n >= 72:
            residual.append((t, v - running / n))

    if len(residual) < 96:
        return climatological_tide(latitude_deg)

    ata = [[0.0] * 4 for _ in range(4)]
    aty = [0.0] * 4
    for t, r in residual:
        b = _tide_basis(t)
        for i in range(4):
            aty[i] += b[i] * r
            for k in range(4):
                ata[i][k] += b[i] * b[k]

    coeffs = _solve_linear(ata, aty)
    if coeffs is None:
        return climatological_tide(latitude_deg)

    tide = Tide(coeffs, fitted=True)
    # Sanity: real tides are < ~3 hPa peak; reject a fit gone wild
    if tide.amplitude > 4.0:
        return climatological_tide(latitude_deg)
    return tide


def remove_tide(series, tide):
    return [WeatherPoint(p.ts, p.pressure - tide.at(p.ts), p.estimated) for p in series]


# =============================================================
# WMO tendency
# =============================================================

def tendency_text(change_3h):
    if change_3h is None:
        return "Measuring"

    a = abs(change_3h)
    if a < 0.1:
        return "Steady"

    direction = "Rising" if change_3h > 0 else "Falling"
    if a < 1.6:
        return f"{direction} slowly"
    if a < 3.6:
        return direction
    if a < 6.0:
        return f"{direction} quickly"
    return f"{direction} very rapidly"


def tendency_characteristic(series, now):
    """
    Shape of the last 3 hours (after WMO code table 0200): compares the
    trend of the first and second 90 minutes.
    """
    first = [p for p in window(series, now, 180) if now - p.ts > 90 * 60]
    second = window(series, now, 90)
    if len(first) < 10 or len(second) < 10:
        return None

    s1, _ = theil_sen([(p.ts / 60, p.pressure) for p in first])
    s2, _ = theil_sen([(p.ts / 60, p.pressure) for p in second])
    if s1 is None or s2 is None:
        return None

    eps = 0.3 / 90   # 0.3 hPa per 90 min counts as a real change
    up1, down1 = s1 > eps, s1 < -eps
    up2, down2 = s2 > eps, s2 < -eps

    if up1 and down2:
        return "Rising, then falling"
    if down1 and up2:
        return "Falling, then rising"
    if up1 and not (up2 or down2):
        return "Rising, then steady"
    if down1 and not (up2 or down2):
        return "Falling, then steady"
    if not (up1 or down1) and up2:
        return "Steady, then rising"
    if not (up1 or down1) and down2:
        return "Steady, then falling"
    if up1 and up2:
        return "Rising faster" if s2 > s1 * 1.5 else "Rising steadily"
    if down1 and down2:
        return "Falling faster" if s2 < s1 * 1.5 else "Falling steadily"
    return "Steady"


# =============================================================
# Zambretti forecaster
# =============================================================

ZAMBRETTI_TEXT = [
    "Settled fine", "Fine weather", "Becoming fine", "Fine, becoming less settled",
    "Fine, possible showers", "Fairly fine, improving", "Fairly fine, possible showers early",
    "Fairly fine, showery later", "Showery early, improving", "Changeable, mending",
    "Fairly fine, showers likely", "Rather unsettled, clearing later",
    "Unsettled, probably improving", "Showery, bright intervals",
    "Showery, becoming less settled", "Changeable, some rain",
    "Unsettled, short fine intervals", "Unsettled, rain later", "Unsettled, some rain",
    "Mostly very unsettled", "Occasional rain, worsening", "Rain at times, very unsettled",
    "Rain at frequent intervals", "Rain, very unsettled", "Stormy, may improve",
    "Stormy, much rain",
]

_RISE = [25, 25, 25, 24, 24, 19, 16, 12, 11, 9, 8, 6, 5, 2, 1, 1, 0, 0, 0, 0, 0, 0]
_STEADY = [25, 25, 25, 25, 25, 25, 23, 23, 22, 18, 15, 13, 10, 4, 1, 1, 0, 0, 0, 0, 0, 0]
_FALL = [25, 25, 25, 25, 25, 25, 25, 25, 23, 23, 21, 20, 17, 14, 7, 3, 1, 1, 1, 0, 0, 0]


def zambretti(slp_hpa, change_3h, month, northern=True):
    """
    Negretti & Zambra forecaster (the widely used 'beteljuice' tables),
    950-1050 hPa sea-level pressure, 3-hour trend, season.
    Returns (index 0..25, text); index 0 = best weather, 25 = worst.
    """
    if slp_hpa is None or change_3h is None:
        return None, None

    trend = 0 if abs(change_3h) < 1.6 else (1 if change_3h > 0 else -1)

    summer = 4 <= month <= 9 if northern else not (4 <= month <= 9)
    p = slp_hpa
    if summer:
        p += 7 if trend > 0 else (-7 if trend < 0 else 0)

    p = max(950.0, min(1049.9, p))
    option = int((p - 950.0) / (100.0 / 22))

    table = _RISE if trend > 0 else (_FALL if trend < 0 else _STEADY)
    index = table[option]
    return index, ZAMBRETTI_TEXT[index]


# =============================================================
# Storm / hazard model
# =============================================================

def _clamp01(x):
    return max(0.0, min(1.0, x))


def storm_risk(change_1h, change_3h, change_24h, slp_hpa, dew_c, humidity_change_3h, spread_c):
    """
    Probability (0-100) of stormy / rainy weather in the next ~12 h, with
    the reasons that drove it. Logistic combination of standard
    forecasting signals; weights chosen so a 'falling quickly' barometer
    with humid air reads as likely, and steady high pressure as unlikely.
    """
    reasons = []
    score = -3.0

    if change_3h is not None:
        fall = _clamp01((-change_3h - 1.0) / 5.0)          # full at -6 hPa/3h
        score += 3.5 * fall
        if change_3h <= -1.6:
            reasons.append(f"Pressure falling {abs(change_3h):.1f} hPa in 3 h")

        if change_1h is not None:
            # Accelerating fall (last hour faster than the 3 h average)
            accel = (change_3h / 3.0) - change_1h
            if change_3h <= -0.5 and change_1h < -0.5 and accel > 0.3:
                score += _clamp01(accel / 1.0) * 1.0
                reasons.append("Pressure fall is accelerating")

    if change_24h is not None and change_24h <= -10:
        score += 1.0
        reasons.append(f"Deep low developing ({change_24h:.0f} hPa in 24 h)")

    if slp_hpa is not None:
        low = _clamp01((1010.0 - slp_hpa) / 25.0)
        score += 1.5 * low
        if slp_hpa < 1000:
            reasons.append(f"Low pressure ({slp_hpa:.0f} hPa)")

    if dew_c is not None:
        moist = _clamp01((c_to_f(dew_c) - 50.0) / 20.0)  # full at 70 °F dew point
        score += 1.2 * moist
        if c_to_f(dew_c) >= 60:
            reasons.append(f"Humid air (dew point {c_to_f(dew_c):.0f}°F)")

    if humidity_change_3h is not None and humidity_change_3h > 5:
        score += 0.8 * _clamp01(humidity_change_3h / 15.0)
        reasons.append(f"Humidity rising ({humidity_change_3h:+.0f}% in 3 h)")

    if spread_c is not None and spread_c < 10:
        score += 0.8 * _clamp01((10 - spread_c) / 8.0)

    probability = 100.0 / (1.0 + math.exp(-score))

    if probability >= 75:
        level = "Severe"
    elif probability >= 50:
        level = "High"
    elif probability >= 25:
        level = "Moderate"
    else:
        level = "Low"

    if change_3h is not None and change_3h <= -3.6:
        eta = "within 3-6 hours"
    elif change_3h is not None and change_3h <= -1.6:
        eta = "within 6-12 hours"
    else:
        eta = None

    return round(probability), level, reasons, eta


def gust_front(samples, now):
    """
    Thunderstorm outflow: in the last 30 minutes (stationary), a quick
    pressure jump (>= 1 hPa within 20 min) together with a temperature
    drop (>= 2 °C).
    """
    recent = [s for s in samples
              if now - s.ts <= 30 * 60 and not s.moving
              and s.pressure is not None and s.temp_c is not None]
    if len(recent) < 5:
        return False

    for i in range(len(recent)):
        for j in range(i + 1, len(recent)):
            if recent[j].ts - recent[i].ts > 20 * 60:
                break
            if (recent[j].pressure - recent[i].pressure >= 1.0
                    and recent[i].temp_c - recent[j].temp_c >= 2.0):
                return True
    return False


# =============================================================
# Per-metric history and trends (for the charts)
# =============================================================

def bucket_points(points, now, hours=72, seconds=300):
    """Average (ts, value) points into fixed buckets for charting."""
    buckets = {}
    for ts, v in points:
        if v is not None and now - ts <= hours * 3600:
            buckets.setdefault(int(ts // seconds), []).append(v)
    return [(k * seconds + seconds / 2, statistics.fmean(v)) for k, v in sorted(buckets.items())]


def slope_per_hour(points, now, minutes=180):
    """Robust (Theil-Sen) rate of change over the last `minutes`, per hour."""
    recent = [(ts, v) for ts, v in points if v is not None and now - ts <= minutes * 60]
    if len(recent) < 20 or (recent[-1][0] - recent[0][0]) < minutes * 60 * 0.4:
        return None
    slope, _ = theil_sen([((ts - now) / 60.0, v) for ts, v in recent])
    return None if slope is None else slope * 60


# Readings older than this don't count as "current" (sensor unplugged)
CURRENT_MAX_AGE_S = 30 * 60


def metric_summary(points, now, unit, decimals):
    """Chart data for one metric. "now" and "slope_h" are only set while the
    newest reading is recent (CURRENT_MAX_AGE_S); "last"/"last_ts" always
    give the newest reading, e.g. to say when a sensor was last seen."""
    day = [v for ts, v in points if v is not None and now - ts <= 24 * 3600]
    last_ts, last = next(((ts, v) for ts, v in reversed(points) if v is not None), (None, None))
    fresh = last_ts is not None and now - last_ts <= CURRENT_MAX_AGE_S
    return {
        "points": bucket_points(points, now),
        "unit": unit,
        "decimals": decimals,
        "now": last if fresh else None,
        "last": last,
        "last_ts": last_ts,
        "slope_h": slope_per_hour(points, now) if fresh else None,
        "min_24h": min(day) if day else None,
        "max_24h": max(day) if day else None,
    }


# =============================================================
# Summary for the UI
# =============================================================

@dataclass
class WeatherSummary:
    station_hpa: float = None
    slp_hpa: float = None
    slp_source: str = None          # "elevation" or "auto"
    change_1h: float = None
    change_3h: float = None
    change_6h: float = None
    change_24h: float = None
    tendency: str = "Measuring"
    characteristic: str = None
    forecast_index: int = None
    forecast: str = None
    storm_probability: int = None
    storm_level: str = None
    storm_reasons: list = field(default_factory=list)
    storm_eta: str = None
    alerts: list = field(default_factory=list)   # (level, text)
    confidence: float = 0.0          # 0..1, share of real data in last 3 h
    temp_c: float = None
    humidity: float = None
    dew_c: float = None
    heat_index_f: float = None
    temp_min_24h_c: float = None
    temp_max_24h_c: float = None
    history: list = field(default_factory=list)  # (ts, weather pressure), 5-min
    trend_3h: tuple = None           # (slope hPa/min, intercept at now)
    tide_amplitude: float = None     # hPa, peak daily swing removed from trends
    tide_fitted: bool = False        # True = measured from history
    # Per-metric chart data: name -> {"points": [(ts, v)] (5-min, 72 h),
    #   "unit", "decimals", "now", "slope_h" (per hour, last 3 h),
    #   "min_24h", "max_24h"}
    metrics: dict = field(default_factory=dict)


def analyse(samples, now=None, elevation_m=None, northern=True):
    """
    Full analysis from minute samples (oldest first).
    elevation_m: known station elevation for true sea-level pressure;
    None = self-calibrating estimate.
    """
    now = time.time() if now is None else now
    out = WeatherSummary()

    samples = [s for s in samples if now - s.ts <= 72 * 3600]
    series = weather_pressure_series(samples)

    latest = next((s for s in reversed(samples) if s.pressure is not None), None)
    # "Current" temperature/humidity must be recent: with the outside sensor
    # unplugged, hours-old values would otherwise drive the storm/fog models
    latest_th = next((s for s in reversed(samples)
                      if s.temp_c is not None and s.humidity is not None), None)
    if latest_th and now - latest_th.ts > CURRENT_MAX_AGE_S:
        latest_th = None

    if latest_th:
        out.temp_c = latest_th.temp_c
        out.humidity = latest_th.humidity
        out.dew_c = dew_point_c(latest_th.temp_c, latest_th.humidity)
        out.heat_index_f = heat_index_f(c_to_f(latest_th.temp_c), latest_th.humidity)

    temps = [s.temp_c for s in samples if s.temp_c is not None and now - s.ts <= 24 * 3600]
    if temps:
        out.temp_min_24h_c, out.temp_max_24h_c = min(temps), max(temps)

    if not series or latest is None:
        add_metrics(out, samples, series, now)
        return out

    out.station_hpa = latest.pressure

    # Trends are computed with the daily atmospheric tide removed, but only
    # once it has been measured: a textbook tide with the wrong phase (clock
    # time isn't solar time) would add error rather than remove it.
    tide = fit_tide(series, now)
    out.tide_amplitude = tide.amplitude if tide.fitted else None
    out.tide_fitted = tide.fitted
    detided = remove_tide(series, tide) if tide.fitted else series

    out.change_1h = change_over(detided, now, 60)
    out.change_3h = change_over(detided, now, 180)
    out.change_6h = change_over(detided, now, 360)
    out.change_24h = change_over(series, now, 1440, min_coverage=0.4)   # tide cancels over 24 h

    out.tendency = tendency_text(out.change_3h)
    out.characteristic = tendency_characteristic(detided, now)

    last3 = window(series, now, 180)
    if last3:
        out.confidence = sum(not p.estimated for p in last3) / max(len(last3), 150)

    slope, intercept = theil_sen([((p.ts - now) / 60.0, p.pressure)
                                  for p in window(detided, now, 180)])
    if slope is not None:
        out.trend_3h = (slope, intercept)

    # Sea-level pressure
    if elevation_m is not None:
        out.slp_hpa = sea_level_pressure(latest.pressure, elevation_m, out.temp_c)
        out.slp_source = "elevation"
    elif len(series) >= 60:
        # Weather pressure relative to its own long-term median, anchored
        # to the standard atmosphere: a local anomaly, good enough for
        # Zambretti until the elevation (or GPS) is known.
        baseline = statistics.median(p.pressure for p in series)
        out.slp_hpa = STANDARD_PRESSURE + (series[-1].pressure - baseline)
        out.slp_source = "auto"

    month = time.localtime(now).tm_mon
    out.forecast_index, out.forecast = zambretti(out.slp_hpa, out.change_3h, month, northern)

    # Humidity change over 3 h
    hum = [(s.ts, s.humidity) for s in samples
           if s.humidity is not None and now - s.ts <= 180 * 60]
    humidity_change = None
    if len(hum) >= 30 and (hum[-1][0] - hum[0][0]) >= 90 * 60:
        hs, _ = theil_sen([((t - now) / 60.0, h) for t, h in hum])
        humidity_change = None if hs is None else hs * 180

    spread = None if out.dew_c is None else out.temp_c - out.dew_c

    (out.storm_probability, out.storm_level,
     out.storm_reasons, out.storm_eta) = storm_risk(
        out.change_1h, out.change_3h, out.change_24h, out.slp_hpa,
        out.dew_c, humidity_change, spread,
    )

    # Hazard alerts
    if gust_front(samples, now):
        out.alerts.append(("critical", "Thunderstorm outflow: sudden pressure jump and cooling"))
        out.storm_probability = max(out.storm_probability, 90)
        out.storm_level = "Severe"

    if out.change_3h is not None and out.change_3h <= -6.0:
        out.alerts.append(("critical", "Pressure falling very rapidly: severe weather likely"))
    elif out.storm_probability is not None and out.storm_probability >= 70:
        out.alerts.append(("warning", "Storm likely " + (out.storm_eta or "soon")))

    if spread is not None and spread <= 2.0 and out.humidity >= 95:
        out.alerts.append(("warning", "Fog or low cloud likely"))

    if out.temp_c is not None and out.temp_c <= 1.0 and out.humidity is not None and out.humidity >= 80:
        out.alerts.append(("warning", "Near freezing and damp: possible ice"))

    # Chart data: 5-minute points over 24 h
    last24 = window(series, now, 1440)
    buckets = {}
    for p in last24:
        buckets.setdefault(int(p.ts // 300), []).append(p.pressure)
    out.history = [(k * 300 + 150, statistics.fmean(v)) for k, v in sorted(buckets.items())]

    add_metrics(out, samples, series, now)
    return out


def add_metrics(out, samples, series, now):
    """Chart series + trends for every weather metric."""

    # Pressure: the weather-only series, shown as sea-level equivalent
    # when known (so the numbers match weather reports)
    offset = 0.0
    if out.slp_hpa is not None and series:
        offset = out.slp_hpa - series[-1].pressure
    out.metrics["pressure"] = metric_summary(
        [(p.ts, p.pressure + offset) for p in series], now, "hPa", 1)

    out.metrics["temp"] = metric_summary(
        [(s.ts, c_to_f(s.temp_c)) for s in samples], now, "°F", 1)

    out.metrics["humidity"] = metric_summary(
        [(s.ts, s.humidity) for s in samples], now, "%", 0)

    out.metrics["dew"] = metric_summary(
        [(s.ts, c_to_f(dew_point_c(s.temp_c, s.humidity))) for s in samples], now, "°F", 1)

    out.metrics["light"] = metric_summary(
        [(s.ts, s.lux) for s in samples], now, "lx", 0)
