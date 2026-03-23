# SpeakForWater

Automated daily podcast generation from water resources research papers .

## How It Works

Every day, a GitHub Actions workflow automatically:

1. Reads the next queued paper URL from your **Google Sheet**
2. Launches a headless browser to automate **NotebookLM** (creates notebook, adds source, generates Audio Overview with your custom prompt)
3. Downloads the generated **MP3**
4. Commits it to this repo → **GitHub Pages** rebuilds your website
5. Updates the **RSS feed** (for Spotify, Apple Podcasts, etc.)
6. Marks the episode as published in Google Sheets

You just add paper URLs to the Google Sheet. Everything else is automatic.

## Architecture

```
Google Sheet          GitHub Actions              GitHub Pages
(episode queue)       (daily cron @ 06:00 UTC)    (website + RSS)

[paper_url] ──────→  run_pipeline.py              index.html
[paper_title]           │                         podcast.xml ──→ Spotify
[status: queued]        ├─ Read Sheet                              Apple Podcasts
                        ├─ Automate NotebookLM    episodes/
                        ├─ Download MP3             ep001.mp3
                        ├─ Git commit + push        ep001.json
                        └─ Update Sheet             ep002.mp3
                                                    ...
```

## Setup (One-Time, ~30 minutes)

### 1. Create the Google Sheet

Create a new Google Sheet with these columns in row 1:

| date | paper_url | paper_title | status | episode_number | mp3_url | published_at |
|------|-----------|-------------|--------|----------------|---------|--------------|

Add your first few papers with `status` = `queued`.

### 2. Create a Google Service Account

This lets the GitHub Action read/write your Sheet without a browser login.

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (e.g., "SpeakForWater")
3. Enable the **Google Sheets API** and **Google Drive API**
4. Go to **Credentials** → **Create Credentials** → **Service Account**
5. Download the JSON key file
6. **Share your Google Sheet** with the service account email (the `client_email` in the JSON)

### 3. Export NotebookLM Cookies

1. Install the **"Get cookies.txt LOCALLY"** browser extension
2. Go to `https://notebooklm.google.com` (while logged in)
3. Click the extension → Export (Netscape format)
4. Copy the entire contents of the file (you'll paste it as a secret)

### 4. Fork/Clone This Repo

```bash
# Option A: Use as template
# Click "Use this template" on GitHub

# Option B: Clone
git clone https://github.com/YOUR_USERNAME/speakforwater.git
```

### 5. Set GitHub Secrets

Go to your repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**:

| Secret Name | Value |
|------------|-------|
| `GOOGLE_CREDENTIALS_JSON` | Paste the entire service account JSON key |
| `SPREADSHEET_ID` | The ID from your Google Sheet URL: `docs.google.com/spreadsheets/d/THIS_PART/edit` |
| `NOTEBOOKLM_COOKIES` | Paste the entire contents of cookies.txt |
| `SITE_URL` | Your GitHub Pages URL, e.g. `https://yourusername.github.io/speakforwater` |

### 6. Enable GitHub Pages

Go to repo **Settings** → **Pages** → Source: **Deploy from a branch** → Branch: `main` / `root` → Save.

### 7. Test It

Go to **Actions** tab → **Generate Podcast** → **Run workflow** (manual trigger).

Watch the logs — it should pick up your first queued paper, generate the podcast, and commit the MP3.

## Google Sheet Format

| Column | Field | Description | Example |
|--------|-------|-------------|---------|
| A | `date` | Scheduled date | 2026-03-22 |
| B | `paper_url` | Full paper URL | https://www.sciencedirect.com/... |
| C | `paper_title` | Episode title | Flood Risk Mapping with SAR |
| D | `status` | `queued` → `processing` → `published` or `failed` | queued |
| E | `episode_number` | Episode number (integer) | 42 |
| F | `mp3_url` | Filled automatically after publish | (auto) |
| G | `published_at` | Filled automatically | (auto) |

Just add rows with `status = queued`. The workflow processes the first queued row each day.

## Customizing the Podcast Prompt

Edit `app/services/prompt_manager.py` to change the default prompt. The current prompt creates a conversation between **Anna** (journalist) and **Ehsan** (researcher) targeting non-expert water users.

## Submitting to Podcast Platforms

Once you have a few episodes and your RSS feed is live at `https://yourusername.github.io/speakforwater/podcast.xml`:

| Platform | Where to Submit |
|----------|----------------|
| Spotify | [podcasters.spotify.com](https://podcasters.spotify.com) |
| Apple Podcasts | [podcastsconnect.apple.com](https://podcastsconnect.apple.com) |
| Google Podcasts | Automatic (indexes RSS feeds) |

Submit the RSS URL once. Platforms auto-pull new episodes after that.

## Maintenance

**Cookies refresh (monthly):** Google login cookies expire every 2-4 weeks. Re-export cookies.txt and update the `NOTEBOOKLM_COOKIES` secret. The workflow will fail with a "redirected to Google login" error when cookies expire — that's your signal to refresh them.

**Selector updates (rare):** If Google updates the NotebookLM UI, the Playwright selectors in `app/services/notebooklm.py` may need tweaking. The workflow logs will show which selector failed.

## Project Structure

```
speakforwater/
├── .github/workflows/
│   └── generate-podcast.yml    # GitHub Actions daily workflow
├── app/
│   ├── config.py               # Settings
│   ├── main.py                 # FastAPI server (optional, for local dev)
│   ├── models/schemas.py       # Data models
│   ├── routes/                 # API routes (optional)
│   └── services/
│       ├── notebooklm.py       # Playwright automation (core)
│       ├── prompt_manager.py   # Your SpeakForWater prompt
│       ├── google_sheets.py    # Sheet read/write
│       ├── rss_generator.py    # Podcast RSS feed generator
│       └── task_manager.py     # Task tracking (for API mode)
├── episodes/                   # Generated MP3s + metadata
├── run_pipeline.py             # Main pipeline script (used by Actions)
├── index.html                  # GitHub Pages website
├── podcast.xml                 # RSS feed (auto-generated)
├── docker-compose.yml          # Optional: local Docker setup
├── Dockerfile                  # Optional: container build
└── requirements.txt
```

## Cost

Everything is free:

| Component | Cost |
|-----------|------|
| GitHub Actions | Free (2000 min/month for public repos) |
| GitHub Pages | Free |
| Google Sheets | Free |
| NotebookLM | Free (or $20/mo for PRO) |
| Spotify / Apple Podcasts | Free |

Each podcast run uses ~15 minutes of Actions time, so even daily runs only use ~450 min/month out of your 2000 free minutes.

## License

MIT
