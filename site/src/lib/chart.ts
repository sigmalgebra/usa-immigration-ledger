// One chart spec shared by the build (static SVG via linkedom) and the browser (same chart plus hover).
// Colors come from CSS tokens through class names: SVG presentation attributes can't read var().
import * as Plot from "@observablehq/plot";

export interface LineSeries { key: string; label: string; slot: number }
export interface Point { x: string; y: number | null; s: string; label: string; cite: string }
export interface LineSpec {
  points: Point[];
  series: LineSeries[];
  unit: "count" | "months";
  height?: number;
  area?: boolean; // single-series stock: a 10% wash under the line
}

const tick = (unit: LineSpec["unit"]) => (d: number) =>
  unit === "months" ? `${d}` : d >= 1e6 ? `${+(d / 1e6).toFixed(1)}M` : d >= 1e3 ? `${+(d / 1e3).toFixed(0)}K` : `${d}`;

const full = (unit: LineSpec["unit"], y: number) =>
  unit === "months" ? `${y.toLocaleString("en-US", { maximumFractionDigits: 1 })} months` : y.toLocaleString("en-US");

export function lineChart(spec: LineSpec, opts: { width: number; document?: Document; interactive?: boolean }) {
  const pts = spec.points.map((p) => ({ ...p, date: new Date(p.x) }));
  const solid = pts.filter((p) => p.y !== null);
  const last = spec.series.map((s) => solid.filter((p) => p.s === s.key).at(-1)).filter(Boolean) as typeof solid;
  const marks: Plot.Markish[] = [
    Plot.gridY({ className: "grid", strokeOpacity: 1 }),
    Plot.ruleY([0], { className: "baseline", strokeOpacity: 1 }),
  ];
  for (const s of spec.series) {
    const data = pts.filter((p) => p.s === s.key);
    if (spec.area) {
      // Areas don't break at nulls like lines do, so each run of published quarters is its own segment.
      let seg = 0;
      const runs = data.flatMap((p) => (p.y === null ? (seg++, []) : [{ ...p, seg }]));
      marks.push(Plot.areaY(runs, { x: "date", y: "y", z: "seg", className: `wash s${s.slot}`, curve: "linear" }));
    }
    marks.push(Plot.lineY(data, { x: "date", y: "y", className: `line s${s.slot}`, strokeWidth: 2, curve: "linear" }));
  }
  // End dots with a 2px surface ring; the endpoint is the one value labeled directly.
  for (const s of spec.series) {
    marks.push(Plot.dot(last.filter((d) => d.s === s.key), { x: "date", y: "y", r: 4, className: `enddot s${s.slot}` }));
  }
  marks.push(
    Plot.text(last, {
      x: "date", y: "y", text: (d) => full(spec.unit, d.y as number), dx: 8, textAnchor: "start", className: "endlabel",
    }),
  );
  if (opts.interactive) {
    marks.push(Plot.ruleX(solid, Plot.pointerX({ x: "date", className: "crosshair" })));
    marks.push(
      Plot.tip(solid, Plot.pointer({
        x: "date", y: "y", className: "tip",
        title: (d) => `${d.label}\n${spec.series.find((s) => s.key === d.s)!.label}: ${full(spec.unit, d.y)}\nSource: ${d.cite}`,
      })),
    );
  }
  return Plot.plot({
    document: opts.document,
    width: opts.width,
    height: spec.height ?? 280,
    marginLeft: 44,
    marginRight: 110,
    marginTop: 16,
    style: { background: "transparent", fontFamily: "inherit", fontSize: "12px", overflow: "visible" },
    x: { type: "utc", label: null, ticks: 6, tickSize: 0 },
    y: { label: null, tickFormat: tick(spec.unit), ticks: 5, tickSize: 0, nice: true, zero: true },
    marks,
  });
}

// ---- Visa Bulletin fan chart: printed cutoffs, then the forecast's 10–90% and 25–75% bands and median ----
export interface FanSpec {
  history: { x: number; y: number | null; current: boolean; label: string; cite: string }[]; // ms; y null = unavailable
  bands: { x: number; q10: number; q25: number; q50: number; q75: number; q90: number }[];
  priority: number | null;
}

const monthYear = (ms: number) => new Date(ms).toLocaleDateString("en-US", { month: "short", year: "numeric", timeZone: "UTC" });

export function fanChart(spec: FanSpec, opts: { width: number; document?: Document; interactive?: boolean }) {
  const hist = spec.history.map((p) => ({ ...p, date: new Date(p.x), cut: p.y === null ? null : new Date(p.y) }));
  const band = spec.bands.map((b) => ({
    date: new Date(b.x), q10: new Date(b.q10), q25: new Date(b.q25), q50: new Date(b.q50), q75: new Date(b.q75), q90: new Date(b.q90),
  }));
  const marks: Plot.Markish[] = [
    Plot.gridY({ className: "grid", strokeOpacity: 1 }),
    Plot.areaY(band, { x: "date", y1: "q10", y2: "q90", className: "wash s1", curve: "linear" }),
    Plot.areaY(band, { x: "date", y1: "q25", y2: "q75", className: "wash s1", curve: "linear" }),
    Plot.lineY(band, { x: "date", y: "q50", className: "line s1 median", strokeWidth: 2, strokeDasharray: "4 3", curve: "linear" }),
    Plot.lineY(hist, { x: "date", y: "cut", className: "line s1", strokeWidth: 2, curve: "step-after", defined: (d) => d.cut !== null }),
  ];
  if (spec.priority !== null) {
    const pd = new Date(spec.priority);
    marks.push(Plot.ruleY([pd], { className: "pd-rule", strokeWidth: 2 }));
    marks.push(Plot.text([pd], { x: band.at(-1)?.date ?? hist.at(-1)!.date, y: (d) => d, text: () => "Your priority date", dy: -8, textAnchor: "end", className: "endlabel" }));
  }
  if (opts.interactive) {
    const pts = [
      ...hist.filter((d) => d.cut !== null).map((d) => ({ date: d.date, y: d.cut!, title: `${d.label}: ${d.current ? "Current" : monthYear(d.y!)}\nSource: ${d.cite}` })),
      ...band.map((b) => ({ date: b.date, y: b.q50, title: `${monthYear(+b.date)} (forecast)\nMedian cutoff ${monthYear(+b.q50)}\n80% range ${monthYear(+b.q10)} – ${monthYear(+b.q90)}` })),
    ];
    marks.push(Plot.ruleX(pts, Plot.pointerX({ x: "date", className: "crosshair" })));
    marks.push(Plot.tip(pts, Plot.pointerX({ x: "date", y: "y", title: "title", className: "tip" })));
  }
  return Plot.plot({
    document: opts.document,
    width: opts.width,
    height: 300,
    marginLeft: 48,
    marginRight: 16,
    marginTop: 20,
    style: { background: "transparent", fontFamily: "inherit", fontSize: "12px", overflow: "visible" },
    x: { type: "utc", label: null, ticks: opts.width < 500 ? 4 : 7, tickSize: 0 },
    y: { type: "utc", label: null, ticks: 5, tickSize: 0 },
    marks,
  });
}
