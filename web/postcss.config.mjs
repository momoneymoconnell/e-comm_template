/**
 * PostCSS configuration.
 *
 * Tailwind v4 is a PostCSS plugin and needs no tailwind.config.js: the theme
 * is declared in CSS via `@theme`, in src/app/globals.css.
 */
const config = {
  plugins: {
    "@tailwindcss/postcss": {},
  },
};

export default config;
