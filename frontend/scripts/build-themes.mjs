// Writes src/themes.css - the application's colour themes - from Tailwind's own palette.
//   node scripts/build-themes.mjs
//
// Every colour in the app is a Tailwind utility (bg-slate-900, text-sky-300 ...) and Tailwind v4 draws
// each one from a CSS variable (--color-slate-900). A theme re-points those variables under
// [data-theme=...], so the whole app - and any part of it (a preview, a Customer 360 frame) - changes
// with no change to a page. The pages were designed dark; a light theme mirrors each scale:
//   neutrals (slate)  950 page -> near-white, 900 card -> white, 800/700 borders -> light greys, 400-100 text -> dark
//   accents           300/400 text -> 700/800, 500-700 solid -> 600-800, 800-950 tints -> 200-50
//   white             text-white headings -> near-black; solid accent backgrounds keep white text
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const source = readFileSync(join(root, "node_modules/tailwindcss/theme.css"), "utf8");
const palette = {};
for (const [, family, step, value] of source.matchAll(/--color-([a-z]+)-(\d+):\s*([^;]+);/g)) (palette[family] ??= {})[step] = value.trim();

const STEPS = ["50", "100", "200", "300", "400", "500", "600", "700", "800", "900", "950"];
const NEUTRALS = ["slate", "gray", "zinc", "neutral", "stone"];
const ACCENTS = Object.keys(palette).filter((f) => !NEUTRALS.includes(f));
const vars = (map) => Object.entries(map).map(([k, v]) => `  ${k}: ${v};`).join("\n");

// the original (dark) palette, restated so a dark island inside a light page gets it back
const darkPalette = {};
for (const f of ["slate", ...ACCENTS]) for (const s of STEPS) darkPalette[`--color-${f}-${s}`] = palette[f][s];

const LIGHT_SLATE = {
  950: "#f3f5f9", 900: "#ffffff", 800: "#e5e9f0", 700: "#d2d8e1", 600: "#a1abb9",
  500: "#687587", 400: "#4b5768", 300: "#334155", 200: "#1e293b", 100: "#111827", 50: "#0b1220",
};
const LIGHT_ACCENT = { 50: "950", 100: "900", 200: "800", 300: "800", 400: "700", 500: "600", 600: "700", 700: "800", 800: "200", 900: "100", 950: "50" };
const light = {};
for (const s of STEPS) light[`--color-slate-${s}`] = LIGHT_SLATE[s];
for (const f of ACCENTS) for (const s of STEPS) light[`--color-${f}-${s}`] = palette[f][LIGHT_ACCENT[s]];

// Dusk: the same dark design, softer - charcoal surfaces, a little less contrast
const DIM_SLATE = { 950: "#171b23", 900: "#1e232d", 800: "#2a313d", 700: "#39414f", 600: "#535d6e" };
const dim = { ...darkPalette }; // complete, so a Dusk preview inside a light page is all Dusk
for (const [s, v] of Object.entries(DIM_SLATE)) dim[`--color-slate-${s}`] = v;

const SURFACES = {
  dark: { "--app-bg": "#070b14", "--app-panel": "#0a101d", "--app-card": "#0b1120", "--color-white": "#fff", "--color-black": "#000", "color-scheme": "dark" },
  light: { "--app-bg": "#f3f5f9", "--app-panel": "#ffffff", "--app-card": "#ffffff", "--color-white": "#0b1220", "color-scheme": "light" },
  dim: { "--app-bg": "#14181f", "--app-panel": "#191d25", "--app-card": "#1b2029", "--color-white": "#fff", "color-scheme": "dark" },
};

// in the light theme, solid accent backgrounds (buttons, badges, avatars) keep white text
const solid = [];
for (const f of ACCENTS) for (const s of ["500", "600", "700"]) solid.push(`.bg-${f}-${s}`, `.from-${f}-${s}`);
// ... and their pale text (a count "text-sky-100" on a "bg-sky-600" chip) stays pale
const onSolid = {};
for (const f of ["slate", ...ACCENTS]) for (const s of ["50", "100", "200", "300"]) onSolid[`--color-${f}-${s}`] = palette[f][s];
// and real white things stay white: images (QR codes, logos), embedded documents
const keepWhite = ["img", "iframe", "video", "canvas", "[data-keep-white]"];

// pages that draw SVG with the dark palette written out (charts, flow and map canvases) follow the theme too
const SVG = {
  fill: { "#020617": "slate-950", "#0b1120": "slate-900", "#0f172a": "slate-900", "#0b1222": "slate-950", "#1e293b": "slate-800", "#334155": "slate-700", "#cbd5e1": "slate-300", "#e2e8f0": "slate-200", "#f1f5f9": "slate-100", "#2e1065": "violet-950" },
  stroke: { "#0b1220": "slate-900", "#0f172a": "slate-900", "#1e293b": "slate-800", "#334155": "slate-700", "#475569": "slate-600" },
};
const svgRules = Object.entries(SVG).flatMap(([prop, map]) =>
  Object.entries(map).map(([hex, v]) => `[${prop}="${hex}"] { ${prop}: var(--color-${v}, ${hex}); }`));

// accent families and the companion each pairs with in gradients (never sky - sky is what gets re-pointed)
const COMPANION = {
  blue: "violet", indigo: "fuchsia", violet: "pink", purple: "pink", fuchsia: "rose", pink: "orange", rose: "orange", red: "amber",
  orange: "rose", amber: "rose", yellow: "orange", lime: "emerald", green: "teal", emerald: "teal", teal: "cyan", cyan: "blue",
};
const A2_STEPS = ["300", "400", "500", "600", "700"];
const accentRules = Object.entries(COMPANION).map(([family, partner]) => {
  const map = {};
  for (const s of STEPS) map[`--color-sky-${s}`] = `var(--color-${family}-${s})`;
  for (const s of A2_STEPS) map[`--a2-${s}`] = `var(--color-${partner}-${s})`;
  return `[data-accent="${family}"], [data-accent="${family}"] [data-theme] {\n${vars(map)}\n}`;
});

const version = JSON.parse(readFileSync(join(root, "node_modules/tailwindcss/package.json"), "utf8")).version;
const css = `/* GENERATED by scripts/build-themes.mjs from the Tailwind ${version} palette - do not edit by hand.
   The application's colour themes: <html data-theme="dark|light|dim"> (src/theme.ts chooses), or any element. */

:root, [data-theme="dark"] {
${vars(SURFACES.dark)}
}
[data-theme="dark"] {
${vars(darkPalette)}
}

[data-theme="light"] {
${vars(light)}
${vars(SURFACES.light)}
}
[data-theme="light"] :is(${solid.join(", ")}) {
  --color-white: #fff;
${vars(onSolid)}
}
[data-theme="light"] :is(${keepWhite.join(", ")}) { --color-white: #fff; }

[data-theme="dim"] {
${vars(dim)}
${vars(SURFACES.dim)}
}

/* the page itself */
[data-theme] { color: var(--color-slate-200); }

/* Accent palettes (src/theme.ts ACCENTS): <html data-accent="violet"> makes every sky-* utility - the app's
   accent - draw from that family, in whatever theme (the reference resolves against the theme's own scale).
   --a2-* is the companion colour for gradients. */
:root, [data-theme] {
${vars(Object.fromEntries(A2_STEPS.map((s) => [`--a2-${s}`, `var(--color-indigo-${s})`])))}
}
${accentRules.join("\n")}

${svgRules.join("\n")}
`;
writeFileSync(join(root, "src/themes.css"), css);
console.log(`src/themes.css: ${css.split("\n").length} lines, ${ACCENTS.length} accent families`);
