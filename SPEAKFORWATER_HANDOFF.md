# SpeakForWater — Project Handoff

> Comprehensive summary of the SpeakForWater project for continuing work in Claude Code.
> Last updated: 2026-06-04

---

## 1. Project Overview

**SpeakForWater** is a daily AI-narrated podcast that turns peer-reviewed open-access water research papers into 10-minute conversational episodes for non-scientists (farmers, homeowners, water managers, policy-makers, etc.).

- **Owner:** Ehsan Kahrizi (kahriziehsan490@gmail.com)
- **Domain:** speakforwater.com (Cloudflare DNS + GitHub Pages)
- **GitHub repo:** https://github.com/Ehsankahrizi/speakforwater
- **Local working directory:** `/Users/ehsankahrizi/speakforwater`
- **Mission:** Translate technical water research into plain-language audio that anyone can understand.
- **Hosts (AI-generated voices):**
  - **Anna** (female journalist) — runs the show
  - **Ehsan** (male water-resources researcher) — explains the science

---

## 2. Architecture

### Daily Pipeline (runs via GitHub Actions cron)

```
Search Papers (04:00 UTC)
  └─ search_papers.py
     ├─ OpenAlex API search (configured keywords + journals)
     ├─ app/services/paper_ranker.py → Groq Llama 3.1 8B scoring
     │  (novelty, impact, accessibility, audience_fit, threshold ≥ 7)
     └─ Add accepted papers to Google Sheet (status=queued)

Generate Podcast (06:00 UTC)
  └─ run_pipeline.py
     ├─ Read next queued row from Google Sheet
     ├─ Override episode_number with sequential display number (count of published + 1)
     ├─ app/services/notebooklm.py → notebooklm-py SDK → NotebookLM Audio Overview
     │  (uses config/podcast_prompt.yml with TV-news format, gender-locked voices)
     ├─ ffmpeg stitch intro/outro → final MP3
     ├─ ffprobe → real duration (replaces hardcoded "10 min")
     ├─ app/services/title_simplifier.py → Groq Llama → 8-10 word cover title
     ├─ app/services/cover_generator.py → cover.png/cover2.png template + Pillow + Montserrat
     ├─ (Disabled) app/services/video_generator.py + youtube_publisher.py
     │  Behind YOUTUBE_ENABLED feature flag — currently OFF
     ├─ Write public/episodes/epXXX.mp3 + epXXX.json
     ├─ scripts/json_to_md.py → src/content/episodes/XXX-slug.md
     ├─ Regenerate public/podcast.xml
     └─ Commit + push to main

Deploy site (auto)
  └─ .github/workflows/deploy.yml
     └─ Triggered by:
        - push to main, OR
        - workflow_run on "Generate Podcast" completion (bypasses GITHUB_TOKEN limitation)
     └─ Astro build → upload-pages-artifact → deploy-pages
```

### Website (Astro + GitHub Pages)

- **Hero**: full-screen video (`public/movie_1.mp4`, 60-sec, slow-played at 0.5×) with SVG wave animation
- **Intro player**: glass-effect player below subscribe buttons (loads `/ep000.m4a`)
- **Episodes archive**: sorted by `episode_number` descending (newest first, top-left → bottom-right)
- **No featured/pinned episode** — just a grid of recent + "View all"
- **Custom domain**: `speakforwater.com` via Cloudflare (DNS-only, no proxy)
- **HTTPS**: GitHub Pages-issued Let's Encrypt cert

---

## 3. Key Files

### Pipeline / Backend
| File | Purpose |
|------|---------|
| `run_pipeline.py` | Main daily pipeline orchestrator |
| `search_papers.py` | OpenAlex paper search + AI ranking trigger |
| `app/services/notebooklm.py` | NotebookLM SDK wrapper |
| `app/services/paper_search.py` | OpenAlex search helpers |
| `app/services/paper_ranker.py` | Groq Llama scoring (novelty/impact/accessibility/fit) |
| `app/services/prompt_manager.py` | Loads `config/podcast_prompt.yml` |
| `app/services/google_sheets.py` | EpisodeQueue class for the Sheet |
| `app/services/rss_generator.py` | Builds `public/podcast.xml` |
| `app/services/audio_stitcher.py` | ffmpeg intro/outro stitching |
| `app/services/cover_generator.py` | Pillow + Montserrat cover.png generator |
| `app/services/title_simplifier.py` | Groq Llama → 8-10 word listener title |
| `app/services/video_generator.py` | (Disabled) ffmpeg MP3 + cover → MP4 |
| `app/services/youtube_publisher.py` | (Disabled) YouTube Data API v3 upload |

