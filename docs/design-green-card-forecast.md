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

As simulated, both models are overconfident: over all origins their nominal 80% ranges held the real date 63.9%
(pace, n 29,787) and 63.4% (bootstrap, n 33,053) of the time. So every simulated path is widened around the
per-month median, `paths' = median + s * (paths - median)`, by one scalar `s` per model (`forecast.widen`). Forecast
bands, the crossing table and the backtest all go through `widen`, so they stay consistent; `s = 1` is the paths
unchanged, and at `s = 1` the backtest reproduces the numbers above exactly.

- **Grid.** `s` in 1, 1.25, 1.5, 1.75, 2, 2.5, 3, 4 (`SPREADS`). The backtest simulates each origin once and
  widens those same paths for every `s`.
- **Split by origin.** Calibration: origins before 2016-01 (`CALIBRATE_BEFORE`). Held out: origins from 2016-01
  on. The split is on when a forecast was made. A calibration trial's outcome can be printed after 2016.
- **Fit.** For each model, `s` is the smallest grid value at which every calibration origin year (2008 to 2015)
  reaches 75% coverage (`WORST_YEAR_TARGET`), or the largest if none does (`forecast.calibrate`). A year's
  coverage is over the judged trials whose origin falls in that calendar year, final action and filing charts
  pooled, the same pooling as the published held-out scores. A year with fewer than 200 judged trials
  (`MIN_YEAR_JUDGED`) is ignored: at 200, a 95% interval on a 75% hit rate is about 6 points either side. Every
  origin year from 2008 to 2024 has more than 1,000 judged trials at the fitted `s`, so none is ignored today.
- **Choice.** The model whose worst held-out year (2016 on, same 200-trial minimum) has the higher coverage at its
  `s`; then the lower held-out pooled median error (`forecast.choose`).
- **Published.** `forecast.json` gives, per model, `s`, the calibration and held-out pooled scores, the worst
  held-out year (`worst_held_out`), and the score for every origin year at that `s` (`by_year`, each with its
  judged n). `by_chart` gives the same block for the chosen model at its `s`, computed separately over the
  final action series (`vb/final/...`) and the dates-for-filing series (`vb/filing/...`). It does not feed model
  choice or calibration, which still pool both charts. `first_rule` gives each model at the spread the first rule
  picked, with its scores, and `calibration_curve` gives, per grid value, the pooled calibration coverage and the
  worst calibration year's coverage. Each series carries its own calibration and held-out scores at each model's
  `s`. The page shows a series' held-out coverage when at least 30 of its held-out trials were judged
  (`MIN_JUDGED`); below that it says "too few cases to judge" and shows the held-out score of all series on the
  same chart. 30 judged trials puts a 95% interval on an 80% hit rate at about 14 points either side. 55 of the 150
  live series fall under 30.
- **Per chart on the page.** The headline sentence, the "all categories and countries" tile, the too-few-cases
  fallback and the year-by-year table follow the selected chart, from `by_chart`. Both charts' versions are
  rendered at build time and the inactive one is hidden, so switching charts in the browser swaps them without
  recomputing. The filing headline adds that the chart began in October 2015, so no filing forecast helped choose
  the widening, and that its record is weaker than the final action chart's. The methods table keeps the combined
  numbers, since model choice and calibration use them.

#### The first rule, and why it was replaced

The first rule fit `s` as the smallest grid value whose *pooled* 2008 to 2015 coverage reached 80%
(`AVERAGE_TARGET`, kept as `forecast.calibrate_average` only to publish how it held up). It picked `s = 1.5` for
pace and `s = 1.25` for bootstrap, and the old choice (held-out coverage closest to 80%) picked pace. From 2016
on, pace's range then held the real date only 68.4% of the time (n 17,972), bootstrap's 63.5% (n 20,927). The
pooled 2008 to 2015 average hides weak years: at pace `s = 1.5`, 2010 scored 61.0% while 2014 scored 92.4%.
Bulletins after 2019 move much more than before, and a fit to the average of calmer years does not carry over. A
recent window does not fix it either: fitting the same 80% average on final action origins from 2016 to 2019 picks
pace `s = 1.25`, which holds 61.7% on origins from 2020 on (n 6,143).

