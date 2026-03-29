# SpeakForWater

Automated daily podcast generation from water resources research papers.


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

## License

MIT
