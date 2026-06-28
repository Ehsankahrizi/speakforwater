/**
 * Resolve a media path (audio / video) to the URL the browser should fetch.
 *
 * When PUBLIC_MEDIA_BASE is set (e.g. https://media.speakforwater.com), large
 * media (episode MP3s, the intro audio, the hero video) is served from
 * Cloudflare R2 instead of being bundled into the site. When it's unset, paths
 * stay relative, so the site behaves exactly as before (same-origin on GitHub
 * Pages). This makes the R2 cutover a single env-var flip with no code change.
 */
export const MEDIA_BASE = (import.meta.env.PUBLIC_MEDIA_BASE || "").replace(/\/$/, "");

export function mediaUrl(path?: string): string | undefined {
  if (!path) return path;
  if (/^https?:\/\//.test(path)) return path; // already absolute, leave as-is
  if (!MEDIA_BASE) return path; // relative (current GitHub Pages behavior)
  return `${MEDIA_BASE}${path.startsWith("/") ? "" : "/"}${path}`;
}