After seeing those held-out numbers we switched to the worst-year rule, and chose between the models by their
worst held-out year. Both decisions used results from 2016 on, so the 2016-onward figures below are not a fully
blind test. The page says so.

Calibration coverage by `s`, pooled and worst calibration year (2008 to 2015):

| s | 1 | 1.25 | 1.5 | 1.75 | 2 | 2.5 | 3 | 4 |
|---|---|---|---|---|---|---|---|---|
| pace, pooled | 74.9% | 79.7% | 82.8% | 85.2% | 87.2% | 90.0% | 91.5% | 93.7% |
| pace, worst year | 52.1% | 58.3% | 61.0% | 63.6% | 66.2% | 70.8% | 73.7% | 78.3% |
| bootstrap, pooled | 75.5% | 80.3% | 83.6% | 86.6% | 88.5% | 91.4% | 92.6% | 94.4% |
| bootstrap, worst year | 51.1% | 56.8% | 60.6% | 64.9% | 68.1% | 74.5% | 78.0% | 83.2% |

The worst calibration year is 2010 at every grid value for both models.

Result per model at its worst-year `s`, from this build (median error in months). The "final" columns score only
the final action chart, the scope of the reviewer's experiment, and reproduce it exactly. For the chosen model they
match `by_chart.final` in `forecast.json`.

| Model | s | Calibration (n) | Held-out, both charts (n) | Worst held-out year, both charts | Held-out error | Held-out, final (n) | Worst held-out year, final | Held-out error, final |
|---|---|---|---|---|---|---|---|---|
| pace (chosen) | 4 | 93.7% (11,602) | 86.4% (17,500) | 76.8% (2023) | 10.0 | 91.0% (12,060) | 80.1% (2023) | 9.0 |
| bootstrap | 3 | 92.6% (11,946) | 81.1% (20,474) | 62.4% (2021) | 8.0 | 82.1% (12,414) | 59.2% (2021) | 8.0 |

**Chosen model: `pace` with `s = 4`.** Its worst held-out year (76.8%) beats bootstrap's (62.4%), so the
median-error tiebreak, which would favor bootstrap, does not come into play. The same choice holds on the final
action chart alone (80.1% against 59.2%). `s = 4` is the top of the grid: pace's worst calibration year only
clears 75% there. Like the model, the spread is recomputed on every build, so the page and `forecast.json` state
what the data picked.

Pace at `s = 4`, coverage by origin year (judged n), as published:

| Year | 2008 | 2009 | 2010 | 2011 | 2012 | 2013 | 2014 | 2015 |
|---|---|---|---|---|---|---|---|---|
| Both charts (fit) | 91.8% (1,200) | 89.2% (1,020) | 78.3% (1,526) | 96.9% (1,721) | 96.1% (1,556) | 99.2% (1,610) | 97.8% (1,543) | 97.9% (1,426) |

| Year | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 |
|---|---|---|---|---|---|---|---|---|---|
| Both charts (check) | 94.0% (1,515) | 93.1% (1,518) | 90.3% (1,578) | 91.6% (1,843) | 90.6% (2,615) | 77.1% (2,013) | 85.9% (1,918) | 76.8% (2,894) | 85.6% (1,606) |
| Final action (`by_chart.final`) | 94.0% (1,515) | 93.1% (1,518) | 90.3% (1,578) | 90.2% (1,542) | 96.5% (1,428) | 94.4% (1,010) | 98.6% (978) | 80.1% (1,615) | 84.3% (876) |
| Dates for filing (`by_chart.filing`) | | | | 98.7% (301) | 83.5% (1,187) | 59.8% (1,003) | 72.7% (940) | 72.6% (1,279) | 87.3% (730) |

Through 2019 the filing chart adds few trials and the rows agree. From 2020 on it is close to half of each year's
trials and scores below the final action chart in every year but 2024, which pulls the pooled years down. The filing chart has no
judged held-out trials before 2019.

