import js from "@eslint/js";
import tseslint from "typescript-eslint";
import prettier from "eslint-config-prettier";

// Flat config (ESLint 9+). Order matters: `prettier` is last so it disables
// any stylistic rules that would fight the formatter -- Prettier owns layout,
// ESLint owns correctness.
export default tseslint.config(
  { ignores: ["dist/**", "node_modules/**"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    rules: {
      // Allow intentionally-unused args when prefixed with `_` (e.g. injected
      // deps a stub doesn't use); still flag genuinely dead bindings.
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
    },
  },
  {
    // Tests cast partial mocks and fixtures to `any` on purpose; demanding full
    // types there is noise, not safety. Production code keeps the rule.
    files: ["test/**"],
    rules: { "@typescript-eslint/no-explicit-any": "off" },
  },
  prettier,
);
