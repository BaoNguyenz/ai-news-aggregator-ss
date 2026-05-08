# AI News Aggregator

An intelligent, fully automated system that scrapes AI-related content from multiple sources, enriches it with full-text content, generates LLM-powered summaries, curates a personalized daily digest based on your profile, and delivers it straight to your inbox every morning.

## Overview

This project aggregates AI news from three sources:

- **YouTube Channels** — Scrapes video metadata and fetches full transcripts via the YouTube Transcript API
- **OpenAI RSS Feed** — Monitors the OpenAI blog for new posts
- **Anthropic RSS Feeds** — Monitors Anthropic's news, research, and engineering blogs; fetches and converts full article HTML to Markdown

All content is processed by a **3-agent LLM pipeline** (OpenAI API) that summarizes articles, ranks them by personal relevance, and composes a clean HTML email digest — sent automatically every day.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Daily Cron Job (05:00 UTC)                │
└─────────────────────────┬───────────────────────────────────┘
                          │
          ┌───────────────▼───────────────┐
          │         [0] Bootstrap          │
          │   Create DB tables if needed   │
          └───────────────┬───────────────┘
                          │
          ┌───────────────▼───────────────┐
          │         [1] Scraping           │
          │  YouTube RSS → video metadata  │
          │  OpenAI RSS  → article metadata│
          │  Anthropic RSS → article meta  │
          └───────────────┬───────────────┘
                          │ saves raw records
                          ▼
                    ┌──────────┐
                    │PostgreSQL│
                    │          │
                    │ youtube  │
                    │ _videos  │
                    │          │
                    │ openai   │
                    │_articles │
                    │          │
                    │anthropic │
                    │_articles │
                    └──────────┘
                          │
          ┌───────────────▼───────────────┐
          │       [2] Processing           │
          │  Anthropic: fetch URL → HTML   │
          │             → convert Markdown │
          │  YouTube:   fetch transcript   │
          │  OpenAI:    (uses RSS desc.)   │
          └───────────────┬───────────────┘
                          │ updates content columns
                          ▼
                    ┌──────────┐
                    │PostgreSQL│
                    │          │
                    │anthropic │
                    │.markdown │← full article text
                    │          │
                    │youtube   │
                    │.transcript│← full transcript
                    └──────────┘
                          │
          ┌───────────────▼───────────────┐
          │    [3] Digest Generation       │
          │  DigestAgent (gpt-4o-mini)     │
          │  Reads full content per article│
          │  → generates title + summary   │
          └───────────────┬───────────────┘
                          │ saves summaries
                          ▼
                    ┌──────────┐
                    │PostgreSQL│
                    │          │
                    │ digests  │
                    │title     │
                    │summary   │
                    │sent_at   │
                    └──────────┘
                          │
          ┌───────────────▼───────────────┐
          │    [4] Curation & Ranking      │
          │  CuratorAgent (gpt-4.1)        │
          │  Scores each digest 0–10       │
          │  against your user profile     │
          │  → returns ranked top-10 list  │
          └───────────────┬───────────────┘
                          │ (in-memory, not persisted)
          ┌───────────────▼───────────────┐
          │    [5] Email Delivery          │
          │  EmailAgent (gpt-4o-mini)      │
          │  Writes personalized greeting  │
          │  + introduction for the email  │
          │  → Gmail SMTP sends HTML email │
          │  → marks digests as sent       │
          └───────────────────────────────┘
