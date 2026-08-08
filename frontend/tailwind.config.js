/** @type {import('tailwindcss').Config} */
// Colours, radii and shadows all resolve to the CSS custom properties defined
// in src/styles/tokens.css, so tokens.css stays the single source of truth and
// Tailwind utilities never drift from the component classes.
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        brand: {
          50: "rgb(var(--sp-brand-50-rgb) / <alpha-value>)",
          100: "rgb(var(--sp-brand-100-rgb) / <alpha-value>)",
          200: "rgb(var(--sp-brand-200-rgb) / <alpha-value>)",
          300: "rgb(var(--sp-brand-300-rgb) / <alpha-value>)",
          400: "rgb(var(--sp-brand-400-rgb) / <alpha-value>)",
          500: "rgb(var(--sp-brand-500-rgb) / <alpha-value>)",
          600: "rgb(var(--sp-brand-600-rgb) / <alpha-value>)",
          700: "rgb(var(--sp-brand-700-rgb) / <alpha-value>)",
          800: "rgb(var(--sp-brand-800-rgb) / <alpha-value>)",
          900: "rgb(var(--sp-brand-900-rgb) / <alpha-value>)",
        },
        ink: {
          50: "rgb(var(--sp-ink-50-rgb) / <alpha-value>)",
          100: "rgb(var(--sp-ink-100-rgb) / <alpha-value>)",
          200: "rgb(var(--sp-ink-200-rgb) / <alpha-value>)",
          300: "rgb(var(--sp-ink-300-rgb) / <alpha-value>)",
          400: "rgb(var(--sp-ink-400-rgb) / <alpha-value>)",
          500: "rgb(var(--sp-ink-500-rgb) / <alpha-value>)",
          600: "rgb(var(--sp-ink-600-rgb) / <alpha-value>)",
          700: "rgb(var(--sp-ink-700-rgb) / <alpha-value>)",
          800: "rgb(var(--sp-ink-800-rgb) / <alpha-value>)",
          900: "rgb(var(--sp-ink-900-rgb) / <alpha-value>)",
        },
        surface: "rgb(var(--sp-surface-rgb) / <alpha-value>)",
        canvas: "rgb(var(--sp-canvas-rgb) / <alpha-value>)",
        line: "rgb(var(--sp-border-rgb) / <alpha-value>)",
        ok: { surface: "rgb(var(--sp-ok-surface-rgb) / <alpha-value>)", border: "rgb(var(--sp-ok-border-rgb) / <alpha-value>)", text: "rgb(var(--sp-ok-text-rgb) / <alpha-value>)", solid: "rgb(var(--sp-ok-solid-rgb) / <alpha-value>)" },
        warn: { surface: "rgb(var(--sp-warn-surface-rgb) / <alpha-value>)", border: "rgb(var(--sp-warn-border-rgb) / <alpha-value>)", text: "rgb(var(--sp-warn-text-rgb) / <alpha-value>)", solid: "rgb(var(--sp-warn-solid-rgb) / <alpha-value>)" },
        danger: { surface: "rgb(var(--sp-danger-surface-rgb) / <alpha-value>)", border: "rgb(var(--sp-danger-border-rgb) / <alpha-value>)", text: "rgb(var(--sp-danger-text-rgb) / <alpha-value>)", solid: "rgb(var(--sp-danger-solid-rgb) / <alpha-value>)" },
        programme: { surface: "rgb(var(--sp-programme-surface-rgb) / <alpha-value>)", border: "rgb(var(--sp-programme-border-rgb) / <alpha-value>)", text: "rgb(var(--sp-programme-text-rgb) / <alpha-value>)" },
        maternal: { surface: "rgb(var(--sp-maternal-surface-rgb) / <alpha-value>)", border: "rgb(var(--sp-maternal-border-rgb) / <alpha-value>)", text: "rgb(var(--sp-maternal-text-rgb) / <alpha-value>)" },
      },
      // Non-colour scales stay as plain custom properties — the
      // <alpha-value> form applies only to colours.
      fontFamily: {
        sans: "var(--sp-font-sans)",
        mono: "var(--sp-font-mono)",
      },
      borderRadius: {
        sm: "var(--sp-radius-sm)",
        md: "var(--sp-radius-md)",
        lg: "var(--sp-radius-lg)",
        xl: "var(--sp-radius-xl)",
      },
      boxShadow: {
        xs: "var(--sp-shadow-xs)",
        sm: "var(--sp-shadow-sm)",
        md: "var(--sp-shadow-md)",
        lg: "var(--sp-shadow-lg)",
      },
      spacing: {
        sidebar: "var(--sp-sidebar-w)",
        topbar: "var(--sp-topbar-h)",
        tabbar: "var(--sp-bottomnav-h)",
      },
    },
  },
  plugins: [],
};
