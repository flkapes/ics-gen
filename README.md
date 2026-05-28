# ics-gen

Single-input family calendar web app with live LLM preview.

## Stack choice: Flask vs FastAPI
For this version, I migrated to **FastAPI** to better support interactive UX patterns (preview endpoint + structured form handling) while keeping performance high.

Why FastAPI here:
- Clean request handling for separate preview/save endpoints.
- Great performance with `uvicorn`.
- Easy future expansion to JSON APIs and typed contracts.

## Features
- One textarea input + submit flow.
- Supports OpenAI, Anthropic, and Ollama (`LLM_PROVIDER`).
- **Live preview** before saving.
- Smooth, responsive UI with lightweight animation.
- Apple-compatible `/calendar.ics` feed with multiple `VALARM` reminders.
- Deterministic reminder defaults: timed events get up to 1 week, 1 day, and 1 hour reminders when those offsets fit; all-day events get 1 day and morning-of reminders.
- Calendar normalization rules for consistent output:
  - No time means an all-day event.
  - A start time without an end time or duration defaults to a 60-minute event.
  - Named virtual meeting services and links (Zoom, Google Meet, Microsoft Teams, Webex, FaceTime, Skype, GoTo Meeting, BlueJeans, Discord, Slack huddles, and similar) are preserved as the event location instead of generic values like “Remote”.

## Run
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export LLM_PROVIDER=openai
export OPENAI_API_KEY=...
# export ANTHROPIC_API_KEY=...
# export OLLAMA_BASE_URL=http://localhost:11434

uvicorn app:app --reload
```

Open `http://127.0.0.1:8000`.

## Apple Calendar subscription
Use the HTTP feed URL shown by the app, for example:

```text
http://127.0.0.1:8000/calendar.ics
```

Do not manually change the localhost URL to `webcal://`. On localhost, Apple Calendar can attempt an HTTPS/TLS request for `webcal://127.0.0.1:8000/calendar.ics`, which a plain `uvicorn` HTTP server cannot answer; `uvicorn` then logs `Invalid HTTP request received` and Calendar reports that subscription failed. If you need a `webcal://` URL, put the app behind a real HTTPS endpoint or tunnel and subscribe to that public secure URL.