```

---

## How It Works — Step by Step

### Step 0 — Database Bootstrap

On every run, the pipeline calls `Base.metadata.create_all(engine)` to ensure all PostgreSQL tables exist before any data access. Safe to run repeatedly — will not overwrite existing data.

---

### Step 1 — Scraping

All three scrapers use **RSS feeds** as their data source. RSS feeds return only lightweight metadata (title, URL, short description, publish date) — **not** the full article content.

| Source | RSS provides | Stored in |
|--------|-------------|-----------|
| **YouTube** | title, URL, video ID, short description | `youtube_videos` — `transcript = NULL` initially |
| **OpenAI** | title, URL, GUID, short description | `openai_articles` — description used as-is |
| **Anthropic** | title, URL, GUID, short description | `anthropic_articles` — `markdown = NULL` initially |

Each scraper only fetches articles published within the configured time window (default: last 48 hours). Duplicate detection uses `video_id` / `guid` as primary keys — re-running will not create duplicates.

---

### Step 2 — Processing (Content Enrichment)

Since RSS only provides short descriptions, two sources require additional fetching:

**Anthropic** — Full article text is not available via RSS:
1. The scraper's stored `url` is fetched with an HTTP GET request
2. The returned HTML is converted to Markdown using `html-to-markdown`
3. The Markdown is saved to `anthropic_articles.markdown`

**YouTube** — Video content is not available via RSS:
1. The `YouTubeTranscriptApi` fetches the transcript for each video
2. All transcript snippets are joined into a single text string
3. Saved to `youtube_videos.transcript`
4. If transcripts are unavailable (disabled/private), the video is marked `__UNAVAILABLE__` and excluded from digest generation

**OpenAI** — No additional processing:
- The short RSS `description` is used directly as the article content in subsequent steps

---

### Step 3 — Digest Generation

`DigestAgent` (model: `gpt-4o-mini`) reads the full content of every article that does not yet have a digest:

- Anthropic articles → reads `markdown` column
- YouTube videos → reads `transcript` column
- OpenAI articles → reads `description` column

For each article, it generates:
- **title** — a concise 5–10 word headline
- **summary** — a 2–3 sentence summary focusing on key insights and practical value

Results are stored in the `digests` table. Articles are never digested twice (checked by `article_type:article_id` composite key).

---

### Step 4 — Curation & Ranking

`CuratorAgent` (model: `gpt-4.1`) receives all recent digests alongside your user profile (interests, background, expertise level) and scores each article from 0.0 to 10.0 for personal relevance.

The ranking result is **not persisted to the database** — it exists only in memory for the current pipeline run and is passed directly into the email generation step.

Scoring guidelines used by the model:
- 9–10: Directly aligns with your stated interests, high value
- 7–8.9: Strong alignment, good value
- 5–6.9: Moderate relevance
- Below 5: Low relevance

---

### Step 5 — Email Delivery

`EmailAgent` (model: `gpt-4o-mini`) receives the top-N ranked articles and generates:
- A personalized **greeting** with your name and today's date
- A 2–3 sentence **introduction** previewing the day's top content

The final email is assembled as HTML and sent via **Gmail SMTP (port 465, SSL)**. After sending, all included digest IDs are marked with `sent_at = now()` to prevent them from appearing in future emails.

---

## Project Structure

```
ai-news-aggregator/
├── main.py                    # Entry point
├── render.yaml                # Render.com deployment config
├── Dockerfile
├── pyproject.toml
└── app/
    ├── config.py              # YouTube channel IDs to monitor
    ├── daily_runner.py        # Pipeline orchestrator (5 steps)
    ├── runner.py              # Scraper registry and executor
    ├── profiles/
    │   └── user_profile.py    # Your interests, background, expertise
    ├── scrapers/
    │   ├── base.py            # BaseScraper — RSS parsing via feedparser
    │   ├── anthropic.py       # Anthropic scraper + HTML→Markdown
    │   ├── openai.py          # OpenAI RSS scraper
    │   └── youtube.py         # YouTube video + transcript scraper
    ├── database/
    │   ├── models.py          # SQLAlchemy table definitions
    │   ├── repository.py      # All database read/write operations
    │   ├── connection.py      # DB connection and environment detection
    │   ├── create_tables.py   # Manual table initializer
    │   └── check_connection.py
    ├── agent/
    │   ├── base.py            # BaseAgent — OpenAI client setup
    │   ├── digest_agent.py    # gpt-4o-mini: summarization
    │   ├── curator_agent.py   # gpt-4.1: personalized ranking
    │   └── email_agent.py     # gpt-4o-mini: email introduction
    └── services/
        ├── base.py            # BaseProcessService — shared processing loop
        ├── process_anthropic.py
        ├── process_youtube.py
        ├── process_digest.py
        ├── process_curator.py
        ├── process_email.py
        └── email.py           # Gmail SMTP sender + HTML formatter
```

---

## Database Schema

```
youtube_videos                  openai_articles
──────────────────────          ──────────────────────
video_id        PK              guid            PK
title                           title
url                             url
channel_id                      description     (used as content)
published_at                    published_at
description                     category
transcript      ← nullable      created_at
created_at

