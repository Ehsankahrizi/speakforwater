# Migrating SpeakForWater to Cloudflare Pages + R2 (private repo)

Goal: serve the site from Cloudflare's edge and the audio from Cloudflare R2,
with the GitHub repo **private** — so visitors see only the final website, not
the code, workflows, or that it's hosted on GitHub. Stays free.

**Why R2 for audio:** Cloudflare Pages rejects any file over **25 MiB**, and 74
of 81 episodes (plus the intro and hero video) are larger. Large media goes to
R2; the site references it at `https://media.speakforwater.com/...`.

The code is already wired for this and is **backward-compatible**: with the env
vars unset, the site and pipeline behave exactly as they do today on GitHub
Pages. Nothing below breaks the live site until the final cutover (Step 7).

---

## Naming used in this guide (change if you like)

| Thing | Value |
|---|---|
| R2 bucket | `speakforwater-media` |
| Media domain (R2 public) | `media.speakforwater.com` |
| Site domain (Cloudflare Pages) | `speakforwater.com` |

The site reads `PUBLIC_MEDIA_BASE`; the pipeline reads `MEDIA_BASE_URL`. Both =
`https://media.speakforwater.com`.

---

## Step 1 — Create the R2 bucket

1. Cloudflare dashboard → **R2** → **Create bucket** → name `speakforwater-media`.
2. After creating, open the bucket → **Settings** → **Public access** →
   **Connect a custom domain** → `media.speakforwater.com`. (Cloudflare adds the
   DNS record automatically since the domain is already on Cloudflare.)

## Step 2 — Set the bucket's CORS policy (REQUIRED)

The episode players use `crossorigin="anonymous"` so the audio visualizer can
read the waveform. Without CORS, the audio **won't even play**. In the bucket →
**Settings** → **CORS policy** → add:

```json
[
  {
    "AllowedOrigins": [
      "https://speakforwater.com",
      "https://www.speakforwater.com",
      "https://*.pages.dev"
    ],
    "AllowedMethods": ["GET", "HEAD"],
    "AllowedHeaders": ["*"],
    "MaxAgeSeconds": 86400
  }
]
```

## Step 3 — Create an R2 API token

R2 → **Manage R2 API Tokens** → **Create API token** → permission
**Object Read & Write**, scoped to `speakforwater-media`. Copy:

- Account ID (shown on the R2 overview page)
- Access Key ID
- Secret Access Key

## Step 4 — Upload all existing media to R2

From the repo root on your Mac:

```bash
pip install boto3
R2_ACCOUNT_ID=<account-id> \
R2_ACCESS_KEY_ID=<access-key> \
R2_SECRET_ACCESS_KEY=<secret-key> \
R2_BUCKET=speakforwater-media \
python3 scripts/upload_media_to_r2.py --dry-run    # preview (83 files, ~2.5 GB)

# then drop --dry-run to actually upload
```

Verify one file is public, e.g. open:
`https://media.speakforwater.com/episodes/ep081.mp3`

## Step 5 — Tell the pipeline about R2 (GitHub Actions)

Repo → **Settings** → **Secrets and variables** → **Actions**:

- **Secrets:** `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`
- **Secrets or Variables:** `R2_BUCKET` = `speakforwater-media`,
  `MEDIA_BASE_URL` = `https://media.speakforwater.com`

Then add these to the `env:` of **both** `generate-podcast.yml` and
`search-papers.yml`'s job that calls the pipeline (the generate workflow is the
one that uploads). Each new episode's MP3 will upload to R2 and stay out of the
repo automatically; the RSS feed will use the R2 URLs.

## Step 6 — Create the Cloudflare Pages project

1. Cloudflare dashboard → **Workers & Pages** → **Create** → **Pages** →
   **Connect to Git** → pick the `speakforwater` repo (authorize Cloudflare's
   GitHub app; it can read private repos).
2. Build settings:
   - **Framework preset:** Astro
   - **Build command:** `npm run build`
   - **Build output directory:** `dist`
3. **Environment variables** (Production *and* Preview):
   - `PUBLIC_MEDIA_BASE` = `https://media.speakforwater.com`
4. Deploy. Test the generated `*.pages.dev` URL — confirm episodes **play** and
   the visualizer animates (that proves CORS is right).

> Cloudflare Pages rebuilds automatically on every push to `main`, including the
> bot commits from the daily pipeline — so this replaces the GitHub Pages
> `deploy.yml` workflow entirely.

## Step 7 — Cutover (do this only after Step 6 works on pages.dev)

This is the point of no return for GitHub Pages. Run from the repo root:

```bash
# 1. Stop tracking large media in the repo (it lives in R2 now)
git rm --cached public/episodes/*.mp3 public/ep000.m4a public/movie_1.mp4
printf '\n# Large media — served from Cloudflare R2\npublic/episodes/*.mp3\npublic/ep000.m4a\npublic/movie_1.mp4\n' >> .gitignore

# 2. Remove the GitHub Pages deploy workflow (Cloudflare Pages handles deploys)
git rm .github/workflows/deploy.yml

git commit -m "Cutover to Cloudflare Pages + R2: drop large media and GitHub Pages deploy"
git push
```

Then in the Cloudflare dashboard:

3. Pages project → **Custom domains** → add `speakforwater.com` (and `www`).
   Cloudflare updates the DNS from the old GitHub Pages records to the Pages
   project (proxied / orange-cloud). The `server: GitHub.com` header disappears.
4. GitHub repo → **Settings** → **General** → **Change visibility** →
   **Make private**. (Pages keeps building — Cloudflare's GitHub app retains
   access. The daily Actions pipeline keeps running on a private repo.)
5. GitHub repo → **Settings** → **Pages** → set source to **None** (turn off the
   now-unused GitHub Pages site).

## Step 8 — Verify

- `curl -sI https://speakforwater.com | grep -i server` → should say `cloudflare`,
  not `GitHub.com`.
- Open an episode page → audio plays, equalizer animates.
- `https://speakforwater.com/podcast.xml` → enclosure URLs point at
  `media.speakforwater.com` (regenerated on the next episode, or regenerate
  manually).
- The repo is private; visiting its GitHub URL while logged out 404s.

---

## Rollback

Nothing is destroyed until Step 7. If something's wrong on `pages.dev`, just
don't do Step 7 — the live GitHub Pages site is untouched. After Step 7, to roll
back you'd `git revert` the cutover commit and re-point the domain to GitHub
Pages.
