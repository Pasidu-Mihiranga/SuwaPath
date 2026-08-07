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
          50: "var(--sp-brand-50)",
          100: "var(--sp-brand-100)",
          200: "var(--sp-brand-200)",
          300: "var(--sp-brand-300)",
          400: "var(--sp-brand-400)",
          500: "var(--sp-brand-500)",
          600: "var(--sp-brand-600)",
          700: "var(--sp-brand-700)",
          800: "var(--sp-brand-800)",
          900: "var(--sp-brand-900)",
        },
        ink: {
          50: "var(--sp-ink-50)",
          100: "var(--sp-ink-100)",
          200: "var(--sp-ink-200)",
          300: "var(--sp-ink-300)",
          400: "var(--sp-ink-400)",
          500: "var(--sp-ink-500)",
          600: "var(--sp-ink-600)",
          700: "var(--sp-ink-700)",
          800: "var(--sp-ink-800)",
          900: "var(--sp-ink-900)",
        },
        surface: "var(--sp-surface)",
        canvas: "var(--sp-canvas)",
        line: "var(--sp-border)",
        ok: { surface: "var(--sp-ok-surface)", border: "var(--sp-ok-border)", text: "var(--sp-ok-text)" },
        warn: { surface: "var(--sp-warn-surface)", border: "var(--sp-warn-border)", text: "var(--sp-warn-text)" },
        danger: { surface: "var(--sp-danger-surface)", border: "var(--sp-danger-border)", text: "var(--sp-danger-text)" },
        programme: { surface: "var(--sp-programme-surface)", border: "var(--sp-programme-border)", text: "var(--sp-programme-text)" },
        maternal: { surface: "var(--sp-maternal-surface)", border: "var(--sp-maternal-border)", text: "var(--sp-maternal-text)" },
      },
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
