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
