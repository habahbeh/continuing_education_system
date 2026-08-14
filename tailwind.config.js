/** Tailwind configuration — Arabic RTL first (ADR-003). */
module.exports = {
  content: [
    "./templates/**/*.html",
    "./apps/**/templates/**/*.html",
    "./apps/**/*.py",
  ],
  theme: {
    extend: {
      colors: {
        brand:        { DEFAULT: "#7a1f2b", dark: "#5c1721", soft: "#f7ecee" },
        ink:          { DEFAULT: "#1b1f24", 2: "#3d444d", 3: "#6b7580" },
        surface:      { DEFAULT: "#f6f7f9", 2: "#eef1f5" },
        line:         { DEFAULT: "#dde2e8", 2: "#eaeef3" },
        ok:           { DEFAULT: "#1c7c46", soft: "#e8f5ee" },
        warn:         { DEFAULT: "#8a6d0c", soft: "#fdf8e6" },
        danger:       { DEFAULT: "#b3261e", soft: "#fdecea" },
        info:         { DEFAULT: "#1d5fa8", soft: "#e9f1fb" },
      },
      fontFamily: {
        sans: ['"Noto Kufi Arabic"', "system-ui", "sans-serif"],
      },
    },
  },
  plugins: [],
};
