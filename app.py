from __future__ import annotations

import datetime as dt
import json
import os
import re
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import requests
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "events.db"

app = FastAPI(title="Family Calendar Capture")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@dataclass
class Event:
    name: str
    date: str
    start_time: str | None = None
    end_time: str | None = None
    location: str | None = None
    description: str | None = None
    alerts: list[int] | None = None


def init_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                date TEXT NOT NULL,
                start_time TEXT,
                end_time TEXT,
                location TEXT,
                description TEXT,
                alerts TEXT
            )
            """
        )
        conn.commit()


def normalize_event(raw: dict[str, Any]) -> Event:
    alerts_raw = raw.get("alerts")
    alerts: list[int] = []
    if isinstance(alerts_raw, list):
        alerts = [int(v) for v in alerts_raw if str(v).strip()]
    elif isinstance(alerts_raw, str):
        alerts = [int(v.strip()) for v in alerts_raw.split(",") if v.strip()]

    return Event(
        name=str(raw.get("name", "")).strip(),
        date=str(raw.get("date", "")).strip(),
        start_time=str(raw.get("start_time", "")).strip() or None,
        end_time=str(raw.get("end_time", "")).strip() or None,
        location=str(raw.get("location", "")).strip() or None,
        description=str(raw.get("description", "")).strip() or None,
        alerts=alerts or None,
    )


def extract_json(text: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        raise ValueError("No JSON object found in model response")
    return json.loads(match.group(0))


def build_prompt(user_text: str) -> str:
    today = dt.date.today().isoformat()
    return (
        "You are an event extraction engine.\n"
        f"Today's date is {today}.\n"
        "Convert the user text into JSON with this exact schema:\n"
        '{"events":[{"name":"...","date":"YYYY-MM-DD","start_time":"HH:MM or null","end_time":"HH:MM or null","location":"... or null","description":"... or null","alerts":[minutes_before,...]}]}\n'
        "Rules: Return ONLY JSON. Use 24-hour time. If no time, set start_time/end_time to null.\n"
        "If multiple events are present, return multiple items in events.\n"
        f"User text:\n{user_text}"
    )


def call_openai(prompt: str) -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required for OpenAI provider")
    resp = requests.post(
        "https://api.openai.com/v1/responses",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"model": os.getenv("OPENAI_MODEL", "gpt-4.1-mini"), "input": prompt},
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("output", [{}])[0].get("content", [{}])[0].get("text", "")


def call_anthropic(prompt: str) -> str:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is required for Anthropic provider")
    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": os.getenv("ANTHROPIC_MODEL", "claude-3-5-haiku-latest"), "max_tokens": 1200, "messages": [{"role": "user", "content": prompt}]},
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["content"][0]["text"]


def call_ollama(prompt: str) -> str:
    base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    resp = requests.post(
        f"{base}/api/generate",
        json={"model": os.getenv("OLLAMA_MODEL", "llama3.1"), "prompt": prompt, "stream": False},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json().get("response", "")


def parse_with_provider(provider: str, text: str) -> list[Event]:
    prompt = build_prompt(text)
    if provider == "openai":
        raw = call_openai(prompt)
    elif provider == "anthropic":
        raw = call_anthropic(prompt)
    elif provider == "ollama":
        raw = call_ollama(prompt)
    else:
        raise ValueError("Provider must be openai, anthropic, or ollama")
    payload = extract_json(raw)
    events = [normalize_event(ev) for ev in payload.get("events", [])]
    return [e for e in events if e.name and e.date]


def write_events(events: Iterable[Event]) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        for event in events:
            conn.execute(
                "INSERT INTO events (id, name, date, start_time, end_time, location, description, alerts) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), event.name, event.date, event.start_time, event.end_time, event.location, event.description, ",".join(str(a) for a in event.alerts) if event.alerts else None),
            )
        conn.commit()


def to_ics_datetime(date_str: str, time_str: str | None) -> tuple[str, bool]:
    if not time_str:
        return date_str.replace("-", ""), True
    return dt.datetime.fromisoformat(f"{date_str}T{time_str}").strftime("%Y%m%dT%H%M%S"), False


def event_to_ics(event_row: sqlite3.Row) -> str:
    dtstart, all_day = to_ics_datetime(event_row["date"], event_row["start_time"])
    if event_row["end_time"]:
        dtend, _ = to_ics_datetime(event_row["date"], event_row["end_time"])
    elif all_day:
        dtend = (dt.date.fromisoformat(event_row["date"]) + dt.timedelta(days=1)).strftime("%Y%m%d")
    else:
        start_dt = dt.datetime.fromisoformat(f"{event_row['date']}T{event_row['start_time']}")
        dtend = (start_dt + dt.timedelta(hours=1)).strftime("%Y%m%dT%H%M%S")

    lines = ["BEGIN:VEVENT", f"UID:{event_row['id']}@ics-gen", f"DTSTAMP:{dt.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}", f"SUMMARY:{event_row['name']}"]
    lines.extend([f"DTSTART;VALUE=DATE:{dtstart}", f"DTEND;VALUE=DATE:{dtend}"] if all_day else [f"DTSTART:{dtstart}", f"DTEND:{dtend}"])
    if event_row["location"]:
        lines.append(f"LOCATION:{event_row['location']}")
    if event_row["description"]:
        lines.append(f"DESCRIPTION:{event_row['description']}")
    if event_row["alerts"]:
        for minutes in event_row["alerts"].split(","):
            lines.extend(["BEGIN:VALARM", f"TRIGGER:-PT{minutes}M", "ACTION:DISPLAY", f"DESCRIPTION:Reminder: {event_row['name']}", "END:VALARM"])
    lines.append("END:VEVENT")
    return "\r\n".join(lines)


def build_calendar(events: list[sqlite3.Row]) -> str:
    out = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//ics-gen//Event Scheduler//EN", "CALSCALE:GREGORIAN", "METHOD:PUBLISH"]
    out.extend(event_to_ics(e) for e in events)
    out.append("END:VCALENDAR")
    return "\r\n".join(out) + "\r\n"


def fetch_rows() -> list[sqlite3.Row]:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute("SELECT * FROM events ORDER BY date, start_time").fetchall()


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("index.html", {"request": request, "events": fetch_rows(), "error": None, "provider": os.getenv("LLM_PROVIDER", "openai").strip().lower()})


@app.post("/preview", response_class=HTMLResponse)
def preview(request: Request, event_text: str = Form(...)) -> HTMLResponse:
    provider = os.getenv("LLM_PROVIDER", "openai").strip().lower()
    try:
        events = parse_with_provider(provider, event_text.strip()) if event_text.strip() else []
        return templates.TemplateResponse("_preview.html", {"request": request, "events": events, "error": None})
    except Exception as exc:
        return templates.TemplateResponse("_preview.html", {"request": request, "events": [], "error": str(exc)})


@app.post("/save")
def save(event_text: str = Form(...)) -> RedirectResponse:
    provider = os.getenv("LLM_PROVIDER", "openai").strip().lower()
    events = parse_with_provider(provider, event_text.strip()) if event_text.strip() else []
    if events:
        write_events(events)
    return RedirectResponse(url="/", status_code=303)


@app.get("/calendar.ics")
def calendar_feed() -> Response:
    return Response(content=build_calendar(fetch_rows()), media_type="text/calendar")
