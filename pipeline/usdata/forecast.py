"""When will a Visa Bulletin cutoff pass a priority date? Pure: no I/O, deterministic for a given input.

A series is one value per bulletin month: the cutoff date, Current (no cutoff), or None (unavailable "U", or no
archived bulletin that month). Dates are integers, days since 1970-01-01. Current counts as a cutoff equal to the
first day of the bulletin month (a backlog of zero days), so "crossed" is always `cutoff > priority date`.

Two models produce `paths`, an (n, horizon) array of simulated cutoffs for months 1..horizon after the origin:
- `pace`: the trailing 36-month mean advance, plus the empirical error that forecast would have made at each
  horizon from earlier origins in the same series. Path k takes the k-th quantile of the error at every horizon.
- `bootstrap`: 12-month blocks of monthly movements resampled from the trailing 120 months, applied to the
  latest cutoff, with a seeded generator.

Both are overconfident as simulated, so every path is widened around the per-month median by one factor per model
(`widen`). The factor is fit on backtest origins before CALIBRATE_BEFORE, as the smallest that keeps every
calibration year's coverage at WORST_YEAR_TARGET, and judged on origins from it on.

A date can be filed once it is current under either chart, so a dates-for-filing series is forecast and scored
against its effective cutoff: each widened filing path is raised to a widened final action path simulated at the
same origin with the final series' own seed (`effective`). Raising after widening keeps every filing path at or
above the final path it is paired with, so the filing bands and crossings are never behind final action's.
"""
from __future__ import annotations

import hashlib
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import date
from typing import Iterator, Literal, Mapping, Sequence

import numpy as np

from .model import Current, Cutoff, Month

ModelName = Literal["pace", "bootstrap"]
MODELS: tuple[ModelName, ...] = ("pace", "bootstrap")
Chart = Literal["final", "filing"]
CHARTS: tuple[Chart, ...] = ("final", "filing")  # series ids are vb/<chart>/<category>/<area>
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
SPREADS = (1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0, 4.0)
WORST_YEAR_TARGET = 0.75  # every calibration year's coverage must reach this
MIN_YEAR_JUDGED = 200  # an origin year with fewer judged trials than this is too thin to count
AVERAGE_TARGET = 0.80  # the first rule tried: pooled calibration coverage; kept only to publish how it held up
CALIBRATE_BEFORE = Month(2016, 1)  # origins before this fit the spread; origins from it on judge it
MIN_JUDGED = 30  # fewer judged trials than this in a series: too few to report its own coverage
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


def final_of(series_id: str) -> str | None:
    """The final action series a dates-for-filing series is effective against; None for any other series."""
    return "vb/final/" + series_id.removeprefix("vb/filing/") if series_id.startswith("vb/filing/") else None


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


def widen(paths: np.ndarray, spreads: Sequence[float]) -> Iterator[np.ndarray]:
    """For each spread s: median + s * (paths - median), the median taken per month across paths. s = 1 is the
    paths unchanged. The median path, and so each month's median cutoff, is the same for every s."""
    med = np.median(paths, axis=0)
    dev = paths - med
    for s in spreads:
        yield paths if s == 1 else med + s * dev


def effective(filing: np.ndarray, final: np.ndarray) -> np.ndarray:
    """Path by path, the later cutoff of the two charts, pairing paths by the rank of their mean cutoff: a
    comonotonic coupling. Pace paths are already in that order. Pairing bootstrap paths by index would pair
    independent draws, and the larger of two independent draws runs early."""
    def ranked(p: np.ndarray) -> np.ndarray:
        return p[np.argsort(p.mean(axis=1), kind="stable")]
    return np.maximum(ranked(filing), ranked(final))


def first_crossings(paths: np.ndarray, priority: Sequence[int]) -> np.ndarray:
    """(n_paths, len(priority)) month of the first simulated cutoff after each priority date; horizon+1 if none.

    A path that retrogresses after crossing still counts as crossed: that month was the first approval
    opportunity.
    """
    rows = np.arange(len(paths))
    out = np.empty((len(paths), len(priority)), dtype=int)
    for i, p in enumerate(priority):
        hit = paths > p
        first = hit.argmax(axis=1)
        out[:, i] = np.where(hit[rows, first], first + 1, paths.shape[1] + 1)
    return out


def crossing_quantiles(paths: np.ndarray, priority: int) -> Crossing:
    return _crossings(first_crossings(paths, [priority]), paths.shape[1])[0]


def _crossings(table: np.ndarray, horizon: int) -> list[Crossing]:
    """Per column of a first-crossing table: its 10th, 50th and 90th percentile month."""
    n = len(table)
    rows = np.sort(table, axis=0)[[min(n - 1, int(x * n)) for x in (0.10, 0.50, 0.90)]]
    return [tuple(None if v > horizon else int(v) for v in col) for col in rows.T]  # type: ignore[misc]


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
    spread: float
    origin: Month
    cutoff: int
    bands: tuple[tuple[int, ...], ...]  # months 1..HORIZON: cutoff days at BAND_LEVELS
    crossings: tuple[tuple[int, Crossing], ...]  # (priority date days, crossing quantiles), monthly steps


