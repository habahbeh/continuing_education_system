/**
 * Tailwind configuration — Arabic RTL first (ADR-003).
 *
 * PALETTE — Sprint 8J.
 * The brand is #7b202b (HSL 353° 59% 30%), and it carries 10.08:1 against
 * white, so it works as ink AND as fill. The scale below holds that hue and
 * moves lightness, easing saturation off at both ends so the tints read
 * institutional rather than pink.
 *
 * Every name that existed before this sprint still resolves — `brand.DEFAULT`,
 * `brand.dark`, `brand.soft`, `ink.2`, `ink.3`, `surface.2`, `line.2` and the
 * four semantic pairs. Seventy-one templates and the whole component layer
 * were written against them; a rename would have been a rewrite, not a
 * palette. The numbered steps are additive.
 */
module.exports = {
  content: [
    "./templates/**/*.html",
    "./apps/**/templates/**/*.html",
    "./apps/**/*.py",
  ],
  theme: {
    extend: {
      colors: {
        brand: {
          50:  "#fbf4f5",  // selected row, hovered nav
          100: "#f7e9ea",  // active nav ground, brand badge, key KPI tile
          200: "#eeced2",  // badge + section borders
          300: "#e2a7ae",  // focus halo, disabled primary
          400: "#ce6471",  // decorative only — never text
          500: "#af3141",  // focus ring core
          600: "#912733",  // primary button hover
          700: "#7b202b",  // THE brand — button, topbar, active nav, h1, links
          800: "#611a22",  // primary button pressed, sidebar emphasis
          900: "#4b161d",  // strongest headings, letterhead rule
          950: "#2e0f13",
          // Pre-8J aliases. Kept so the component layer and the templates
          // that reference them keep resolving.
          DEFAULT: "#7b202b",
          dark:    "#611a22",
          soft:    "#f7e9ea",
        },
        // `ink-3` is darker than it was (#6b7580 → #5d6b7a) so muted text
        // clears AA on the grey page ground (5.08:1), not only on white.
        ink:     { DEFAULT: "#0f1620", 2: "#3b4653", 3: "#5d6b7a" },
        surface: { DEFAULT: "#f4f6f8", 2: "#eef1f4", card: "#ffffff" },
        line:    { DEFAULT: "#d7dde4", 2: "#e7ecf1" },

        // Semantic. `danger` is deliberately moved warmer and lighter than it
        // was (#b3261e → #a4231c, hue 3°) to open a gap against brand-700
        // (hue 353°): before this, a destructive action and a primary action
        // read as the same family. Colour is never the only signal — every
        // chip still carries its Arabic label.
        ok:     { DEFAULT: "#186a3b", soft: "#e9f5ee", line: "#bfe0cd" },
        warn:   { DEFAULT: "#7a5803", soft: "#fdf6e3", line: "#e8d69b" },
        danger: { DEFAULT: "#a4231c", soft: "#fdeceb", line: "#f3c4c1" },
        info:   { DEFAULT: "#1a5490", soft: "#e8f1fa", line: "#bcd5ee" },
      },
      fontFamily: {
        // Arabic comes from the self-hosted face (static/fonts/, OFL-1.1 —
        // see PROVENANCE.md). Latin and the digits fall through to the system
        // stack on purpose: its tabular figures align amounts better and its
        // 0/O and 1/l are easier to tell apart on a receipt.
        sans: [
          '"Noto Kufi Arabic"',
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "sans-serif",
        ],
      },
      fontSize: {
        // Arabic needs more leading than Latin at every step.
        "2xs": ["0.6875rem", { lineHeight: "1.45" }],  // 11px — eyebrows
        xs:   ["0.75rem",   { lineHeight: "1.55" }],   // 12px — labels, meta
        sm:   ["0.8125rem", { lineHeight: "1.6"  }],   // 13px — table data
        base: ["0.875rem",  { lineHeight: "1.7"  }],   // 14px — body
        lg:   ["0.9375rem", { lineHeight: "1.55" }],   // 15px — h2
        xl:   ["1.0625rem", { lineHeight: "1.5"  }],   // 17px — h1 in print
        "2xl":["1.375rem",  { lineHeight: "1.4"  }],   // 22px — page title
        "3xl":["1.75rem",   { lineHeight: "1.35" }],   // 28px — KPI figure
      },
      borderRadius: { lg: "0.5rem", xl: "0.625rem" },
      ringColor:  { DEFAULT: "#af3141" },
      ringOffsetColor: { DEFAULT: "#ffffff" },
      boxShadow: {
        // One elevation, used only where something genuinely floats above the
        // page (the mobile nav drawer). Cards get a border, not a shadow.
        drawer: "0 0 0 100vmax rgba(15,22,32,.45)",
      },
      maxWidth: { content: "110rem" },
      spacing:  { "18": "4.5rem" },
      transitionDuration: { DEFAULT: "150ms" },
    },
  },
  plugins: [],
};
