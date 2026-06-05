import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { fileURLToPath } from "node:url";

/**
 * Append a short content hash to a cover URL so browsers/CDNs fetch the
 * fresh image whenever its bytes change (the filename itself is stable,
 * e.g. /episodes/ep076.png, which otherwise gets cached indefinitely).
 *
 * Runs at build time (Node). Returns the path unchanged if the file can't
 * be read. Only covers whose contents actually change get a new hash, so
 * unchanged covers stay cached across deploys.
 */
const cache = new Map<string, string>();

export function versionedCover(cover?: string): string | undefined {
  if (!cover) return cover;
  if (cache.has(cover)) return cache.get(cover);
  try {
    const rel = cover.replace(/^\//, "").split("?")[0];
    const path = fileURLToPath(new URL(`../../public/${rel}`, import.meta.url));
    const hash = createHash("sha1").update(readFileSync(path)).digest("hex").slice(0, 8);
    const out = `${cover}?v=${hash}`;
    cache.set(cover, out);
    return out;
  } catch {
    cache.set(cover, cover);
    return cover;
  }
}
