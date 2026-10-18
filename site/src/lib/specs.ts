// Build-time: turn ledger series into chart specs. Missing quarters become null points so lines break
// at gaps (quarters a form wasn't reported) instead of drawing a straight line across them.
import type { LineSeries, LineSpec, Point } from "./chart";
import { quarterEnd, quarterLabel, series, source, type Measure } from "./ledger";

const MEASURE_LABEL: Record<string, string> = {
  received: "Received",
  completed: "Completed",
  approved: "Approved",
  denied: "Denied",
  pending: "Pending",
  median_processing_months: "Median processing time",
};

function quarterRange(from: string, to: string): string[] {
  const parse = (p: string) => { const m = /^FY(\d{4})Q(\d)$/.exec(p)!; return +m[1] * 4 + (+m[2] - 1); };
  const out: string[] = [];
  for (let i = parse(from); i <= parse(to); i++) out.push(`FY${Math.floor(i / 4)}Q${(i % 4) + 1}`);
  return out;
}

export function seriesSpec(id: string, measures: Measure[], opts: Partial<LineSpec> = {}): LineSpec {
  const s = series(id);
  const lines: LineSeries[] = measures.map((m, i) => ({ key: m, label: MEASURE_LABEL[m], slot: i + 1 }));
  const facts = s.facts.filter((f) => measures.includes(f.measure as Measure) && f.window === (f.measure === "pending" ? "snapshot" : "quarter"));
  const have = facts.filter((f) => f.value !== null).map((f) => f.period).sort();
  const points: Point[] = [];
  if (have.length) {
    for (const m of measures) {
      for (const p of quarterRange(have[0], have.at(-1)!)) {
        const f = facts.find((x) => x.period === p && x.measure === m);
        points.push({
          x: quarterEnd(p).toISOString(), y: f?.value ?? null, s: m, label: quarterLabel(p),
          cite: f ? `${source(f.sourceId).original_name}, ${f.cell}` : "not published",
        });
      }
    }
  }
  return { points, series: lines, unit: measures[0] === "median_processing_months" ? "months" : "count", ...opts };
}
