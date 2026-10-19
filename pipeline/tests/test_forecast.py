from __future__ import annotations

from datetime import date, timedelta

import pytest

from usdata.forecast import (HORIZON, MODELS, History, Trial, add_months, backtest, choose, crossing_quantiles,
                             days, forecast, score, simulate, Score)
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
    assert crossing_quantiles(paths, priority) == (18, 18, 18)


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
    f = forecast("vb/final/x/all", h, model)
    trials = backtest("vb/final/x/all", h, model)
    assert f is not None and len(f.bands) == HORIZON and len(trials) > 20
    assert f == forecast("vb/final/x/all", h, model)
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


def test_choose_prefers_calibrated_coverage_then_error():
    assert choose({"pace": Score(100, 0.62, 90, 3.0), "bootstrap": Score(100, 0.78, 90, 9.0)}) == "bootstrap"
    assert choose({"pace": Score(100, 0.804, 90, 3.0), "bootstrap": Score(100, 0.796, 90, 9.0)}) == "pace"


def test_unavailable_latest_bulletin_projects_from_the_last_printed_cutoff():
    rows = [(add_months(START, i), Cutoff(date(1995, 1, 1) + timedelta(days=30 * i))) for i in range(60)]
    rows.append((add_months(START, 60), None))
    f = forecast("vb/final/x/all", History.of(rows), "pace")
    assert f is not None and f.origin == add_months(START, 60)
    assert f.cutoff == days(date(1995, 1, 1) + timedelta(days=30 * 59))