### Scripts (one-off / utility)
| File | Purpose |
|------|---------|
| `scripts/json_to_md.py` | Convert epXXX.json → Astro content markdown |
| `scripts/sync_episodes.py` | Sheet → markdown sync (alternative path) |
| `scripts/reset_queue.py` | Reset Sheet status + renumber episode_number |
| `scripts/setup_youtube_oauth.py` | One-time local OAuth flow for YouTube |

### Website (Astro)
| File | Purpose |
|------|---------|
| `src/pages/index.astro` | Homepage with hero + intro player + episode grid |
| `src/pages/episodes/index.astro` | Full episode archive (sortable by episode_number desc) |
| `src/pages/episodes/[slug].astro` | Single-episode page with player + show notes |
| `src/pages/about.astro` | About page |
| `src/pages/subscribe.astro` | Subscribe links + RSS |
| `src/pages/disclaimer.astro` | 10-section legal disclaimer |
| `src/pages/404.astro` | 404 page |
| `src/components/Header.astro` | Nav with transparent-over-hero mode, Home tab |
| `src/components/Footer.astro` | Footer with disclaimer link |
| `src/components/AudioPlayer.astro` | Reusable audio player component |
| `src/components/EpisodeCard.astro` | Episode card (no date, just duration) |
| `src/components/SubscribeButtons.astro` | Apple / Spotify / RSS / Newsletter |
| `src/content/config.ts` | Zod schema for episode markdown frontmatter |
| `src/styles/global.css` | Design tokens |
| `astro.config.mjs` | Astro + sitemap integration |
| `public/cover.png` / `public/cover2.png` | Cover image templates |
| `public/movie_1.mp4` | Hero background video |
| `public/ep000.m4a` | Introduction episode audio |
| `public/podcast.xml` | RSS feed |
| `public/CNAME` | speakforwater.com |

### Workflows
| File | Trigger | Purpose |
|------|---------|---------|
| `.github/workflows/search-papers.yml` | cron daily 04:00 UTC + manual | Find new papers + AI rank |
| `.github/workflows/generate-podcast.yml` | cron daily 06:00 UTC + manual | Generate next queued episode |
| `.github/workflows/deploy.yml` | push to main + workflow_run on Generate Podcast | Build & deploy Astro site |
| `.github/workflows/reset_queue.yml` | manual only | Reset Sheet status + renumber |

### Config
| File | Purpose |
|------|---------|
| `config/podcast_prompt.yml` | NotebookLM prompt (TV-news format, gender-locked) |
| `config/keywords.yml` | Paper search keywords |
| `config/journals.yml` | Journal sources for OpenAlex |

---

## 4. GitHub Secrets & Variables

### Secrets (Settings → Secrets and variables → Actions)
- `GOOGLE_CREDENTIALS_JSON` — service account JSON for Sheets
- `SPREADSHEET_ID` — Google Sheet ID
- `SHEET_NAME` — usually "Sheet1"
- `NOTEBOOKLM_AUTH_JSON` — Google session JSON (refresh manually when expired)
- `GROQ_API_KEY` — free tier, generates 14,400 req/day max
- `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN` — YouTube OAuth (currently unused)
- `SITE_URL` — `https://speakforwater.com`

### Repository Variables (Settings → Variables)
- `YOUTUBE_ENABLED` — set to `"true"` to enable YouTube auto-publish (currently OFF for legal safety)

### Workflow-level env (in `.github/workflows/generate-podcast.yml`)
- `TITLE_PX_X1=219, TITLE_PX_Y1=191, TITLE_PX_X2=1006, TITLE_PX_Y2=542`
- `TITLE_FONT_MAX=48, EP_FONT_MAX=34`
- `EP_CENTER_X=613, EP_CENTER_Y=150`

