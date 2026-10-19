// The only module that reads public/data/v1. Runs at build time; every lookup that misses throws,
// so a page can't cite a number the ledger doesn't hold.
import { readFileSync } from "node:fs";
import { join } from "node:path";

// Resolved from the project root: bundling moves this module, so import.meta.url would point into dist/.
const DATA = join(process.cwd(), "public", "data", "v1") + "/";
const load = <T,>(rel: string): T => JSON.parse(readFileSync(DATA + rel, "utf8"));

export type Measure = "received" | "approved" | "denied" | "completed" | "pending" | "median_processing_months";
export type Window = "quarter" | "fytd" | "snapshot";
export type Status = "reported" | "estimate" | "dash" | "na" | "null" | "blank" | "withheld";

export interface Fact {
  series: string;
  period: string;
  measure: string;
  window: string;
  value: number | null;
  status: Status;
  sourceId: string;
  cell: string;
  raw: string;
}

export interface Series {
  id: string;
  code: string | null;
  category: string;
  title: string;
  note: string | null;
  facts: Fact[];
}

export interface Source {
  source_id: string;
  dataset: string;
  url: string;
  original_name: string;
  sha256: string;
  retrieved_at: string;
  retrieved_by: string;
  archive_path: string;
  archive_url: string;
  publisher: string;
  via?: string;
}

export interface Check {
  check: string;
  severity: "error" | "warn" | "info";
  subject: string;
  detail: string;
  cells: string[];
}

type RawSeries = Omit<Series, "facts"> & { facts: [string, string, string, number | null, Status, string, string, string][] };

const SERIES: Map<string, Series> = new Map(
  load<RawSeries[]>("uscis-forms/series.json").map((s) => [
    s.id,
    {
      ...s,
      facts: s.facts.map(([period, measure, window, value, status, sourceId, cell, raw]) => ({
        series: s.id, period, measure, window, value, status, sourceId, cell, raw,
      })),
    },
  ]),
);
const SOURCES: Map<string, Source> = new Map(load<Source[]>("sources.json").map((s) => [s.source_id, s]));

export const allSeries = (): Series[] => [...SERIES.values()];
export const checks = (): Check[] => load<Check[]>("checks.json");
export const sources = (): Source[] => [...SOURCES.values()];

export function series(id: string): Series {
  const s = SERIES.get(id);
  if (!s) throw new Error(`ledger has no series ${id}`);
  return s;
}

export function source(id: string): Source {
  const s = SOURCES.get(id);
  if (!s) throw new Error(`ledger has no source ${id}`);
  return s;
}

/** Stocks (pending) are snapshots; flows are quarter values unless a window is given. */
export function fact(s: string, p: string, m: Measure, w?: Window): Fact {
  const window = w ?? (m === "pending" ? "snapshot" : "quarter");
  const f = series(s).facts.find((x) => x.period === p && x.measure === m && x.window === window);
  if (!f) throw new Error(`ledger has no fact ${s} ${p} ${m} ${window}`);
  if (f.value === null) throw new Error(`fact ${s} ${p} ${m} is ${f.status}, not a number; cite it explicitly`);
  return f;
}

export const value = (s: string, p: string, m: Measure, w?: Window): number => fact(s, p, m, w).value as number;

export function change(s: string, m: Measure, from: string, to: string): number {
  return value(s, to, m) / value(s, from, m) - 1;
}

export function periods(s: string, m: Measure): string[] {
  const window = m === "pending" ? "snapshot" : "quarter";
  return series(s).facts.filter((f) => f.measure === m && f.window === window && f.value !== null).map((f) => f.period).sort();
}

export const latestPeriod = (): string => periods("total", "received").at(-1)!;

// ---- I-130 visa queue and State Dept issuances (compact tables) ----
export type AwaitingRow = { country: string; pref: string; period: string; value: number | null; sourceId: string; cell: string };
export const awaiting = (): AwaitingRow[] =>
  load<[string, string, string, number | null, string, string, string][]>("uscis-i130-awaiting/compact.json").map(
    ([country, pref, period, value, _status, sourceId, cell]) => ({ country, pref, period, value, sourceId, cell }),
  );

export type IssuanceRow = { month: string; country: string; visaClass: string; value: number | null; sourceId: string; cell: string };
export const issuances = (): IssuanceRow[] =>
  load<[string, string, string, number | null, string, string][]>("state-iv/compact.json").map(
    ([month, country, visaClass, value, sourceId, cell]) => ({ month, country, visaClass, value, sourceId, cell }),
  );

