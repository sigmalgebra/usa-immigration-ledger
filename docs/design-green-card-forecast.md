# Design: green card wait forecast

## Problem

A visitor with a family or employment green card case knows their **priority date**, category (F1–F4, EB1–EB5)
and country of chargeability. They want to know when their case can be approved. For preference categories the
binding constraint is the State Department's monthly **Visa Bulletin**: a case can be approved only once the
bulletin's *final action date* for its category and country is later than its priority date. USCIS processing
time matters only after that. We forecast when the final action date will pass a given priority date, with an
honest range, and we publish how accurate the method would have been in the past.

Constraints: the site is static (no server), so all forecasting runs in the Python pipeline at build time and ships
as data. The browser only looks up and interpolates. travel.state.gov refuses automated clients, so bulletins come
from Internet Archive captures (same rule and code path as the existing `wayback` command).

## Usage

```bash
PYTHONPATH=pipeline python -m usdata wayback --dataset state-vb   # archive every bulletin capture
PYTHONPATH=pipeline python -m usdata build                        # parses bulletins, fits, backtests, publishes
```

```
GET /data/v1/visa-bulletin/facts.csv          every printed cutoff with source cell
GET /data/v1/visa-bulletin/forecast.json      per (chart, category, area): history + forecast quantiles + backtest
```

Site page `/green-card-wait/`: pick category and country, type a priority date, and see:
- "Current now" if it already is, with the bulletin cited;
- otherwise the median month and an 80% range ("most likely around Mar 2029; 8 in 10 simulated futures between
  Jul 2027 and Nov 2031"), plus the latest USCIS median I-485 time as the step after;
- a fan chart of the projected final action date with the visitor's priority date as a line;
- the backtest for that series: how often the 80% range contained the real crossing, and the typical error.

## Shape

### Data (ledger extension)

- New dataset id `state-vb`, publisher U.S. Department of State, parser `pipeline/usdata/datasets/visa_bulletin.py`,
  registered in `datasets/__init__.py` with `archived_period` = bulletin month `YYYY-MM` from the URL slug
  `visa-bulletin-for-<month>-<year>`. Add its folder prefixes to `wayback.PREFIXES`
  (`travel.state.gov/content/travel/en/legal/visa-law0/visa-bulletin/*` and the older
  `travel.state.gov/content/visas/en/law-and-policy/bulletin/*`). HTML only.
- Values are dates, not counts. Extend the domain instead of encoding dates as numbers:
  `Value = Reported | Absent | Cutoff(date) | Current`. Unavailable ("U") is `Absent("unavailable")`.
  `publish` writes Cutoff as ISO date in `value`, status `date`; Current as empty value, status `current`.
- RawObservation: labels `(chart, family_or_employment, printed_category, printed_area)`; period `Month` (the bulletin
  month); measure `final_action_date` or `filing_date`; window `month`; CellRef sheet `table N`, row/col 1-based.
- Chart detection: before October 2015 there is one chart per section (treat as final action). From October 2015,
  bulletins print "FINAL ACTION DATES" and "DATES FOR FILING" charts; classify each table by the nearest preceding
  heading text. Fail loudly if a family/employment table can't be classified.
- `catalog/visa-bulletin.toml` maps printed categories ("1st", "2A*", "Other Workers", "5th Unreserved...") and
  areas ("All Chargeability Areas Except Those Listed", "CHINA-mainland born", "EL SALVADOR GUATEMALA HONDURAS"...)
  to canonical ids. Same discipline as `uscis-forms.toml`: unknown label fails the build with a suggestion.
  Series id: `vb/<chart>/<category>/<area>` e.g. `vb/final/f4/philippines`.

### Forecast (pure module `pipeline/usdata/forecast.py`)

- Input per series: monthly `(bulletin_month, Cutoff | Current | None)`.
- Represent each month as **backlog days** = bulletin month first day minus cutoff (Current → 0; unavailable →
  missing). Movement per month = change in cutoff date in days (advance positive, retrogression negative).
- Two candidate models, both implemented, chosen by backtest (global choice, documented):
  - `pace`: cutoff advances at its mean monthly rate over the trailing 36 months; interval from the empirical
    distribution of past forecast errors at that horizon.
  - `bootstrap`: simulate N=2000 futures by resampling 12-month blocks of historical monthly movements from the
    trailing 120 months of the same series (seeded RNG, deterministic). Apply them to the latest cutoff.
- `crossing_quantiles(paths, priority_date) -> (q10, q50, q90 months)` uses first-crossing per path (running max is
  NOT taken: a retrogression after crossing still counts as crossed, matching how final action works in practice
  for the first approval opportunity). Paths that never cross within 240 months are reported as "beyond 20 years".
- Ship per series: last 120 months of history; forecast cutoff quantiles q10/q25/q50/q75/q90 for months 1..240;
  backtest metrics. The browser computes the visitor's crossing month from those quantiles (monotone approximation,
  noted on the page). Also ship, per series, a small precomputed table of crossing quantiles for priority dates at
  monthly steps across the plausible range so the page can interpolate exact first-crossing results instead of the
  approximation. Prefer the table; keep the file under ~2 MB total.
