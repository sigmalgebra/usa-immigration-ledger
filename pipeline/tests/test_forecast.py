from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest

from usdata.forecast import (HORIZON, MODELS, SPREADS, History, Trial, add_months, backtest, calibrate, choose,
                             crossing_quantiles, days, forecast, score, simulate, widen, Score)
from usdata.publish import DATA
from usdata.model import Current, Cutoff, Month

START = Month(2000, 1)


def series(n: int, cutoff0: date, step_days: int) -> History:
    return History.of([(add_months(START, i), Cutoff(cutoff0 + timedelta(days=step_days * i))) for i in range(n)])


@pytest.mark.parametrize("model", MODELS)
def test_constant_pace_forecasts_the_exact_crossing_month(model):
    h = series(60, date(1995, 1, 1), 30)
    origin = 59
    last = date(1995, 1, 1) + timedelta(days=30 * 59)
    priority = days(last) + 30 * 17 + 5  # passed by the 18th bulletin after the origin
    paths = simulate(h, origin, model, HORIZON, seed=1)
    widened = list(widen(paths, SPREADS))
    assert widened[0] is paths
    assert [crossing_quantiles(w, priority) for w in widened] == [(18, 18, 18)] * len(SPREADS)


def test_spread_widens_the_crossing_range_symmetrically_around_the_median():
    # 21 paths, 10 days a month apart from a median that advances 10 days a month.
    k = np.arange(1, HORIZON + 1)
    paths = 10.0 * k + 10.0 * np.arange(-10, 11)[:, None]
    priority = 1005
    assert [crossing_quantiles(w, priority) for w in widen(paths, [1, 1.5, 2])] == [
        (93, 101, 109), (89, 101, 113), (85, 101, 117)]


@pytest.mark.parametrize("model", MODELS)
def test_frozen_series_is_beyond_the_horizon(model):
    h = series(60, date(2001, 6, 1), 0)
    paths = simulate(h, 59, model, HORIZON, seed=1)
    assert crossing_quantiles(paths, days(date(2001, 6, 2))) == (None, None, None)


def _noisy() -> History:
    steps = [0, 45, 10, -20, 90, 30, 0, 0, 60, 15, 120, -60] * 13
    c, rows = date(1990, 1, 1), []
    for i, s in enumerate(steps):
        c += timedelta(days=s)
        rows.append((add_months(START, i), Cutoff(c)))
    rows[30] = (rows[30][0], None)
    return History.of(rows)


@pytest.mark.parametrize("model", MODELS)
def test_forecast_and_backtest_are_deterministic(model):
    h = _noisy()
    f = forecast("vb/final/x/all", h, model, 1.5)
    trials = backtest("vb/final/x/all", h, model)
    assert f is not None and len(f.bands) == HORIZON and f.spread == 1.5
    assert list(trials) == list(SPREADS) and len(trials[1.0]) > 20
    assert f == forecast("vb/final/x/all", h, model, 1.5)
    assert trials == backtest("vb/final/x/all", h, model)


def test_current_is_a_zero_backlog():
    h = History.of([(Month(2020, 1), Current()), (Month(2020, 3), Cutoff(date(2019, 5, 1)))])
    assert h.cutoff == (days(date(2020, 1, 1)), None, days(date(2019, 5, 1)))


def test_score_counts_known_and_overdue_outcomes_only():
    trials = [
        Trial(START, 6, (2, 4, 6), 5, 30),       # inside, error 1
        Trial(START, 6, (2, 4, 6), 9, 30),       # outside, error 5
        Trial(START, 6, (2, 4, 6), None, 30),    # not crossed and the whole interval has passed: a miss
        Trial(START, 6, (2, 40, 60), None, 30),  # not crossed, interval still open: not judged
        Trial(START, 6, (2, None, None), 7, 30), # inside an open-ended interval; no median to score
    ]
    assert score(trials) == Score(n=4, coverage80=0.5, n_error=2, median_abs_error=3.0)


def _year(year: int, judged: int, hits: int) -> list[Trial]:
    """`judged` trials from origins in `year`, `hits` of them inside the range."""
    return [Trial(Month(year, 1 + i % 12), 6, (2, 4, 6), 5 if i < hits else 9, 30) for i in range(judged)]


def test_calibrate_picks_the_smallest_spread_where_every_year_reaches_75_percent():
    thin = _year(2015, 199, 0)  # under MIN_YEAR_JUDGED: ignored however badly it does
    grid = {
        1.0: _year(2010, 200, 190) + _year(2011, 200, 140) + thin,   # 2011 at 70%
        1.5: _year(2010, 200, 150) + _year(2011, 200, 160) + thin,   # 75% and 80%: the first to qualify
        2.0: _year(2010, 200, 200) + _year(2011, 200, 200) + thin,
    }
    assert calibrate(grid) == 1.5
    assert calibrate({1.0: _year(2010, 200, 100), 2.0: _year(2010, 200, 149)}) == 2.0
    assert calibrate({1.0: thin, 3.0: thin}) == 3.0


def test_choose_prefers_the_higher_worst_year_then_the_lower_error():
    steady = _year(2016, 200, 170) + _year(2017, 200, 170)       # 85% every year
    lumpy = _year(2016, 200, 200) + _year(2017, 200, 150)        # higher pooled, worst year 75%
    assert choose({"pace": lumpy, "bootstrap": steady}) == "bootstrap"
    late = [Trial(t.origin, t.lead, (2, 4, 6), 6 if t.actual == 5 else 9, 30) for t in steady]  # inside, error 2
    assert choose({"pace": late, "bootstrap": steady}) == "bootstrap"
    assert choose({"pace": steady, "bootstrap": late}) == "pace"