Pooled held-out scores as published, pace at `s = 4`, and what the page shows for each chart:

| Scope | Held-out (n) | Worst held-out year | Held-out error | Shown |
|---|---|---|---|---|
| Final action | 91.0% (12,060) | 80.1% (2023) | 9.0 | headline, tile and year table when the final action chart is selected |
| Dates for filing | 76.0% (5,440) | 59.8% (2021) | 13.0 | the same when the filing chart is selected, with the sentence on why it is weaker |
| Both charts | 86.4% (17,500) | 76.8% (2023) | 10.0 | methods table only |

### Dates for filing: the effective cutoff

**Bug.** Each chart was forecast on its own, from its own trailing 36-month pace. The dates-for-filing forecast
could then come out later than the final action forecast for the same category, country and priority date. F1,
all other countries, priority date 2021-12-12, from the July 2026 bulletin: final action (cutoff 2018-02-01) said
most likely April 2030, range May 2027 to beyond 20 years. Dates for filing (cutoff 2019-01-01, ahead of final
action) said most likely July 2037, range December 2026 to beyond 20 years. The F1 filing cutoff stood at
2017-09-01 from mid-2023 to March 2026 while final action moved from December 2014 to February 2018, so the
filing pace was the slower one and its projection fell behind final action's.

**Rule.** A date can be filed once it is current under either chart. If the final action date passes it, it can
certainly be filed. So a filing series is forecast against its effective cutoff, the later of the two charts in
each month. Printed history mostly agrees already. In 5,352 category, country and month cells the filing chart
prints a date and final action prints a date or Current. The filing date is behind final action in 183 of them,
177 where final action is Current and 6 where it prints a later date.

**As built.**
- `forecast.effective(filing, final)` takes the larger cutoff, path by path. It runs on the *widened* paths of
  each chart, each widened around its own median by the model's `s`. Taking the maximum first and then widening
  the result around its own median could push a filing path below the final action path it was paired with, and
  the published filing range could again start later than final action's. Taking it after widening keeps every
  filing path at or above a published final action path. So every filing band quantile is at or above final
  action's, and every crossing quantile is at or before it.
- The final action paths are those of the final series' own forecast: same origin, same seed
  (`_seed(<final id>, origin, model)`), same path count. The filing forecast's `cutoff` is the later of the two
  printed cutoffs, so a date already passed by final action reads as current for filing too, and the crossing
  table starts there.
- **Pairing.** Paths are paired by the rank of their mean cutoff, a comonotonic coupling. Pace paths are quantile
  paths ordered by error rank, so this pairs path k with path k. Pace's results are identical to pairing by
  index (checked: the build gave the same pace numbers both ways). Bootstrap paths are independent draws.
  Pairing them by index takes the larger of two independent futures, which runs early. Bootstrap's held-out
  filing coverage fell from 78.6% to 61.2% that way (worst year 36.0%). Pairing by rank gives 79.6% (n 8,060,
  worst year 65.9%). The coupling assumes the two charts move together, which their history supports.
- **Backtest.** For a filing series, the actual crossing is the first later month in which either chart's
  printed cutoff passes the priority date (`_either`). The simulation at each origin takes the same maximum, with
  final action paths simulated from final action data up to that origin only.
- **Unchanged.** Model choice and spread calibration logic. Calibration has no filing trials (the chart began in
  October 2015), so both spreads, the calibration curve and every final action number stay as they were. Final
  action held out: 91.0% (n 12,060), worst year 80.1% (2023), median error 9.0 months. Pace is still chosen.

**Result.** Filing held out, pace at `s = 4`, is now 76.0% (n 5,440, was 71.0%). The worst year is 2021 at 59.8%
(was 58.2%). The median error is 13.0 months, unchanged, over 3,743 known crossings (was 3,110, since a crossing
on either chart now counts). The reported case now reads most likely April 2030, range December 2026 to beyond
20 years, for dates for filing. Final action reads April 2030, range May 2027 to beyond 20 years. The page says
under every filing forecast that a date can be filed once it passes either chart.
`test_published_filing_forecasts_are_never_later_than_final_action` checks every published filing series against
its final action series. Bands must be at or above, and crossing quantiles at or before, at every priority date
both tables hold, with beyond the horizon counted as infinity.