- `backtest(series, model)`: origins every month from 2008 to the latest month with at least 24 months of later
  history; for each origin, synthetic priority dates at cutoff + {6, 12, 24, 48} months; actual crossing month from
  the real history (right-censored if not yet crossed: count coverage only when the outcome is known or the
  interval's upper end has passed). Metrics per series and pooled: n, median absolute error (months) of the median,
  80% interval coverage. Determinism: same input → identical output.

### Site

- `site/src/pages/green-card-wait.astro` plus a small client script; reads `forecast.json` at build time for the
  select options and fetches the series JSON client-side (or inlines if small). Chart with the existing `lineChart`
  conventions (CSS tokens, 2px lines, the pending area style for the fan band). Accessible form controls, works at
  390px wide, table view of the numbers.
- Clear limits on the page: not legal advice; per-country limits and spillover make bulletins jump; the State
  Department can retrogress dates; a current date still needs USCIS or consular processing; cross-chargeability
  and category changes are out of scope.
- Add to nav as "Green card wait".

### Checks and tests

- Pins in `pipeline/tests/pins.toml` for two bulletin cells (one pre-2015 single chart, one recent final action),
  verified by an independent HTML text check.
- Forecast unit tests on synthetic series: a constant +30 days/month series must forecast the exact crossing month
  with a tight interval; a frozen series must report "beyond 20 years"; determinism test.
- Build fails on unparseable bulletin cells, unknown labels or unclassifiable charts.

## Tradeoffs accepted

- We accept a purely statistical model of bulletin movement, blind to visa demand, in exchange for one that can be
  backtested honestly from public data. A demand model would need NVC/USCIS inventory data we don't have for most
  categories.
- We accept precomputed tables (static site) in exchange for zero running cost.

## Alternatives considered

- Queue model from USCIS "I-130 awaiting visa availability" counts divided by annual visa supply. Covers family
  categories and five countries only, ignores NVC/consular inventory and per-country spillover; kept as context, not
  the forecast.
- Straight-line extrapolation alone: kept as the `pace` candidate so the backtest can show whether the bootstrap
  earns its complexity.

## Implementation reconciliation

Backtest, as built on 291 archived bulletins (December 2001 to July 2026; 2009-03, 2009-09 to 2009-11 and 2012-10
were never captured by the Internet Archive). Origins every month from January 2008 with at least 24 later months,
synthetic priority dates 6/12/24/48 months past the origin cutoff. Coverage is the share of judged trials whose real
first crossing fell inside the 10th to 90th percentile range; the error is the median absolute miss of the median
month, over trials whose crossing has happened.

### Calibration

As simulated, both models are overconfident: over all origins their nominal 80% ranges held the real date 63.0%
(pace, n 29,763) and 61.8% (bootstrap, n 32,957) of the time. So every simulated path is widened around the
per-month median, `paths' = median + s * (paths - median)`, by one scalar `s` per model (`forecast.widen`). Forecast
bands, the crossing table and the backtest all go through `widen`, so they stay consistent; `s = 1` is the paths
unchanged, and at `s = 1` the backtest reproduces the numbers above exactly.

- **Grid.** `s` in 1, 1.25, 1.5, 1.75, 2, 2.5, 3, 4 (`SPREADS`). The backtest simulates each origin once and
  widens those same paths for every `s`.
- **Split by origin.** Calibration: origins before 2016-01 (`CALIBRATE_BEFORE`). Held out: origins from 2016-01
  on. The split is on when a forecast was made. A calibration trial's outcome can be printed after 2016.
- **Fit.** For each model, `s` is the smallest grid value whose pooled calibration coverage reaches 80%, or the
  largest if none does (`forecast.calibrate`).
- **Choice.** The model whose held-out pooled coverage at its `s` is closest to 80%, to the whole point; then the
  lower held-out median error (`forecast.choose`).
- **Published.** `forecast.json` gives, per model, `s`, the calibration score and the held-out score, plus the
  calibration coverage at every grid value (`calibration_curve`). Each series carries its own calibration and
  held-out scores at each model's `s`. The page shows a series' held-out coverage when at least 30 of its held-out
  trials were judged (`MIN_JUDGED`); below that it says "too few cases to judge" and shows the pooled held-out
  score. 30 judged trials puts a 95% interval on an 80% hit rate at about 14 points either side. Below that a
  series' own number says little. 55 of the 150 live series fall under 30, 35 of them dates-for-filing series.

Calibration coverage by `s` (pooled):

| s | 1 | 1.25 | 1.5 | 1.75 | 2 | 2.5 | 3 | 4 |
|---|---|---|---|---|---|---|---|---|
| pace | 74.9% | 79.7% | 82.8% | 85.2% | 87.2% | 90.0% | 91.5% | 93.7% |
| bootstrap | 75.5% | 80.3% | 83.6% | 86.6% | 88.5% | 91.4% | 92.6% | 94.4% |

Result per model, at its fitted `s` (median error in months):

| Model | s | Calibration coverage (n) | Held-out coverage (n) | Held-out median error |
|---|---|---|---|---|
| pace (chosen) | 1.5 | 82.8% (11,606) | 67.0% (17,940) | 10.0 |
| bootstrap | 1.25 | 80.3% (11,948) | 60.9% (20,796) | 9.0 |

**Chosen model: `pace` with `s = 1.5`.** Its held-out coverage is 13 points from 80%, against 19 for bootstrap.
Like the model, the spread is recomputed on every build, so the page and `forecast.json` state what the data
picked.

Held-out scores by series and chart, at each model's `s`:

| Series | pace calibration (n) | pace held-out (n) | pace held-out error | bootstrap calibration (n) | bootstrap held-out (n) | bootstrap held-out error |
|---|---|---|---|---|---|---|
| Final action chart, all series | 82.8% (11,606) | 75.7% (12,259) | 9.0 | 80.3% (11,948) | 65.5% (12,568) | 8.0 |
| Dates for filing chart, all series | none | 48.2% (5,681) | 12.0 | none | 53.8% (8,228) | 10.0 |
| F2A, all other countries (final) | 89.0% (228) | 79.7% (148) | 6.0 | 75.4% (228) | 77.7% (148) | 5.0 |
| F4, Philippines (final) | 75.0% (356) | 56.2% (409) | 9.0 | 84.6% (356) | 67.7% (409) | 8.0 |
| EB-2, India (final) | 90.3% (330) | 99.4% (358) | 15.0 | 81.2% (330) | 92.8% (363) | 10.0 |
| EB-3, all other countries (final) | 98.5% (263) | 79.3% (58) | 17.0 | 89.0% (263) | 67.2% (58) | 9.0 |

What the numbers say:

- **Widening helps, but the 2008 to 2015 factor does not carry over to 2016 on.** Held-out coverage at `s = 1` is
  55.4% (pace) and 54.1% (bootstrap). At the fitted `s` it is 67.0% and 60.9%, still short of 80%. On the
  held-out origins pace would need about `s = 3` (81.2%), bootstrap about `s = 3` (80.8%).
- **Not a censoring artifact.** Held-out origins have shorter observation windows, so more of their trials stay
  unjudged. Judging calibration trials as if only 36 months had been observed leaves pace's calibration coverage at
  74.1% (against 74.9% with the full window), while held-out trials with the same window score 54.9%. Bulletins
  since 2016 are harder to forecast than those before.
- **The dates-for-filing chart has no calibration trials.** It begins in October 2015, and both models need two
  years of history, so its spread comes entirely from the final action chart. It scores worst held out (48.2% for
  pace). On the final action chart alone, pace's held-out coverage is 75.7%.
- **The median error moves a little with `s`.** The median path is the same for every `s`, but the median of
  first-crossing months over paths is not exactly the first crossing of the median path. Held-out median error for
  pace is 10.0 months at every grid value. For bootstrap it is 9.0 from `s = 1` to `s = 1.5`, then 8.0 from 1.75 on.
  The published error is the one at the fitted `s`. Pooled n also shifts with `s`, because an unresolved trial is
  judged only once its range's upper end has passed, and a wider range passes later.

Deviations from the spec above, and why:

- **Two output folders.** Facts are at `state-vb/facts.csv` (the normal per-dataset path), the forecast at
  `visa-bulletin/forecast.json`. The forecast index holds every series with its backtest; each series printed in the
  latest bulletin also gets `visa-bulletin/forecast/<chart>/<category>/<area>.json` (history, bands, crossing
  table), which the page fetches on selection. One file would be about 2.7 MB; split, the page loads about 20 KB per
  series. The folder totals 2.7 MB, over the ~2 MB target, because every series keeps 240 monthly bands.
- **Pace paths.** Pace has no simulation, so its "paths" are 200 quantile paths: path k adds the k-th quantile of
  past errors at every horizon. First crossings over them give the same crossing quantiles as reading the bands.
  Beyond the longest horizon with 12 past errors, the last error quantiles are widened in proportion to the horizon.
- **Backtest paths.** The bootstrap backtest uses 500 paths per origin (2,000 for the published forecast); 500
  resolve a 10th or 90th percentile to well under a month.
- **Backtest runs in a process pool.** Scoring eight spreads costs about 2.5 times the serial backtest (about 90 s
  against 35 s on a 12-core laptop), so `forecast.run` maps the (model, series) backtests over a
  `ProcessPoolExecutor`. Each task is seeded on its own, so the output is identical to a serial run and to any
  worker count. The forecast step takes about 28 s on 12 cores. A full `usdata build` takes about 55 s.
- **First crossing without a running maximum.** `first_crossings` takes the first month whose cutoff is past the
  priority date directly. The month is the same as the running-maximum version, and the code is cheaper.
- **Gaps.** A month printed "U", or with no archived bulletin, carries the last printed cutoff forward in model input
  (movement 0, then a jump). For actual crossings it counts as not crossed. When the latest bulletin prints "U"
  (EB-2 India in July 2026), the forecast starts from the last printed cutoff if that is under a year old.
- **Current** at a priority date counts as crossed. Backtest priority dates later than the origin month are skipped
  (they would still be in the future), so a Current series contributes no trials while it stays Current.
- **Live series only.** Series not printed in the latest bulletin (old EB-5 groupings, Schedule A) are backtested
  but not forecast.
- **Blank cell.** One cell is printed empty (March 2011, 5th Pilot Programs, Philippines). It is published as
  `Absent("blank")` with a `bulletin-blank-cell` warning instead of failing the build.
- **Section rows.** Three 2003 bulletins print "Employment-Based" as a row with values; the catalog lists it under
  `ignore`.
- **Dependency.** The pipeline now needs numpy (pinned in `pipeline/requirements.txt`).