def test_unavailable_latest_bulletin_projects_from_the_last_printed_cutoff():
    rows = [(add_months(START, i), Cutoff(date(1995, 1, 1) + timedelta(days=30 * i))) for i in range(60)]
    rows.append((add_months(START, 60), None))
    f = forecast("vb/final/x/all", History.of(rows), "pace", 1.0)
    assert f is not None and f.origin == add_months(START, 60)
    assert f.cutoff == days(date(1995, 1, 1) + timedelta(days=30 * 59))


def _crossing(f, priority: date):
    return dict(f.crossings)[days(priority)]


@pytest.mark.parametrize("model", MODELS)
def test_a_filing_date_ahead_of_final_but_slower_is_filed_once_final_action_passes_it(model):
    final = series(60, date(1990, 1, 1), 60)   # 1999-09-11 at the origin, 60 days a month
    filing = series(60, date(2000, 1, 1), 10)  # 2001-08-13 at the origin, 10 days a month
    alone = forecast("vb/filing/x/all", filing, model, 1.0)
    effective = forecast("vb/filing/x/all", filing, model, 1.0, final)
    approval = forecast("vb/final/x/all", final, model, 1.0)
    assert alone and effective and approval
    assert _crossing(alone, date(2003, 1, 1)) == (51, 51, 51)
    assert _crossing(approval, date(2003, 1, 1)) == (21, 21, 21)
    assert _crossing(effective, date(2003, 1, 1)) == (21, 21, 21)
    assert effective.cutoff == days(date(2001, 8, 13))


def test_filing_backtest_counts_a_crossing_on_either_chart_without_looking_ahead():
    final = series(132, date(1985, 1, 1), 60)   # 2000-10-09 at the first origin, January 2008
    filing = series(132, date(2000, 1, 1), 10)  # 2002-08-18 there
    alone = backtest("vb/filing/x/all", filing, "pace")[1.0]
    effective = backtest("vb/filing/x/all", filing, "pace", final)[1.0]
    first = Month(2008, 1)
    assert [(t.lead, t.predicted, t.actual) for t in alone if t.origin == first][:2] == [
        (6, (19, 19, 19), 19), (12, (None, None, None), None)]
    assert [(t.lead, t.predicted, t.actual) for t in effective if t.origin == first][:2] == [
        (6, (15, 15, 15), 15), (12, (18, 18, 18), 18)]
    # Changing final action after an origin changes what happened next, never what was forecast there.
    cut = final.index(Month(2009, 1))
    moved = History(final.start, final.cutoff[:cut] + tuple(c + 9000 for c in final.cutoff[cut:]))
    later = backtest("vb/filing/x/all", filing, "pace", moved)[1.0]
    assert [t.predicted for t in later if t.origin < Month(2009, 1)] ==         [t.predicted for t in effective if t.origin < Month(2009, 1)]
    assert [t.actual for t in later if t.origin < Month(2009, 1)] !=         [t.actual for t in effective if t.origin < Month(2009, 1)]


def _published(chart: str, category: str, area: str) -> dict | None:
    p = DATA / "visa-bulletin" / "forecast" / chart / category / f"{area}.json"
    return json.loads(p.read_text(encoding="utf-8"))["forecast"] if p.exists() else None


def _wait(f: dict, priority: date) -> tuple[float, float, float]:
    """The page's lookup (site/src/lib/wait.ts): interpolate the crossing table; beyond the horizon is +inf."""
    p, rows = days(priority), f["crossings"]
    i = next((k for k, r in enumerate(rows) if r[0] > p), len(rows))
    lo, hi = rows[max(0, i - 1)], rows[min(len(rows) - 1, i)]
    frac = 0 if hi[0] == lo[0] else (p - lo[0]) / (hi[0] - lo[0])
    out = []
    for k in (1, 2, 3):
        a, b = lo[k], hi[k]
        m = b if a is None or b is None else round(a + (b - a) * frac)
        out.append(float("inf") if m is None else m)
    return out[0], out[1], out[2]


def test_published_f1_filing_forecast_for_december_2021_is_not_later_than_final_action():
    final, filing = _published("final", "f1", "all"), _published("filing", "f1", "all")
    assert final and filing
    pd = date(2021, 12, 12)
    assert days(pd) >= days(date.fromisoformat(filing["cutoff"]))  # still waiting under both charts
    (f10, f50, _), (a10, a50, _) = _wait(filing, pd), _wait(final, pd)
    assert f50 <= a50 and f10 <= a10


def test_published_filing_forecasts_are_never_later_than_final_action():
    pairs = [(p, p.parents[2] / "final" / p.parent.name / p.name)
             for p in (DATA / "visa-bulletin" / "forecast" / "filing").glob("*/*.json")]
    pairs = [(fl, fn) for fl, fn in pairs if fn.exists()]
    assert len(pairs) > 20
    inf = float("inf")
    checked = 0
    for fl, fn in pairs:
        filing, final = (json.loads(p.read_text(encoding="utf-8"))["forecast"] for p in (fl, fn))
        assert filing["origin"] == final["origin"], fl
        assert filing["cutoff"] >= final["cutoff"], fl
        later = {r[0]: [inf if q is None else q for q in r[1:]] for r in final["crossings"]}
        for pd, *q in filing["crossings"]:
            if pd in later:
                assert all(x <= y for x, y in zip((inf if v is None else v for v in q), later[pd])), (fl, pd)
                checked += 1
        assert all(x >= y for a, b in zip(filing["bands"], final["bands"]) for x, y in zip(a, b)), fl
    assert checked > 1000