### Fan chart view

With `s = 4` the 10th-percentile cutoff can fall decades below anything printed (F4 Philippines final action: down
to September 1986 within the window shown, against a lowest printed cutoff of February 1993 since 2016). The
crossing math is unaffected, but the chart looked absurd. `fanChart` now sets the y domain's lower end to the
earlier of the lowest cutoff printed in the last 10 years of shown history and the visitor's priority date, minus
5% of the span to the top, and never below the data's own minimum. The upper end is the data's maximum, as
before. The bands, median and history lines clip at the frame (`clip: "frame"`), so the range is cut at the lower
edge rather than rescaled. The chart subtitle says the lower part of the range is cropped. No data, band or
crossing changed; the "Show the numbers" table still lists the full range.

Held-out scores by series and chart, at each model's `s`:

| Series | pace calibration (n) | pace held-out (n) | pace held-out error | bootstrap calibration (n) | bootstrap held-out (n) | bootstrap held-out error |
|---|---|---|---|---|---|---|
| Final action chart, all series | 93.7% (11,602) | 91.0% (12,060) | 9.0 | 92.6% (11,946) | 82.1% (12,414) | 8.0 |
| Dates for filing chart, all series | none | 76.0% (5,440) | | none | 79.6% (8,060) | |
| F2A, all other countries (final) | 96.9% (228) | 98.7% (148) | 6.0 | 89.5% (228) | 83.8% (148) | 5.0 |
| F4, Philippines (final) | 91.3% (356) | 82.2% (409) | 9.0 | 93.5% (356) | 85.6% (409) | 7.0 |
| EB-2, India (final) | 96.4% (330) | 100.0% (358) | 15.0 | 78.5% (330) | 91.2% (363) | 12.0 |
| EB-3, all other countries (final) | 99.6% (263) | 98.3% (58) | 18.0 | 95.4% (263) | 82.8% (58) | 8.0 |

What the numbers say:

- **The range is wide on purpose.** At `s = 4`, pace's range is four times as wide around the median as
  simulated. That is what it took for every year from 2008 to 2015 to reach 75%. From 2016 on it held 86.4%
  pooled, with no year below 76.8%. The page states the measured numbers for the selected chart instead of
  promising 80%.
- **Not a censoring artifact.** Held-out origins have shorter observation windows, so more of their trials stay
  unjudged. Judging calibration trials as if only 36 months had been observed leaves pace's calibration coverage at
  `s = 1` at 74.1% (against 74.9% with the full window), while held-out trials with the same window score 54.9% (measured before the filing fix below, which
  changed only filing trials).
  Bulletins since 2016 are harder to forecast than those before.
- **The dates-for-filing chart has no calibration trials.** It begins in October 2015, and both models need two
  years of history, so its spread comes entirely from the final action chart. It scores worst held out (76.0% for
  pace) and pulls the pooled worst year (2023) down to 76.8%, against 80.1% for final action alone. That is why
  the page reports each chart on its own: final action holds 91.0% with no year below 80.1%, filing 76.0% with
  2021 at 59.8%.
- **The median error moves a little with `s`.** The median path is the same for every `s`, but the median of
  first-crossing months over paths is not exactly the first crossing of the median path. Pooled held-out median
  error for pace is 10.0 months at every grid value. For bootstrap it is 9.0 at `s = 1` and 1.25, then 8.0
  from 1.5 on. Pooled n also shifts with `s`, because an unresolved trial is judged only once its range's upper
  end has passed, and a wider range passes later.

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
- **No 80% promise.** The spec above says "an 80% range". The range is still the 10th to 90th percentile of the
  widened paths, but the page calls it "the range" or "the likely range" and states its measured coverage since
  2016 instead, with a year-by-year table for the chosen model and the disclosure above.
