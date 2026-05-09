# Deploying SpeakForWater website

This is the new Astro-based website for `speakforwater.com`. It replaces
your current `index.html`. It auto-builds and deploys to GitHub Pages on every
`git push` to `main`.

## What's in this folder

```
speakforwater-site/
├── package.json              # Astro and dependencies
├── astro.config.mjs          # Site URL, sitemap integration
├── tsconfig.json
├── public/
│   ├── CNAME                 # speakforwater.com
│   ├── robots.txt
│   └── favicon.svg           # Brand icon (water droplet)
├── src/
│   ├── content/
│   │   ├── config.ts         # Episode schema (zod)
│   │   └── episodes/         # Markdown file per episode
│   ├── layouts/Layout.astro  # SEO, head, header, footer
│   ├── components/           # Header, Footer, AudioPlayer, EpisodeCard, SubscribeButtons
│   ├── pages/
│   │   ├── index.astro       # Homepage
│   │   ├── about.astro
│   │   ├── subscribe.astro
│   │   ├── 404.astro
│   │   └── episodes/
│   │       ├── index.astro   # Archive with filter
│   │       └── [slug].astro  # Single episode page
│   └── styles/global.css
├── scripts/sync_episodes.py  # Pulls episodes from Google Sheet
└── .github/workflows/deploy.yml
```

## Step-by-step deploy (first time)

### 1. Copy these files into your existing `speakforwater` repo

In your local clone of `Ehsankahrizi/speakforwater`, copy everything from
this `speakforwater-site/` folder into the repo root.

Important — DO NOT delete or replace yet:
- `episodes/` directory (your MP3 files live here)
- `podcast.xml` (your RSS feed)
- `.github/workflows/` (your existing pipeline workflows)

**To merge:** copy all the new files. The new `.github/workflows/deploy.yml`
will be added alongside your existing workflow files. The new `index.html`
is replaced by Astro's build output, but Astro builds to `dist/` and
GitHub Actions deploys that — so your repo's old `index.html` should be
deleted (the new dynamic homepage replaces it).

### 2. Delete the old `index.html`

```bash
git rm index.html
```

The new homepage (`src/pages/index.astro`) replaces it.

### 3. Move your existing podcast.xml into `public/`

```bash
git mv podcast.xml public/podcast.xml
```

This way Astro copies it into the build output.

### 4. Move episode MP3s into `public/episodes/`

```bash
mkdir -p public/episodes
git mv episodes/*.mp3 public/episodes/  # if any exist
```

The site now serves audio from `https://speakforwater.com/episodes/ep001.mp3`.
This is what `audio_url` in each episode markdown points to.

### 5. Configure GitHub Pages to use Actions

In your repo:
1. Go to **Settings → Pages**
2. Under **Source**, select **GitHub Actions** (not "Deploy from a branch")
3. Set custom domain to `speakforwater.com` if not already set
4. Wait for the next push to trigger the build

### 6. Test the build locally (optional)

```bash
cd speakforwater   # your repo root
npm install
npm run dev        # opens http://localhost:4321
npm run build      # produces dist/
```

### 7. Push and deploy

```bash
git add .
git commit -m "Migrate website to Astro"
git push origin main
```

Watch the Actions tab. Build takes ~1 minute. When green, visit
`https://speakforwater.com` — the new site is live.

## Connecting to the existing podcast pipeline

Your `run_pipeline.py` already publishes MP3s to `episodes/` and updates
`podcast.xml`. To make new episodes appear on the website:

### Option A — let `sync_episodes.py` do it (recommended)

Add a step to your existing `.github/workflows/podcast.yml` (or whatever
your daily workflow is called) — right after the publish step — that runs:

```yaml
- name: Sync episodes to website
  run: python scripts/sync_episodes.py
  env:
    GOOGLE_CREDENTIALS_JSON: ${{ secrets.GOOGLE_CREDENTIALS_JSON }}
    SPREADSHEET_ID: ${{ secrets.SPREADSHEET_ID }}

- name: Commit synced episodes
  run: |
    git add src/content/episodes/
    git commit -m "Sync episodes from Sheet" || echo "No changes"
    git push
```

This pulls every published row from your Google Sheet and creates a markdown
file per episode. The site then auto-rebuilds (because the `deploy.yml`
workflow triggers on push).

### Option B — generate the markdown directly inside `run_pipeline.py`

If you'd rather not run a separate script, port the logic of
`render_markdown()` from `scripts/sync_episodes.py` directly into the
`commit_episode` function in your existing `run_pipeline.py`. Write the
markdown alongside the MP3 commit.

## Adding episode metadata to your Sheet

For best results, add these columns (if they don't already exist):

| Column | Required | Example |
|---|---|---|
| `episode_number` | yes | `47` |
| `paper_title` | yes | `Groundwater depletion is reshaping...` |
| `paper_url` | yes | `https://doi.org/10.1038/...` |
| `journal` | recommended | `Nature Water` |
| `topics` | recommended | `groundwater, GRACE, remote sensing` |
| `description` | recommended | `One-line teaser` |
| `duration` | recommended | `11 min` |
| `show_notes` | optional | full markdown body |
| `status` | yes | `published` |
| `published_at` | yes | ISO date |

The sync script reads only rows where `status == published`.

## Cloudflare configuration

After the site goes live on `https://speakforwater.com`:

1. **SSL/TLS → Overview** → set to **Full**
2. **SSL/TLS → Edge Certificates** → enable **Always Use HTTPS** and **Automatic HTTPS Rewrites**
3. **Speed → Optimization** → enable **Brotli** and **Auto Minify** (HTML/CSS/JS)
4. **Caching → Configuration** → set Browser Cache TTL to "Respect Existing Headers"
5. (Optional) Once site is verified working, flip the orange cloud back ON for the GitHub Pages A records to get DDoS + CDN benefits

## Submitting to podcast directories

When you have ~5 episodes published and the site is live:

1. **Apple Podcasts:** [podcastsconnect.apple.com](https://podcastsconnect.apple.com) — submit RSS feed `https://speakforwater.com/podcast.xml`. Requires 3000×3000px artwork.
2. **Spotify:** [podcasters.spotify.com](https://podcasters.spotify.com) — paste RSS feed.
3. **Amazon Music:** [podcasters.amazon.com](https://podcasters.amazon.com)
4. **Pocket Casts / Overcast / Castro / AntennaPod** — auto-discovered once Apple lists you. No submission needed.

## Customizing

- **Colors / typography:** edit `src/styles/global.css`
- **Logo:** edit `public/favicon.svg` and the inline SVG in `src/components/Header.astro`
- **Footer text:** `src/components/Footer.astro`
- **About page copy:** `src/pages/about.astro`
- **Newsletter provider:** form action in `src/pages/subscribe.astro` is currently
  set to a Buttondown URL — replace with your provider (Substack, ConvertKit, Mailchimp, etc.)

## Troubleshooting

**Build fails: "missing audio_url"** — every episode markdown needs the
front-matter fields defined in `src/content/config.ts`.

**Custom domain not working** — check repo Settings → Pages shows
`speakforwater.com` and "Your site is published". DNS propagation can take
up to 24 hours.

**Audio not playing** — check the file is at `public/episodes/ep00X.mp3` (note the leading zeros).

**Cloudflare SSL errors (`too many redirects`)** — your SSL/TLS mode is set to "Flexible". Change it to **Full**.
