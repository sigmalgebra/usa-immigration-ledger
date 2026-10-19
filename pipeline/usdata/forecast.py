"""When will a Visa Bulletin cutoff pass a priority date? Pure: no I/O, deterministic for a given input.

A series is one value per bulletin month: the cutoff date, Current (no cutoff), or None (unavailable "U", or no
archived bulletin that month). Dates are integers, days since 1970-01-01. Current counts as a cutoff equal to the
first day of the bulletin month (a backlog of zero days), so "crossed" is always `cutoff > priority date`.

Two models produce `paths`, an (n, horizon) array of simulated cutoffs for months 1..horizon after the origin:
- `pace`: the trailing 36-month mean advance, plus the empirical error that forecast would have made at each
  horizon from earlier origins in the same series. Path k takes the k-th quantile of the error at every horizon.
- `bootstrap`: 12-month blocks of monthly movements resampled from the trailing 120 months, applied to the
  latest cutoff, with a seeded generator.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from typing import Literal, Sequence

import numpy as np

from .model import Current, Cutoff, Month

ModelName = Literal["pace", "bootstrap"]
MODELS: tuple[ModelName, ...] = ("pace", "bootstrap")
HORIZON = 240
N_PATHS = 2000
BACKTEST_PATHS = 500  # enough for a 10th/90th percentile; keeps a full rebuild under a minute
STALE_MONTHS = 12
PACE_LEVELS = 200
PACE_WINDOW = 36
PACE_MIN_ERRORS = 12
BLOCK = 12
BOOTSTRAP_WINDOW = 120
MIN_HISTORY = 24
BACKTEST_FROM = Month(2008, 1)
BACKTEST_LEADS = (6, 12, 24, 48)  # synthetic priority date = origin cutoff + this many months
BAND_LEVELS = (0.10, 0.25, 0.50, 0.75, 0.90)
_EPOCH = date(1970, 1, 1).toordinal()

Crossing = tuple[int | None, int | None, int | None]  # months after origin (q10, q50, q90); None = beyond horizon


def days(d: date) -> int:
    return d.toordinal() - _EPOCH


def month_start(m: Month) -> int:
    return days(date(m.year, m.month, 1))


def add_months(m: Month, k: int) -> Month:
    i = m.year * 12 + m.month - 1 + k
    return Month(i // 12, i % 12 + 1)


def months_between(a: Month, b: Month) -> int:
    return (b.year - a.year) * 12 + b.month - a.month


@dataclass(frozen=True)
class History:
    """One series on a gap-free monthly grid from its first bulletin."""

    start: Month
    cutoff: tuple[int | None, ...]  # None: unavailable or not archived

    @staticmethod
    def of(rows: Sequence[tuple[Month, Cutoff | Current | None]]) -> History:
        rows = sorted(rows, key=lambda r: r[0])
        start = rows[0][0]
        grid: list[int | None] = [None] * (months_between(start, rows[-1][0]) + 1)
        for m, v in rows:
            if isinstance(v, Cutoff):
                grid[months_between(start, m)] = days(v.date)
            elif isinstance(v, Current):
                grid[months_between(start, m)] = month_start(m)
        return History(start, tuple(grid))

    def month(self, i: int) -> Month:
        return add_months(self.start, i)

    def index(self, m: Month) -> int:
        return months_between(self.start, m)

    def filled(self) -> np.ndarray:
        """Missing months carry the last printed cutoff forward, so movement is 0 there and jumps on return.
        Months before the first printed value are NaN."""
        out = np.full(len(self.cutoff), np.nan)
        last = np.nan
        for i, c in enumerate(self.cutoff):
            last = c if c is not None else last
            out[i] = last
        return out


def _seed(*parts: object) -> int:
    return int.from_bytes(hashlib.sha256("|".join(map(str, parts)).encode()).digest()[:8], "big")


def simulate(h: History, origin: int, model: ModelName, horizon: int = HORIZON, seed: int = 0,
             n_paths: int = N_PATHS) -> np.ndarray | None:
    """Cutoff paths for months origin+1 .. origin+horizon, using only data up to `origin`. None: too little history.
    An origin printed "U" starts from the last printed cutoff."""
    f = h.filled()[: origin + 1]
    if np.isnan(f[-1]):
        return None
    known = int(np.sum(~np.isnan(f)))
    if known <= MIN_HISTORY:
        return None
    return _pace(f, horizon) if model == "pace" else _bootstrap(f, horizon, seed, n_paths)


def _pace(f: np.ndarray, horizon: int) -> np.ndarray | None:
    n = len(f)
    w = min(PACE_WINDOW, int(np.sum(~np.isnan(f))) - 1)
    rate = np.full(n, np.nan)
    rate[w:] = (f[w:] - f[:-w]) / w  # the pace known at each origin
    if np.isnan(rate[-1]):
        return None
    point = f[-1] + rate[-1] * np.arange(1, horizon + 1)
    # err[o, k-1]: how far the pace forecast made at origin o missed the printed cutoff k months later.
    k_max = min(horizon, n - 1)
    k = np.arange(1, k_max + 1)
    o = np.arange(n)[:, None]
    later = np.where(o + k < n, o + k, 0)
    err = np.where(o + k < n, f[later] - f[o] - rate[o] * k, np.nan)
    counts = np.sum(~np.isnan(err), axis=0)
    usable = int(np.argmin(counts >= PACE_MIN_ERRORS)) if not np.all(counts >= PACE_MIN_ERRORS) else k_max
    if usable == 0:
        return None
    srt = np.sort(err[:, :usable], axis=0)  # NaN sorts last
    levels = (np.arange(PACE_LEVELS) + 0.5) / PACE_LEVELS
    pick = np.ceil(levels[:, None] * counts[:usable]).astype(int) - 1  # inverted-CDF quantile
    offsets = np.empty((PACE_LEVELS, horizon))
    offsets[:, :usable] = np.take_along_axis(srt, pick, axis=0)
    # Beyond the longest horizon this series has enough errors for, widen the last ones in proportion to time.
    offsets[:, usable:] = offsets[:, usable - 1: usable] * np.arange(usable + 1, horizon + 1) / usable
    return point + offsets


def _bootstrap(f: np.ndarray, horizon: int, seed: int, n_paths: int) -> np.ndarray | None:
    moves = np.diff(f[-(BOOTSTRAP_WINDOW + 1):])
    moves = moves[~np.isnan(moves)]
    if len(moves) < MIN_HISTORY:
        return None
    rng = np.random.default_rng(seed)
    n_blocks = -(-horizon // BLOCK)
    starts = rng.integers(0, len(moves) - BLOCK + 1, size=(n_paths, n_blocks))
    idx = (starts[:, :, None] + np.arange(BLOCK)).reshape(n_paths, -1)[:, :horizon]
    return f[-1] + np.cumsum(moves[idx], axis=1)


def first_crossings(paths: np.ndarray, priority: Sequence[int]) -> np.ndarray:
    """(n_paths, len(priority)) month of the first simulated cutoff after each priority date; horizon+1 if none.

    A path that retrogresses after crossing still counts as crossed: that month was the first approval
    opportunity. The running maximum is only a device to find the first crossing with a binary search.
    """
    runmax = np.maximum.accumulate(paths, axis=1)
    out = np.empty((len(paths), len(priority)), dtype=int)
    for i, p in enumerate(priority):
        hit = runmax > p
        out[:, i] = np.where(hit[:, -1], hit.argmax(axis=1) + 1, paths.shape[1] + 1)
    return out


def _q(sorted_vals: np.ndarray, level: float) -> int:
    return int(sorted_vals[min(len(sorted_vals) - 1, int(level * len(sorted_vals)))])


def crossing_quantiles(paths: np.ndarray, priority: int) -> Crossing:
    return _crossing(first_crossings(paths, [priority])[:, 0], paths.shape[1])


def _crossing(months: np.ndarray, horizon: int) -> Crossing:
    s = np.sort(months)
    q = [_q(s, x) for x in (0.10, 0.50, 0.90)]
    return tuple(None if v > horizon else v for v in q)  # type: ignore[return-value]


def bands(paths: np.ndarray, origin: Month) -> list[tuple[int, ...]]:
    """Cutoff quantiles per month ahead, capped at the bulletin month itself (a cutoff can't pass "Current")."""
    s = np.sort(paths, axis=0)
    out = []
    for k in range(paths.shape[1]):
        cap = month_start(add_months(origin, k + 1))
        out.append(tuple(min(cap, int(round(s[min(len(s) - 1, int(x * len(s))), k]))) for x in BAND_LEVELS))
    return out


@dataclass(frozen=True)
class Forecast:
    model: ModelName
    origin: Month
    cutoff: int
    bands: tuple[tuple[int, ...], ...]  # months 1..HORIZON: cutoff days at BAND_LEVELS
    crossings: tuple[tuple[int, Crossing], ...]  # (priority date days, crossing quantiles), monthly steps


def forecast(series_id: str, h: History, model: ModelName) -> Forecast | None:
    """From the latest bulletin, or the last printed cutoff when the latest says "U" (None once that is more than
    a year old). Crossings are tabulated for priority dates on the first of each month from that cutoff to the
    bulletin month, the only dates still waiting."""
    origin = len(h.cutoff) - 1
    printed = [i for i, c in enumerate(h.cutoff) if c is not None]
    if not printed or origin - printed[-1] > STALE_MONTHS:
        return None
    paths = simulate(h, origin, model, HORIZON, _seed(series_id, h.month(origin), model))
    if paths is None:
        return None
    om = h.month(origin)
    cutoff = h.cutoff[printed[-1]]
    assert cutoff is not None
    c = date.fromordinal(cutoff + _EPOCH)
    first = add_months(Month(c.year, c.month), 1) if c.day > 1 else Month(c.year, c.month)
    pds = [month_start(add_months(first, i)) for i in range(months_between(first, om) + 1)]
    pds = [p for p in pds if p >= cutoff]
    table = first_crossings(paths, pds) if pds else np.empty((len(paths), 0))
    return Forecast(model, om, cutoff, tuple(bands(paths, om)),
                    tuple((p, _crossing(table[:, i], HORIZON)) for i, p in enumerate(pds)))


@dataclass(frozen=True)
class Trial:
    origin: Month
    lead: int  # months between the origin cutoff and the synthetic priority date
    predicted: Crossing
    actual: int | None  # months after origin of the first printed crossing; None if not yet by the last bulletin
    observed: int  # months of later bulletins available


@dataclass(frozen=True)
class Score:
    n: int  # trials whose interval can be judged: crossing known, or interval's upper end already passed
    coverage80: float | None
    n_error: int  # trials with a known crossing and a finite median
    median_abs_error: float | None  # months


def _actual(h: History, origin: int, priority: int) -> int | None:
    for t in range(origin + 1, len(h.cutoff)):
        c = h.cutoff[t]
        if c is not None and c > priority:
            return t - origin
    return None


def backtest(series_id: str, h: History, model: ModelName) -> list[Trial]:
    trials = []
    last = len(h.cutoff) - 1
    for origin in range(max(0, h.index(BACKTEST_FROM)), last - MIN_HISTORY + 1):
        c = h.cutoff[origin]
        if c is None:
            continue
        om = h.month(origin)
        cd = date.fromordinal(c + _EPOCH)
        pds = []
        for lead in BACKTEST_LEADS:
            pm = add_months(Month(cd.year, cd.month), lead)
            p = days(date(pm.year, pm.month, min(cd.day, 28)))
            if p < month_start(om):  # a later priority date would still be in the future at the origin
                pds.append((lead, p))
        if not pds:
            continue
        paths = simulate(h, origin, model, last - origin, _seed(series_id, om, model), BACKTEST_PATHS)
        if paths is None:
            continue
        table = first_crossings(paths, [p for _, p in pds])
        for i, (lead, p) in enumerate(pds):
            trials.append(Trial(om, lead, _crossing(table[:, i], paths.shape[1]), _actual(h, origin, p), last - origin))
    return trials


def score(trials: Sequence[Trial]) -> Score:
    judged, errors = [], []
    for t in trials:
        lo, mid, hi = t.predicted
        if t.actual is not None:
            judged.append(lo is not None and lo <= t.actual and (hi is None or t.actual <= hi))
            if mid is not None:
                errors.append(abs(mid - t.actual))
        elif hi is not None and hi < t.observed:
            judged.append(False)
    return Score(len(judged), round(sum(judged) / len(judged), 4) if judged else None, len(errors),
                 float(np.median(errors)) if errors else None)


def choose(pooled: dict[ModelName, Score]) -> ModelName:
    """Pooled 80% coverage closest to 80% (to the whole percentage point); then the lower median error."""
    def key(m: ModelName):
        s = pooled[m]
        cov = round(abs((s.coverage80 if s.coverage80 is not None else 0) - 0.8) * 100)
        return (cov, s.median_abs_error if s.median_abs_error is not None else float("inf"), m)
    return min(MODELS, key=key)


@dataclass(frozen=True)
class SeriesResult:
    forecast: Forecast | None  # with the chosen model; None when the series has too little history
    scores: dict[ModelName, Score]


@dataclass(frozen=True)
class Results:
    model: ModelName
    pooled: dict[ModelName, Score]
    series: dict[str, SeriesResult]


def run(histories: dict[str, History]) -> Results:
    """Backtest both models on every series, choose one by the pooled scores, then forecast with it."""
    trials = {m: {sid: backtest(sid, h, m) for sid, h in sorted(histories.items())} for m in MODELS}
    pooled = {m: score([t for ts in trials[m].values() for t in ts]) for m in MODELS}
    chosen = choose(pooled)
    return Results(chosen, pooled, {sid: SeriesResult(forecast(sid, h, chosen),
                                                      {m: score(trials[m][sid]) for m in MODELS})
                                    for sid, h in sorted(histories.items())})