anthropic_articles              digests
──────────────────────          ──────────────────────
guid            PK              id              PK  (article_type:article_id)
title                           article_type        (youtube | openai | anthropic)
url                             article_id
description                     url
published_at                    title           ← LLM-generated
category                        summary         ← LLM-generated
markdown        ← nullable      created_at
created_at                      sent_at         ← NULL until email is sent
```

---

## Setup

### Prerequisites

- Python 3.12+
- PostgreSQL database (local or hosted)
- OpenAI API key
- Gmail account with an [App Password](https://support.google.com/accounts/answer/185833) enabled
- (Optional) [Webshare](https://www.webshare.io/) proxy credentials for YouTube transcript fetching

### Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/your-username/ai-news-aggregator.git
   cd ai-news-aggregator
   ```

2. Install dependencies:
   ```bash
   uv sync
   ```

3. Configure environment variables — copy `app/example.env` to `app/.env`:
   ```env
   OPENAI_API_KEY=your_openai_api_key
   MY_EMAIL=your_email@gmail.com
   APP_PASSWORD=your_gmail_app_password

   # Option A: Full DATABASE_URL (recommended)
   DATABASE_URL=postgresql://user:password@host:5432/ai_news_aggregator

   # Option B: Individual components (falls back to localhost)
   # POSTGRES_USER=postgres
   # POSTGRES_PASSWORD=postgres
   # POSTGRES_HOST=localhost
   # POSTGRES_PORT=5432
   # POSTGRES_DB=ai_news_aggregator

   # Optional: Webshare proxy for YouTube transcript fetching
   # WEBSHARE_USERNAME=your_username
   # WEBSHARE_PASSWORD=your_password
   ```

4. Initialize the database:
   ```bash
   uv run python -m app.database.create_tables
   ```
   
   Or verify the connection first:
   ```bash
   uv run python -m app.database.check_connection
   ```

5. Configure YouTube channels to monitor in `app/config.py`:
   ```python
   YOUTUBE_CHANNELS = [
       "UCawZsQWqfGSbCI5yjkdVkTA",  # Matthew Berman
   ]
   ```

6. Update your personal profile in `app/profiles/user_profile.py` to customize curation.

### Running

**Full pipeline:**
```bash
uv run main.py
```

**Individual steps (for debugging):**
```bash
uv run python -m app.runner                    # Step 1: Scraping only
uv run python -m app.services.process_anthropic  # Step 2a: Anthropic markdown
uv run python -m app.services.process_youtube    # Step 2b: YouTube transcripts
uv run python -m app.services.process_digest     # Step 3: Digest generation
uv run python -m app.services.process_curator    # Step 4: Curation/ranking
uv run python -m app.services.process_email      # Step 5: Email delivery
```

---

## Deployment on Render.com

The project is pre-configured for Render.com via `render.yaml`:

- **Service type**: Cron Job (not a web service — no always-on cost)
- **Runtime**: Docker
- **Schedule**: `0 5 * * *` — runs daily at 05:00 UTC (12:00 noon Vietnam time)
- **Command**: `python main.py`
- **Database**: PostgreSQL (free tier, connection injected automatically via `DATABASE_URL`)

See `docs/DEPLOYMENT.md` for full deployment instructions.

### Docker

```bash
docker build -t ai-news-aggregator .
docker run --env-file app/.env ai-news-aggregator
```

---

## Adding New Sources

### New RSS Scraper

1. Create `app/scrapers/my_source.py`:
   ```python
   from typing import List
   from .base import BaseScraper, Article

   class MySourceScraper(BaseScraper):
       @property
       def rss_urls(self) -> List[str]:
           return ["https://example.com/feed.xml"]

       def get_articles(self, hours: int = 24) -> List[Article]:
           return [Article(**a.model_dump()) for a in super().get_articles(hours)]
   ```

2. Register it in `app/runner.py`:
   ```python
   from .scrapers.my_source import MySourceScraper

   SCRAPER_REGISTRY = [
       # ... existing scrapers
       ("my_source", MySourceScraper(),
        lambda s, r, h: _save_rss_articles(s, r, h, r.bulk_create_my_articles)),
   ]
   ```

---

## Technology Stack

| Category | Technology |
|----------|-----------|
| Language | Python 3.12+ |
| Package manager | uv |
| Database | PostgreSQL |
| ORM | SQLAlchemy 2.0 |
| Data validation | Pydantic v2 |
| LLM | OpenAI API (gpt-4o-mini, gpt-4.1) |
| RSS parsing | feedparser |
| HTML → Markdown | html-to-markdown |
| YouTube data | youtube-transcript-api |
| Email delivery | Gmail SMTP (smtplib) |
| Containerization | Docker |
| Deployment | Render.com Cron Job |

---

## License

MIT