# Re-branding the front end (name, logo, colours, texts)

All look-and-feel settings live in **one file**: [`src/branding.ts`](src/branding.ts). Edit the values in the
`branding` object; nothing else in the front end needs to change. The file has a comment on every key.

## What you can change there

| Want to… | Edit |
|---|---|
| Rename the app (top bar + browser tab) | `appName` |
| Add a logo | Copy the image into `frontend/public/` (SVG or PNG), set `logo: "/your-logo.svg"` and `logoHeight` |
| Change the browser-tab icon | Replace `frontend/public/favicon.svg` or point `favicon` at a new file in `public/` |
| Company name / link in the footer | `organization`, `organizationUrl` |
| "Need help?" link in the footer | `supportUrl` (e.g. `mailto:bi-team@contoso.com`), `supportLabel` |
| Home page headline, intro, privacy sentence | `heroTitle`, `heroLead`, `privacyNote` |
| Footer sentence | `footerNote` |
| Hide the sample files on the home page | `showSampleFiles: false` (or delete files from the repo's `samples/` folder) |
| Button / link / accent colour | `colors.primary`, `colors.primaryHover`, `colors.primarySoft` |
| Page background, cards, borders, text colours | `colors.background`, `colors.surface`, `colors.border`, `colors.text`, `colors.textMuted` |
| Font | `fontFamily` (a CSS font stack; keep system fonts unless the intranet allows the webfont) |
| Rounded vs square corners | `borderRadius` (`"10px"` default, `"4px"` for a squarer look) |
| Chart colours | `chartPalette` - exactly 8 hex colours; the first one is the main chart colour |
| Suggestion chips in the "describe your data" chat | `modelChatSuggestions` |

## Rules

1. Change **values only**. Do not rename, add or remove keys and do not change the type definitions.
2. Keep hex colours as 6-digit strings like `"#1a73e8"`.
3. `chartPalette` must keep 8 entries. Neighbouring colours should look clearly different; avoid very light
   colours (they vanish on white) and avoid red/green pairs next to each other (colour-blind readers).
4. Do not edit `src/styles.css`, the components, the pages or anything under `backend/` for a re-brand.
   (`styles.css` only holds layout; its colours are overridden at runtime by `applyBranding()`.)

## Verify

```bash
cd frontend
npm run lint && npm run build     # both must finish without errors
npm run dev                       # open http://localhost:5173 and look at the home page and a dashboard
```

If `npm run build` fails after your edit, the error names the line in `src/branding.ts`; the usual causes are a
missing comma, an unclosed quote, or a `chartPalette` with the wrong number of entries.

## How it works (for the curious)

`applyBranding()` (bottom of `branding.ts`, called once in `src/main.tsx`) writes the colours, font and radius
into CSS variables, sets the page title and favicon. Charts come from the back end as Plotly figures drawn
with the default palette; `PlotlyChart` swaps those colours for `chartPalette` before drawing, so the back end
never needs to know about the brand.
