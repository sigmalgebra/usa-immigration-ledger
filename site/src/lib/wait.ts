// Pure lookups for /green-card-wait/, shared by the build (default view) and the browser. No fetching here.
import type { FanSpec } from "./chart";
import type { SeriesForecast } from "./ledger";

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
