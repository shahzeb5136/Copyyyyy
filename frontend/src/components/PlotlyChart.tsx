import { useEffect, useRef } from "react";
import type Plotly from "plotly.js";

type Props = { figure: { data: unknown[]; layout?: Record<string, unknown> }; className?: string };

let plotlyPromise: Promise<typeof Plotly> | null = null;
/** plotly.js is ~1.4 MB gzipped; load it only when the first chart is rendered. */
function loadPlotly(): Promise<typeof Plotly> {
  if (!plotlyPromise) plotlyPromise = import("plotly.js-dist-min").then((m) => m.default);
  return plotlyPromise;
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
      const layout = { ...(figure.layout ?? {}), autosize: true, width: undefined };
      P.react(el, figure.data as Plotly.Data[], layout as Partial<Plotly.Layout>, { responsive: true, displayModeBar: false, displaylogo: false });
    });
    return () => {
      cancelled = true;
      if (lib) lib.purge(el);
    };
  }, [figure]);
  const height = typeof figure.layout?.height === "number" ? (figure.layout.height as number) : 380;
  return <div ref={ref} className={className} style={{ width: "100%", height }} />;
}
