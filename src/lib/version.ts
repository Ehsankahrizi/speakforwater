// Project version, read from the repo-root VERSION file at build time.
//
// VERSION is the single source of truth for both the site and the Python
// pipeline (app/version.py reads the same file). This runs during `astro
// build`, so the value is baked into the static output — no runtime file
// access on the deployed site.

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

function readVersion(): string {
  try {
    const path = fileURLToPath(new URL("../../VERSION", import.meta.url));
    return readFileSync(path, "utf8").trim();
  } catch {
    // A missing VERSION file must not fail the build; the version is for
    // display only.
    return "0.0.0+unknown";
  }
}

export const VERSION = readVersion();