---

## 5. Cover Image Design

- **Template:** `public/cover2.png` (preferred), fallback to `public/cover.png`
- **Title box** (where the simplified title goes):
  - Top-left: `(219, 191)`
  - Bottom-right: `(1006, 542)`
  - Font: Montserrat ExtraBold (system path or fallback to DejaVu)
  - Color: `#082B5A`
  - Auto-fits between min 18pt and max 48pt
- **Episode anchor** (where "EPISODE N" text is centered):
  - Center: `(613, 150)`
  - Font: Montserrat SemiBold
  - Color: `#00AEEF`
  - Max 34pt
- **Short title generation:** Groq Llama 3.1 8B simplifies the technical paper title to 8-10 words for the cover (e.g., "Sustainable Recirculating Aquaculture Systems (RAS)" → "Sustainable fish farming systems explained")

To tune any of these without touching code, edit the env block in `generate-podcast.yml` or for local testing:

```bash
TITLE_PX_X1=219 TITLE_PX_Y1=191 TITLE_PX_X2=1006 TITLE_PX_Y2=542 \
TITLE_FONT_MAX=48 EP_FONT_MAX=34 \
EP_CENTER_X=613 EP_CENTER_Y=150 \
python3 -c "
from pathlib import Path
from app.services.cover_generator import make_cover
make_cover(
    output_path=Path('/tmp/test_cover.png'),
    title='Original paper title here',
    episode_number=72,
    cover_title='Short title here',
)
" && open /tmp/test_cover.png
```

---

## 6. NotebookLM Prompt Highlights

Located in `config/podcast_prompt.yml`. Key features:

- **TV-news format**: Anna opens alone (~90 seconds), then invites Ehsan
- **Voice gender lock** (NotebookLM tends to mis-assign):
  - ANNA = WOMAN, feminine voice
  - EHSAN = MAN, masculine voice
  - Repeated warnings throughout the prompt
- **Natural variation**: 5-6 different opening lines, greeting variations, closings
- **Audience**: farmers, homeowners, citizens — NOT scientists
- **Episode structure**: warm open → topic teaser → welcome guest → why it matters → what they did → key findings → practical takeaways → limitations → closing
- **Style rules**: contractions, gentle humor, no jargon, real-life consequences
- **Final voice reminder** at the bottom

---

## 7. Legal Disclaimer

A `/disclaimer` page is live with 10 sections covering:
1. Nature of the content (AI-generated, transparency)
2. Not the original authors' work or voice
3. Editorial limitations
4. Not professional advice
5. Copyright & fair use (7-day takedown response)
6. Trademarks
7. No warranty; limitation of liability
8. Right to modify
9. Reporting errors
10. Contact: `hello@speakforwater.com`

A shortened version is auto-appended to every YouTube description (when YouTube is enabled).

---

## 8. Recent State (as of 2026-06-04)

### Working
- ✅ Daily paper search with Groq AI ranking
- ✅ Daily podcast generation via NotebookLM
- ✅ Cover image with Montserrat fonts, tuned coordinates, short titles
- ✅ Real durations (ffprobe, not hardcoded)
- ✅ Auto-deploy via `workflow_run` trigger
- ✅ Intro player on homepage
- ✅ 60-second hero video
- ✅ Unpinned/un-featured latest episode
- ✅ Home / Episodes / About / Subscribe nav
- ✅ Disclaimer page + footer link
- ✅ Episodes now sort by `episode_number` desc (newest first)

### Intentionally Off
- ❌ YouTube auto-publish — disabled via `YOUTUBE_ENABLED` repo variable (legal caution)
- ❌ Quality agent (Whisper + Llama review) — abandoned (NotebookLM regeneration not feasible)

### Open Items / Things to Verify
- Title simplifier (Groq Llama) sometimes returns the full title; need to inspect workflow logs (`[cover]` and `title_simplifier` lines)
- Whether ordering on website is now correct after the latest sort fix
- Whether cover renders properly with current pixel coords

---

