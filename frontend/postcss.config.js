export default {
  plugins: {
    // Must run first so @import'ed files are inlined before Tailwind processes
    // @layer / @apply directives across file boundaries.
    "postcss-import": {},
    tailwindcss: {},
    autoprefixer: {},
  },
};
