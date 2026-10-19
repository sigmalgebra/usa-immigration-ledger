// Pure lookups for /green-card-wait/, shared by the build (default view) and the browser. No fetching here.
import type { FanSpec } from "./chart";
import type { ChartName, Fit, ModelName, Score, SeriesForecast } from "./ledger";

const DAY = 864e5;
export const toDays = (iso: string): number => Math.round(Date.parse(iso + "T00:00:00Z") / DAY);
export const monthMs = (m: string): number => Date.parse(m + "-01T00:00:00Z");
export const addMonths = (m: string, k: number): string => {
  const i = +m.slice(0, 4) * 12 + +m.slice(5, 7) - 1 + k;
  return `${Math.floor(i / 12)}-${String((i % 12) + 1).padStart(2, "0")}`;
};
export const monthLabel = (m: string): string =>
  new Date(monthMs(m)).toLocaleDateString("en-US", { month: "short", year: "numeric", timeZone: "UTC" });
export const dateLabel = (iso: string): string =>
  new Date(iso + "T00:00:00Z").toLocaleDateString("en-US", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });

export const pct = (x: number | null): string => (x === null ? "—" : `${Math.round(x * 100)}%`);
export const months = (x: number | null): string => (x === null ? "—" : x.toLocaleString("en-US", { maximumFractionDigits: 1 }));
const count = (n: number): string => n.toLocaleString("en-US");
export const MODEL_LABEL: Record<ModelName, string> = { pace: "recent pace", bootstrap: "resampled history" };
export const CHART_LABEL: Record<ChartName, string> = { final: "final action", filing: "dates for filing" };
export const chartScope = (chart: ChartName): string => `all categories and countries, ${CHART_LABEL[chart]} chart`;
/** Said under every dates-for-filing result: the forecast uses the later cutoff of the two charts. */
export const FILING_EITHER_CHART = "A date can be filed once it passes either chart, so the dates-for-filing estimate is never later than the final action one.";
export const widened = (f: Fit): string => `×${f.spread.toLocaleString("en-US")}`;

/** How a series' held-out forecasts did: its own score once at least `minJudged` were judged, else every series
 * on its chart pooled, said as such. */
export function trackRecord(name: string, chart: ChartName, own: Score, pooled: Score, minJudged: number) {
  const enough = own.n >= minJudged;
  const s = enough ? own : pooled;
  return {
    enough,
    title: enough ? name : chartScope(chart),
    coverage: pct(s.coverage80),
    coverageNote: enough
      ? `of ${count(own.n)} forecasts judged`
      : `${name} has too few cases to judge (${count(own.n)} judged, under ${minJudged})`,
    error: `${months(s.median_abs_error)} months`,
    errorNote: `median over ${count(s.n_error)} forecasts whose date has arrived`,
  };
}

export function otherMethod(model: ModelName, f: Fit, minJudged: number, checkFrom: string): string {
  const label = `${MODEL_LABEL[model]}, widened ${widened(f)}`;
  return f.held_out.n < minJudged
    ? `For this series the other method (${label}) has too few cases to judge.`
    : `For this series the other method (${label}) held the real date ${pct(f.held_out.coverage80)} of the time, over ${count(f.held_out.n)} forecasts made from ${checkFrom} on.`;
}

export type Wait =
  | { kind: "current" }
  | { kind: "unavailable" } // passed by the last printed cutoff, but this month's bulletin prints "U"
  | { kind: "future" }
  | { kind: "forecast"; q10: string | null; q50: string | null; q90: string | null };

/** Months after the forecast origin, from the precomputed first-crossing table. Between two tabulated priority
 * dates each quantile is interpolated; when either side is beyond the horizon, the later (slower) side is used. */
export function wait(s: SeriesForecast, priority: string): Wait {
  const p = toDays(priority);
  const f = s.forecast;
  if (p < toDays(f.cutoff)) return s.history.at(-1)![2] === "unavailable" ? { kind: "unavailable" } : { kind: "current" };
  if (p >= toDays(addMonths(f.origin, 1) + "-01")) return { kind: "future" };
  const rows = f.crossings;
  let i = rows.findIndex((r) => r[0] > p);
  if (i === -1) i = rows.length;
  const lo = rows[Math.max(0, i - 1)], hi = rows[Math.min(rows.length - 1, i)];
  const frac = hi[0] === lo[0] ? 0 : (p - lo[0]) / (hi[0] - lo[0]);
  const q = (k: 1 | 2 | 3): string | null => {
    const a = lo[k], b = hi[k];
    const m = a === null || b === null ? b : Math.round(a + (b - a) * frac);
    return m === null ? null : addMonths(f.origin, m);
  };
  return { kind: "forecast", q10: q(1), q50: q(2), q90: q(3) };
}

export function fanSpec(s: SeriesForecast, priority: string | null, cite: (sourceId: string, cell: string) => string, horizon?: number): FanSpec {
  const f = s.forecast;
  const n = Math.min(f.bands.length, horizon ?? f.bands.length);
  return {
    history: s.history.map(([month, value, status, sourceId, cell]) => ({
      x: monthMs(month),
      y: status === "current" ? monthMs(month) : value ? Date.parse(value + "T00:00:00Z") : null,
      current: status === "current",
      label: monthLabel(month),
      cite: cite(sourceId, cell),
    })),
    bands: f.bands.slice(0, n).map((b, k) => {
      const [q10, q25, q50, q75, q90] = b.map((d) => d * DAY);
      return { x: monthMs(addMonths(f.origin, k + 1)), q10, q25, q50, q75, q90 };
    }),
    priority: priority ? toDays(priority) * DAY : null,
  };
}

/** Months to show ahead: through the slow end of the range plus a year, at least 3 years, at most the horizon. */
export function horizonFor(s: SeriesForecast, w: Wait | null): number {
  const max = s.forecast.bands.length;
  if (!w || w.kind !== "forecast") return Math.min(max, 120);
  if (w.q90 === null) return max;
  const k = (+w.q90.slice(0, 4) * 12 + +w.q90.slice(5, 7)) - (+s.forecast.origin.slice(0, 4) * 12 + +s.forecast.origin.slice(5, 7));
  return Math.max(36, Math.min(max, k + 12));
}