## 9. Common Commands

### Edit & push from local Mac
```bash
cd /Users/ehsankahrizi/speakforwater
# edit files...
git pull --rebase --autostash
git add -A
git commit -m "your message"
git push
```

### Trigger workflows manually
- Generate Podcast: https://github.com/Ehsankahrizi/speakforwater/actions/workflows/generate-podcast.yml
- Search Papers: https://github.com/Ehsankahrizi/speakforwater/actions/workflows/search-papers.yml
- Deploy site: https://github.com/Ehsankahrizi/speakforwater/actions/workflows/deploy.yml
- Reset queue: https://github.com/Ehsankahrizi/speakforwater/actions/workflows/reset_queue.yml

### Refresh NotebookLM auth (when token expires)
```bash
cd /Users/ehsankahrizi/speakforwater
notebooklm login
# Then copy contents of ~/.notebooklm/storage_state.json
# Paste into GitHub secret NOTEBOOKLM_AUTH_JSON
```

### Local test of cover generation
```bash
cd /Users/ehsankahrizi/speakforwater
TITLE_PX_X1=219 TITLE_PX_Y1=191 TITLE_PX_X2=1006 TITLE_PX_Y2=542 \
TITLE_FONT_MAX=48 EP_FONT_MAX=34 \
EP_CENTER_X=613 EP_CENTER_Y=150 \
python3 -c "
from pathlib import Path
from app.services.cover_generator import make_cover
make_cover(
    output_path=Path('/tmp/test_cover.png'),
    title='Sustainable Recirculating Aquaculture Systems (RAS): Development and Challenges',
    episode_number=72,
    cover_title='Sustainable fish farming systems and their challenges',
)
" && open /tmp/test_cover.png
```

---

## 10. Future Ideas (mentioned but not yet built)

- **LinkedIn auto-publish** — likely via Make.com (free tier) or n8n self-hosted
- **Telegram bot** — Cloudflare Worker custom bot for approval/feedback on phone
- **Submit to Apple Podcasts / Spotify / Amazon** — RSS at `https://speakforwater.com/podcast.xml`
- **Analytics dashboard** — Plausible or Cloudflare Web Analytics
- **Newsletter** — Buttondown free tier (form already on `/subscribe`)
- **Author outreach** — auto-email paper authors after publishing
- **Re-enable YouTube** — flip `YOUTUBE_ENABLED` variable when comfortable

---

## 11. Key Decisions / Rationale

- **GitHub Actions over self-hosted**: free, no maintenance, scales fine for daily cron
- **Astro + GitHub Pages over Vercel/Netlify**: free, fast, simple, ties to existing repo
- **NotebookLM over custom TTS pipeline**: quality is dramatically better, free
- **Groq Llama 3.1 over Claude/OpenAI**: free tier (14,400 req/day) covers daily use 480× over
- **Brand Channel on YouTube under personal Google account**: cleaner branding, easier to transfer, no separate login
- **Cloudflare DNS-only (not proxy) for GitHub Pages**: avoids "too many redirects" SSL issues
- **`workflow_run` trigger for deploy**: required because GITHUB_TOKEN pushes don't fire `push` events
- **Sort by `episode_number` desc**: more predictable than `pub_date` when multiple episodes share a date
- **Real ffprobe duration**: original pipeline hardcoded "10 min"; now shows actual length on cards
- **TV-news format with Anna alone first**: best heuristic for NotebookLM voice assignment (female voice for first speaker)

---

## 12. Where to Continue

When you pick this up in Claude Code, start by:
1. `cd /Users/ehsankahrizi/speakforwater`
2. `git pull` to sync latest
3. Check Actions tab for recent workflow status
4. If anything looks off, search workflow logs for `[cover]` or `title_simplifier` debug lines
5. To make changes: edit, `git add -A && git commit -m "..." && git push`
6. Deploy triggers automatically via `workflow_run`

The most fragile parts of the system are:
- **NotebookLM auth** (manual refresh every few weeks)
- **Voice assignment** in NotebookLM (may need prompt tweaks)
- **Cover layout** (pixel coords are template-specific)

Most everything else is robust and self-healing.