// ---- formatting ----
export const fmt = (n: number): string =>
  Number.isInteger(n) ? n.toLocaleString("en-US") : n.toLocaleString("en-US", { maximumFractionDigits: 1 });
export const pct = (x: number, digits = 0): string => `${Math.abs(x * 100).toFixed(digits)}%`;
export const compact = (n: number): string =>
  n >= 1e6 ? `${(n / 1e6).toFixed(n >= 1e7 ? 1 : 2)} million` : n.toLocaleString("en-US");

/** FY2026Q3 -> "April–June 2026". Federal fiscal years start October 1. */
export function quarterLabel(p: string): string {
  const m = /^FY(\d{4})Q([1-4])$/.exec(p);
  if (!m) return p;
  const fy = +m[1], q = +m[2];
  const year = q === 1 ? fy - 1 : fy;
  return `${["Oct–Dec", "Jan–Mar", "Apr–Jun", "Jul–Sep"][q - 1]} ${year}`;
}

export function quarterEnd(p: string): Date {
  const m = /^FY(\d{4})Q([1-4])$/.exec(p)!;
  const fy = +m[1], q = +m[2];
  return q === 1 ? new Date(Date.UTC(fy - 1, 11, 31)) : new Date(Date.UTC(fy, [2, 5, 8][q - 2] + 1, 0));
}

export function sourceHref(f: { sourceId: string }): string {
  return source(f.sourceId).archive_url;
}

/** First and last quarter with any USCIS service-wide figure. Coverage text is derived from these, never typed. */
export const firstPeriod = (): string => periods("total", "received")[0];

/** Calendar years spanned by the USCIS series, e.g. 13 for FY2013Q1–FY2026Q3. */
export function yearsCovered(): number {
  const q = (p: string) => +p.slice(2, 6) * 4 + +p.slice(-1);
  return Math.round((q(latestPeriod()) - q(firstPeriod()) + 1) / 4);
}

/**
 * How far back you have to go to find a value as low (or as high) as this one.
 * Returns the most recent earlier quarter that matched or beat it, or null if none did since records begin.
 */
export function recordSince(s: string, m: Measure, p: string, dir: "low" | "high"): string | null {
  const v = value(s, p, m);
  const earlier = periods(s, m).filter((x) => x < p).reverse();
  return earlier.find((x) => (dir === "low" ? value(s, x, m) <= v : value(s, x, m) >= v)) ?? null;
}

/** The quarters where a measure is missing between first and last, e.g. reports USCIS never published. */
export function gaps(s: string, m: Measure): string[] {
  const have = new Set(periods(s, m));
  const all = periods(s, m);
  const q = (p: string) => +p.slice(2, 6) * 4 + (+p.slice(-1) - 1);
  const out: string[] = [];
  for (let i = q(all[0]); i <= q(all.at(-1)!); i++) {
    const p = `FY${Math.floor(i / 4)}Q${(i % 4) + 1}`;
    if (!have.has(p)) out.push(p);
  }
  return out;
}

// ---- Visa Bulletin cutoffs and the green card wait forecast ----
export type BulletinRow = [month: string, value: string | null, status: "date" | "current" | "unavailable" | "blank", sourceId: string, cell: string, raw: string];
export interface Score { n: number; coverage80: number | null; n_error: number; median_abs_error: number | null }
export type ModelName = "pace" | "bootstrap";
export interface BulletinSeries {
  id: string;
  chart: "final" | "filing";
  section: "family" | "employment";
  category: string;
  category_title: string;
  area: string;
  area_title: string;
  title: string;
  description: string | null;
  first: string;
  latest: BulletinRow;
  path: string | null;
  backtest: Record<ModelName, Score>;
}
export interface ForecastIndex {
  latest: string;
  model: ModelName;
  method: { horizon_months: number; bootstrap_paths: number; pace_levels: number; backtest_bootstrap_paths: number; band_levels: number[]; backtest_from: string; backtest_leads_months: number[]; dates: string };
  pooled: Record<ModelName, Score>;
  series: BulletinSeries[];
}
export interface SeriesForecast {
  id: string;
  history: BulletinRow[];
  forecast: { model: ModelName; origin: string; cutoff: string; bands: number[][]; crossings: [number, number | null, number | null, number | null][] };
}
export const forecastIndex = (): ForecastIndex => load<ForecastIndex>("visa-bulletin/forecast.json");
export const seriesForecast = (path: string): SeriesForecast => load<SeriesForecast>(`visa-bulletin/${path}`);
