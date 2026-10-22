import { useEffect, useRef } from "react";
import type Plotly from "plotly.js";
import { chartColorMap } from "../branding";

type Props = { figure: { data: unknown[]; layout?: Record<string, unknown> }; className?: string };

let plotlyPromise: Promise<typeof Plotly> | null = null;
/** plotly.js is ~1.4 MB gzipped; load it only when the first chart is rendered. */
function loadPlotly(): Promise<typeof Plotly> {
  if (!plotlyPromise) plotlyPromise = import("plotly.js-dist-min").then((m) => m.default);
  return plotlyPromise;
}

const colorMap = chartColorMap();

/** Replace the back end's default chart colours with the brand palette (see branding.ts), if it differs. */
function rebrand<T>(value: T): T {
  if (!colorMap) return value;
  const walk = (v: unknown): unknown => {
    if (typeof v === "string") return colorMap.get(v.toLowerCase()) ?? v;
    if (Array.isArray(v)) return v.map(walk);
    if (v && typeof v === "object") return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, walk(x)]));
    return v;
  };
  return walk(value) as T;
}

/** Renders a Plotly figure produced by the back end. The figure JSON is the only contract. */
export default function PlotlyChart({ figure, className }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    let cancelled = false;
    let lib: typeof Plotly | null = null;
    loadPlotly().then((P) => {
      if (cancelled || !ref.current) return;
      lib = P;
      const layout = { ...rebrand(figure.layout ?? {}), autosize: true, width: undefined };
      P.react(el, rebrand(figure.data) as Plotly.Data[], layout as Partial<Plotly.Layout>, { responsive: true, displayModeBar: false, displaylogo: false });
    });
    return () => {
      cancelled = true;
      if (lib) lib.purge(el);
    };
  }, [figure]);
  const height = typeof figure.layout?.height === "number" ? (figure.layout.height as number) : 380;
  return <div ref={ref} className={className} style={{ width: "100%", height }} />;
}
