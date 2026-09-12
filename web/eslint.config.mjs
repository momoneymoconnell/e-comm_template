/**
 * ESLint flat configuration.
 *
 * `eslint-config-next` v16 exports flat config arrays directly, so they are
 * spread in as-is. The older `FlatCompat` shim is not used: it re-validates
 * these configs through the legacy eslintrc schema and crashes on the plugin
 * cross-references they now contain.
 */

import nextCoreWebVitals from "eslint-config-next/core-web-vitals";
import nextTypeScript from "eslint-config-next/typescript";

const config = [
  ...nextCoreWebVitals,
  ...nextTypeScript,
  {
    ignores: [".next/**", "node_modules/**", "next-env.d.ts", "public/**"],
  },
  {
    rules: {
      // Arguments and bindings prefixed with _ are intentionally unused.
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
    },
  },
]

export default config;