def _latest(series_id: str, h: History, model: ModelName, spread: float) -> tuple[Month, int, np.ndarray] | None:
    """(origin month, last printed cutoff, widened paths) from the latest bulletin, or from the last printed cutoff
    when the latest says "U". None once that is more than a year old, or with too little history."""
    origin = len(h.cutoff) - 1
    printed = [i for i, c in enumerate(h.cutoff) if c is not None]
    if not printed or origin - printed[-1] > STALE_MONTHS:
        return None
    raw = simulate(h, origin, model, HORIZON, _seed(series_id, h.month(origin), model))
    if raw is None:
        return None
    cutoff = h.cutoff[printed[-1]]
    assert cutoff is not None
    return h.month(origin), cutoff, next(widen(raw, [spread]))


def forecast(series_id: str, h: History, model: ModelName, spread: float, final: History | None = None
             ) -> Forecast | None:
    """Crossings are tabulated for priority dates on the first of each month from the cutoff to the bulletin month,
    the only dates still waiting. With `final` (the series' final action chart), cutoff and paths are the effective
    ones: the later of the two charts, path by path."""
    own = _latest(series_id, h, model, spread)
    if own is None:
        return None
    om, cutoff, paths = own
    fin = _latest(str(final_of(series_id)), final, model, spread) if final is not None else None
    if fin is not None and fin[0] == om:
        cutoff, paths = max(cutoff, fin[1]), effective(paths, fin[2])
    c = date.fromordinal(cutoff + _EPOCH)
    first = add_months(Month(c.year, c.month), 1) if c.day > 1 else Month(c.year, c.month)
    pds = [month_start(add_months(first, i)) for i in range(months_between(first, om) + 1)]
    pds = [p for p in pds if p >= cutoff]
    table = first_crossings(paths, pds) if pds else np.empty((len(paths), 0), dtype=int)
    return Forecast(model, spread, om, cutoff, tuple(bands(paths, om)), tuple(zip(pds, _crossings(table, HORIZON))))


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


def _either(h: History, final: History) -> tuple[int | None, ...]:
    """Per month of `h`: the later of its printed cutoff and `final`'s; None where neither printed one."""
    out = []
    for i, c in enumerate(h.cutoff):
        j = final.index(h.month(i))
        printed = [x for x in (c, final.cutoff[j] if 0 <= j < len(final.cutoff) else None) if x is not None]
        out.append(max(printed) if printed else None)
    return tuple(out)


def _actual(cutoffs: Sequence[int | None], origin: int, priority: int) -> int | None:
    for t in range(origin + 1, len(cutoffs)):
        c = cutoffs[t]
        if c is not None and c > priority:
            return t - origin
    return None


def backtest(series_id: str, h: History, model: ModelName, final: History | None = None
             ) -> dict[float, list[Trial]]:
    """Trials at every spread in SPREADS, all from one simulation per origin. With `final`, both the forecast and
    the actual crossing use the effective cutoff, each from data up to the origin only (see `forecast`)."""
    trials: dict[float, list[Trial]] = {s: [] for s in SPREADS}
    last = len(h.cutoff) - 1
    printed = _either(h, final) if final is not None else h.cutoff
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
        raw = simulate(h, origin, model, last - origin, _seed(series_id, om, model), BACKTEST_PATHS)
        if raw is None:
            continue
        widened: Iterator[np.ndarray] = widen(raw, SPREADS)
        if final is not None and 0 <= (j := final.index(om)) < len(final.cutoff):
            fraw = simulate(final, j, model, last - origin, _seed(final_of(series_id), om, model), BACKTEST_PATHS)
            if fraw is not None:
                widened = map(effective, widened, widen(fraw, SPREADS))
        actual = [_actual(printed, origin, p) for _, p in pds]
        for s, paths in zip(SPREADS, widened):
            predicted = _crossings(first_crossings(paths, [p for _, p in pds]), paths.shape[1])
            for (lead, _), q, a in zip(pds, predicted, actual):
                trials[s].append(Trial(om, lead, q, a, last - origin))
    return trials


def _backtest(task: tuple[str, History, ModelName, History | None]) -> dict[float, list[Trial]]:
    return backtest(*task)


def held_out(t: Trial) -> bool:
    return t.origin >= CALIBRATE_BEFORE


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


def by_year(trials: Sequence[Trial]) -> dict[int, Score]:
    """Scores by the calendar year of each trial's origin."""
    years: dict[int, list[Trial]] = {}
    for t in trials:
        years.setdefault(t.origin.year, []).append(t)
    return {y: score(ts) for y, ts in sorted(years.items())}


def worst_year(trials: Sequence[Trial]) -> tuple[int, Score] | None:
    """The origin year with the lowest coverage among those with at least MIN_YEAR_JUDGED judged trials; the
    earliest on a tie. None if no year has that many."""
    years = [(s.coverage80, y, s) for y, s in by_year(trials).items() if s.n >= MIN_YEAR_JUDGED]
    if not years:
        return None
    _, y, s = min(years, key=lambda c: (c[0], c[1]))
    return y, s


