# Family Calendar Capture

Family Calendar Capture is a small FastAPI app that turns natural-language plans into an Apple Calendar-compatible `.ics` subscription feed. It is designed for busy households: paste a school email, type a rough weekly plan, preview the events the LLM extracted, and save them to a feed that Apple Calendar can subscribe to.

This project is intentionally compact, but it demonstrates production-oriented decisions: provider-agnostic LLM integration, deterministic calendar normalization, token-protected feed URLs, persistent SQLite storage, tests, Docker support, and a responsive HTMX-powered interface.

## Highlights

- **Natural-language event capture** — enter one message with one or many events.
- **Live preview before saving** — HTMX calls `/preview` as the user types so extracted details can be reviewed first.
- **Multiple LLM providers** — OpenAI, Anthropic, and local Ollama are supported through `LLM_PROVIDER`.
- **Apple Calendar subscription feed** — saved events are exposed as a standards-friendly `.ics` feed.
- **Safer public sharing** — optional `CALENDAR_FEED_TOKEN` moves the feed to `/calendar/<token>.ics` and disables the plain `/calendar.ics` route.
- **Practical event normalization** — all-day inference, default one-hour durations, recurring events, exception dates, URLs, video-call locations, attachments, geo fields, and multiple reminders.
- **Simple deployment path** — run locally with Uvicorn, with Docker Compose, or behind a Cloudflare Tunnel on a home server/VPS.

## Tech Stack

| Area | Choices |
| --- | --- |
| Backend | FastAPI, Uvicorn |
| UI | Jinja2 templates, HTMX, vanilla CSS/JavaScript |
| Data | SQLite |
| LLM providers | OpenAI Responses API, Anthropic Messages API, Ollama `/api/generate` |
| Calendar output | iCalendar `.ics` feed with `VEVENT`, `RRULE`, `EXDATE`, and `VALARM` support |
| Tests | Python `unittest` |
| Deployment | Docker, Docker Compose, optional systemd + Cloudflare Tunnel |

## How It Works

1. A user enters free-form text such as:

   ```text
   Soccer practice every Tuesday at 6pm next month. Dentist Friday at 9am. Remind us 1 day before.
   ```

2. The app sends a structured extraction prompt to the configured LLM provider.
3. The response is normalized into calendar events with consistent defaults.
4. The preview panel shows the extracted event details.
5. When saved, events are persisted to SQLite and published through the `.ics` feed.
6. Apple Calendar subscribes to the feed and refreshes it like any other calendar subscription.

## Local Development

### 1. Create a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure environment variables

Create a `.env` file in the project root:

```bash
LLM_PROVIDER=openai
OPENAI_API_KEY=replace-me

# Optional but recommended for any public deployment:
# Generate with: openssl rand -hex 32
CALENDAR_FEED_TOKEN=replace-with-a-long-random-token

# Alternative providers:
# LLM_PROVIDER=anthropic
# ANTHROPIC_API_KEY=replace-me
#
# LLM_PROVIDER=ollama
# OLLAMA_BASE_URL=http://localhost:11434
```

Supported providers are `openai`, `anthropic`, and `ollama`.

### 3. Run the app

```bash
uvicorn app:app --reload
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000).

## Docker Compose

Docker Compose runs the app on host port `8080` and stores the SQLite database in `./data/events.db`.

```bash
# Create .env with LLM_PROVIDER, provider credentials, and CALENDAR_FEED_TOKEN first.
docker compose up --build
```

Then open [http://127.0.0.1:8080](http://127.0.0.1:8080).

> `docker-compose.yaml` requires `CALENDAR_FEED_TOKEN` to be set before startup. This is intentional so the calendar feed is not accidentally exposed at a predictable public URL.

## Calendar Subscription

When `CALENDAR_FEED_TOKEN` is **not** set, the feed is available at:

```text
http://127.0.0.1:8000/calendar.ics
```

When `CALENDAR_FEED_TOKEN` **is** set, the plain feed route returns `404` and the feed moves to:

```text
http://127.0.0.1:8000/calendar/<token>.ics
```

To subscribe from Apple Calendar:

1. Open Apple Calendar.
2. Choose **File > New Calendar Subscription**.
3. Paste the HTTP or HTTPS feed URL shown by the app.
4. Choose a refresh interval and save.

The app also includes an **Open in Apple Calendar** button that converts the feed URL to a `webcal://` launch URL for convenience.

## Shortcut-Friendly Endpoint

The `/shortcuts/add` endpoint accepts a `text` query parameter, extracts events, saves them, and redirects back to the home page. It is useful for building a Siri Shortcut or quick mobile workflow.

```text
/shortcuts/add?text=Soccer%20practice%20Tuesday%20at%206pm
```

## Deployment Notes

A common public deployment pattern is:

1. Run the app locally on a server with Uvicorn, Docker Compose, or a `systemd` service.
2. Put it behind HTTPS using a reverse proxy or Cloudflare Tunnel.
3. Set `CALENDAR_FEED_TOKEN` to a long random value.
4. Keep the tokenized `.ics` feed reachable without an interactive login page so Apple Calendar can refresh it.
5. Optionally protect the web UI with Cloudflare Access or another authentication layer while bypassing the tokenized feed path.

Apple Calendar cannot complete browser-based login flows while refreshing subscriptions, so the feed URL itself should be treated like a secret. Rotate `CALENDAR_FEED_TOKEN` if it is shared accidentally.

## Running Tests

```bash
python -m unittest discover -s tests
```

## Environment Variables

| Variable | Required | Description |
| --- | --- | --- |
| `LLM_PROVIDER` | No | LLM provider to use: `openai`, `anthropic`, or `ollama`. Defaults to `openai`. |
| `OPENAI_API_KEY` | For OpenAI | API key used when `LLM_PROVIDER=openai`. |
| `ANTHROPIC_API_KEY` | For Anthropic | API key used when `LLM_PROVIDER=anthropic`. |
| `OLLAMA_BASE_URL` | For Ollama | Ollama server URL. Defaults to `http://localhost:11434`. |
| `CALENDAR_FEED_TOKEN` | Recommended | Enables the tokenized feed path `/calendar/<token>.ics` and disables `/calendar.ics`. |
| `ICS_GEN_DB_PATH` | No | Custom SQLite database path. Defaults to `events.db` in the project root. |

## Project Structure

```text
.
├── app.py                  # FastAPI app, LLM parsing, normalization, SQLite, and ICS generation
├── templates/              # Jinja2 templates for the UI and live preview
├── static/                 # Favicons and Apple touch icons
├── tests/                  # Unit tests
├── Dockerfile              # Container image definition
├── docker-compose.yaml     # Local/containerized deployment
└── requirements.txt        # Python dependencies
```

## Why This Project Exists

This was built as a practical resume project: a focused product idea with real-world integration concerns rather than a toy CRUD app. The codebase shows how to combine LLM extraction with deterministic business rules, user review, persistent storage, and a standards-based output format that works with existing calendar clients.
