/**
 * BRANDING - the ONE file to edit for look-and-feel changes.
 *
 * Everything cosmetic lives here: app name, logo, favicon, colours, font, corner radius, the texts on the
 * home page and footer, the chart colour palette, and the suggestion chips in the chat.
 * The rest of the front end reads from this object; nothing else needs to change for a re-brand.
 *
 * Rules for editing (also in frontend/BRANDING.md):
 *   1. Change VALUES only. Do not rename or remove keys, do not change the types.
 *   2. Logo / favicon files go into  frontend/public/  and are referenced with a leading slash ("/logo.svg").
 *   3. Colours are hex strings ("#2a78d6"). chartPalette needs exactly 8 distinct colours, darkest-to-lightest
 *      order does not matter, but neighbours should look clearly different (they are used in that order).
 *   4. After editing run, inside frontend/:   npm run lint && npm run build   - both must pass.
 */

export type Branding = {
  /** Shown in the top bar and the browser tab. */
  appName: string;
  /** Company or team name shown in the footer (empty = hidden). */
  organization: string;
  /** Link for the organization name in the footer (empty = plain text). */
  organizationUrl: string;
  /** Path to a logo image in frontend/public (e.g. "/logo.svg"), or null for the built-in mark. */
  logo: string | null;
  /** Logo height in pixels in the top bar. */
  logoHeight: number;
  /** Path to the browser-tab icon in frontend/public. */
  favicon: string;
  /** Home page headline and intro sentence. */
  heroTitle: string;
  heroLead: string;
  /** Privacy sentence under "How it works" on the home page. */
  privacyNote: string;
  /** One line at the bottom of every page. */
  footerNote: string;
  /** Where users get help, e.g. "mailto:bi-team@contoso.com" or a Teams/SharePoint link (empty = hidden). */
  supportUrl: string;
  supportLabel: string;
  /** Offer the sample files from the samples/ folder on the home page. */
  showSampleFiles: boolean;
  /** Suggestion chips under the "Tell the AI about your data" chat (max 4 short sentences). */
  modelChatSuggestions: string[];
  /** Colours used by the page chrome (buttons, cards, text). */
  colors: {
    primary: string;        // buttons, links, active step
    primaryHover: string;   // primary button on hover (slightly darker)
    primarySoft: string;    // light tint of primary (progress banner, active step pill)
    background: string;     // page background
    surface: string;        // cards
    border: string;         // card and input borders
    text: string;           // main text
    textMuted: string;      // secondary text
    danger: string;         // destructive actions / errors
  };
  /** CSS font stack. Use a system font stack unless the font is installed/allowed on the intranet. */
  fontFamily: string;
  /** Corner radius of cards and buttons, e.g. "10px" (use "4px" for a squarer corporate look). */
  borderRadius: string;
  /**
   * Chart colours, in the order series are coloured. The FIRST colour is the main chart colour (single-series
   * bars/lines, heatmap ramp). Keep 8 entries. Status colours (green/amber/red for words like "on track",
   * "at risk", "overdue") are fixed on purpose and not part of this list.
   */
  chartPalette: [string, string, string, string, string, string, string, string];
};

export const branding: Branding = {
  appName: "Dashboard Builder",
  organization: "",
  organizationUrl: "",
  logo: null,
  logoHeight: 28,
  favicon: "/favicon.svg",
  heroTitle: "Turn a spreadsheet into a dashboard",
  heroLead:
    "Upload a CSV or Excel file. The app works out what your columns mean, asks you to confirm, builds a dashboard, " +
    "and lets you change it in plain English. Save the dashboard as a file and rebuild it any time with new data.",
  privacyNote:
    "Privacy: only column names, summary statistics, the most common values and a handful of sample rows are sent " +
    "to the AI model - never the whole file. Uploaded files are deleted automatically after a retention period.",
  footerNote: "Only column summaries and sample values are sent to the AI model. Keep your downloaded dashboard file - it is the only copy.",
  supportUrl: "",
  supportLabel: "Need help?",
  showSampleFiles: true,
  modelChatSuggestions: ["Amounts are in thousands", "Ignore the ID columns in charts", "The main date column is the invoice date"],
  colors: {
    primary: "#2a78d6",
    primaryHover: "#2266bc",
    primarySoft: "#e8f0fb",
    background: "#f5f6f8",
    surface: "#ffffff",
    border: "#e3e6eb",
    text: "#1f2937",
    textMuted: "#6b7280",
    danger: "#d03b3b",
  },
  fontFamily: 'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif',
  borderRadius: "10px",
  chartPalette: ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"],
};

// ----------------------------------------------------------------------------------------------------------
// Everything below is plumbing. It applies the values above; it does not need editing for a re-brand.
// ----------------------------------------------------------------------------------------------------------

/** Writes the colours/font/radius into the CSS variables that styles.css uses, and sets title + favicon. */
export function applyBranding(b: Branding = branding): void {
  const root = document.documentElement.style;
  const c = b.colors;
  const vars: Record<string, string> = {
    "--primary": c.primary,
    "--primary-hover": c.primaryHover,
    "--primary-soft": c.primarySoft,
    "--bg": c.background,
    "--surface": c.surface,
    "--border": c.border,
    "--ink": c.text,
    "--ink-muted": c.textMuted,
    "--danger": c.danger,
    "--font": b.fontFamily,
    "--radius": b.borderRadius,
  };
  for (const [k, v] of Object.entries(vars)) root.setProperty(k, v);
  document.title = b.appName;
  const link = document.querySelector<HTMLLinkElement>('link[rel="icon"]');
  if (link && b.favicon) link.href = b.favicon;
}

/** The palette the back end draws charts with (backend/app/engine/charts.py). Used to re-colour figures. */
export const DEFAULT_CHART_PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"];
const DEFAULT_SEQUENTIAL = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#2a78d6", "#1c5cab", "#104281", "#0d366b"];

function mix(hex: string, target: [number, number, number], amount: number): string {
  const h = hex.replace("#", "");
  const rgb = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16));
  const out = rgb.map((v, i) => Math.round(v + (target[i] - v) * amount));
  return "#" + out.map((v) => v.toString(16).padStart(2, "0")).join("");
}
const tint = (hex: string, amount: number) => mix(hex, [255, 255, 255], amount);
const shade = (hex: string, amount: number) => mix(hex, [13, 54, 107], amount);

/** Map from every default chart colour (incl. treemap tints and the heatmap ramp) to the brand equivalent. */
export function chartColorMap(b: Branding = branding): Map<string, string> | null {
  const same = b.chartPalette.every((c, i) => c.toLowerCase() === DEFAULT_CHART_PALETTE[i]);
  if (same) return null;
  const map = new Map<string, string>();
  DEFAULT_CHART_PALETTE.forEach((d, i) => {
    map.set(d, b.chartPalette[i]);
    map.set(tint(d, 0.55), tint(b.chartPalette[i], 0.55)); // treemap fills
  });
  const p = b.chartPalette[0];
  const ramp = [tint(p, 0.75), tint(p, 0.5), tint(p, 0.3), tint(p, 0.1), p, shade(p, 0.25), shade(p, 0.5), shade(p, 0.65)];
  DEFAULT_SEQUENTIAL.forEach((d, i) => map.set(d, ramp[i]));
  return map;
}