def calibrate(trials: Mapping[float, Sequence[Trial]]) -> float:
    """The smallest spread at which every origin year with enough judged trials reaches WORST_YEAR_TARGET; the
    largest if none does."""
    for s in sorted(trials):
        worst = worst_year(trials[s])
        if worst is not None and worst[1].coverage80 >= WORST_YEAR_TARGET:
            return s
    return max(trials)


def calibrate_average(trials: Mapping[float, Sequence[Trial]]) -> float:
    """The first rule tried: the smallest spread whose pooled coverage reaches AVERAGE_TARGET; the largest if none
    does. It held far less than that on held-out origins, so it is published only as a disclosure."""
    for s in sorted(trials):
        cov = score(trials[s]).coverage80
        if cov is not None and cov >= AVERAGE_TARGET:
            return s
    return max(trials)


def choose(held: Mapping[ModelName, Sequence[Trial]]) -> ModelName:
    """From each model's held-out trials at its spread: the higher worst-year coverage; then the lower pooled
    median error."""
    def key(m: ModelName):
        worst = worst_year(held[m])
        err = score(held[m]).median_abs_error
        return (-(worst[1].coverage80 if worst else 0), err if err is not None else float("inf"), m)
    return min(MODELS, key=key)


@dataclass(frozen=True)
class Fit:
    """One model's scores at its spread: on calibration origins (which chose the spread) and held-out origins."""
    spread: float
    calibration: Score
    held_out: Score


@dataclass(frozen=True)
class PooledFit(Fit):
    """A model's fit over all series, with its scores by origin year and its worst held-out year."""
    by_year: dict[int, Score]
    worst_held_out: tuple[int, Score] | None


@dataclass(frozen=True)
class SeriesResult:
    forecast: Forecast | None  # with the chosen model; None when the series has too little history
    fits: dict[ModelName, Fit]  # this series' trials, at each model's pooled spread


@dataclass(frozen=True)
class Results:
    model: ModelName
    pooled: dict[ModelName, PooledFit]  # both charts together
    by_chart: dict[Chart, PooledFit]  # the chosen model at its spread, each chart on its own
    first_rule: dict[ModelName, Fit]  # each model at the spread calibrate_average picks
    # (spread, pooled calibration coverage, worst calibration year's coverage)
    calibration_curve: dict[ModelName, tuple[tuple[float, float | None, float | None], ...]]
    series: dict[str, SeriesResult]


def _fit(trials: Sequence[Trial], spread: float) -> Fit:
    return Fit(spread, score([t for t in trials if not held_out(t)]), score([t for t in trials if held_out(t)]))


def _pooled(trials: Sequence[Trial], spread: float) -> PooledFit:
    return PooledFit(**vars(_fit(trials, spread)), by_year=by_year(trials),
                     worst_held_out=worst_year([t for t in trials if held_out(t)]))


def run(histories: dict[str, History]) -> Results:
    """Backtest both models on every series; fit each model's spread on the calibration origins; choose the model
    by its held-out scores; then forecast with it."""
    series = sorted(histories.items())
    final = {sid: histories.get(str(final_of(sid))) for sid, _ in series}
    tasks = [(sid, h, m, final[sid]) for m in MODELS for sid, h in series]
    with ProcessPoolExecutor() as pool:  # one simulation per (model, series, origin); the work is CPU-bound
        done = dict(zip(((m, sid) for sid, _, m, _ in tasks), pool.map(_backtest, tasks, chunksize=4)))
    trials = {m: {sid: done[m, sid] for sid, _ in series} for m in MODELS}
    calib = {m: {s: [t for ts in trials[m].values() for t in ts[s] if not held_out(t)] for s in SPREADS}
             for m in MODELS}
    spread = {m: calibrate(calib[m]) for m in MODELS}
    every = {m: {s: [t for ts in trials[m].values() for t in ts[s]] for s in SPREADS} for m in MODELS}
    held = {m: [t for t in every[m][spread[m]] if held_out(t)] for m in MODELS}
    pooled = {m: _pooled(every[m][spread[m]], spread[m]) for m in MODELS}
    first = {m: _fit(every[m][s], s) for m in MODELS for s in [calibrate_average(calib[m])]}
    curve = {m: tuple((s, score(calib[m][s]).coverage80, w[1].coverage80 if (w := worst_year(calib[m][s])) else None)
                      for s in SPREADS) for m in MODELS}
    chosen = choose(held)
    by_chart = {c: _pooled([t for sid, ts in trials[chosen].items() if sid.startswith(f"vb/{c}/")
                            for t in ts[spread[chosen]]], spread[chosen]) for c in CHARTS}
    return Results(chosen, pooled, by_chart, first, curve,
                   {sid: SeriesResult(forecast(sid, h, chosen, spread[chosen], final[sid]),
                                      {m: _fit(trials[m][sid][spread[m]], spread[m]) for m in MODELS})
                    for sid, h in series})
