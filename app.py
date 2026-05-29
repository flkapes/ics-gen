from __future__ import annotations

import datetime as dt
import json
import os
import re
import secrets
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import requests
from fastapi import FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "events.db"
STATIC_DIR = BASE_DIR / "static"
ICON_FILE_NAMES = {
    "favicon.ico",
    "favicon.png",
    "apple-touch-icon.png",
    "apple-touch-icon-precomposed.png",
}

app = FastAPI(title="Family Calendar Capture")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR), check_dir=False), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

DEFAULT_TIMED_EVENT_MINUTES = 60
DEFAULT_ALL_DAY_ALERTS = [1440, 540]
DEFAULT_TIMED_EVENT_ALERTS = [10080, 1440, 60]
NEAR_TERM_ALERT_CANDIDATES = [720, 360, 180, 60, 30, 10]
REMOTE_LOCATION_VALUES = {"remote", "online", "virtual", "video call", "video conference", "web conference"}
VIRTUAL_LOCATION_PATTERNS = [
    ("Zoom", re.compile(r"\bzoom(?:\.us)?\b", re.IGNORECASE)),
    ("Google Meet", re.compile(r"\b(?:google\s+meet|meet\.google\.com|gmeet)\b", re.IGNORECASE)),
    ("Microsoft Teams", re.compile(r"\b(?:microsoft\s+teams|ms\s+teams|teams\.microsoft\.com|teams\s+meeting)\b", re.IGNORECASE)),
    ("Cisco Webex", re.compile(r"\b(?:webex|cisco\s+webex)\b", re.IGNORECASE)),
    ("FaceTime", re.compile(r"\bfacetime\b", re.IGNORECASE)),
    ("Skype", re.compile(r"\bskype\b", re.IGNORECASE)),
    ("GoTo Meeting", re.compile(r"\b(?:gotomeeting|go to meeting)\b", re.IGNORECASE)),
    ("BlueJeans", re.compile(r"\bbluejeans\b", re.IGNORECASE)),
    ("Discord", re.compile(r"\bdiscord(?:\.gg|\.com)?\b", re.IGNORECASE)),
    ("Slack Huddle", re.compile(r"\b(?:slack\s+huddle|huddle)\b", re.IGNORECASE)),
]
URL_PATTERN = re.compile(r"https?://[^\s<>()\"']+", re.IGNORECASE)
NO_REMINDER_PATTERN = re.compile(r"\b(?:no|without|skip|omit|disable)\s+(?:alerts?|reminders?|notifications?)\b|\b(?:do not|don't)\s+(?:alert|remind|notify)\b", re.IGNORECASE)


def humanize_alerts(alerts: list[int] | None) -> str | None:
    if not alerts:
        return None
    units: list[str] = []
    for minutes in sorted({int(v) for v in alerts}, reverse=True):
        if minutes % 10080 == 0:
            value = minutes // 10080
            units.append(f"{value} week{'s' if value != 1 else ''}")
        elif minutes % 1440 == 0:
            value = minutes // 1440
            units.append(f"{value} day{'s' if value != 1 else ''}")
        elif minutes % 60 == 0:
            value = minutes // 60
            units.append(f"{value} hour{'s' if value != 1 else ''}")
        else:
            units.append(f"{minutes} minutes")
    return ", ".join(units) + " before"


templates.env.globals["humanize_alerts"] = humanize_alerts


def humanize_rrule(rule: str | None) -> str | None:
    if not rule:
        return None
    raw = rule.strip()
    if raw.startswith("RRULE:"):
        raw = raw.split(":", 1)[1]

    parts: dict[str, str] = {}
    for item in raw.split(";"):
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        parts[key.strip().upper()] = value.strip()

    freq_map = {
        "DAILY": "Daily",
        "WEEKLY": "Weekly",
        "MONTHLY": "Monthly",
        "YEARLY": "Yearly",
    }
    day_map = {"MO": "Mon", "TU": "Tue", "WE": "Wed", "TH": "Thu", "FR": "Fri", "SA": "Sat", "SU": "Sun"}

    freq = freq_map.get(parts.get("FREQ", "").upper())
    if not freq:
        return rule

    chunks: list[str] = [freq]
    interval = parts.get("INTERVAL")
    if interval and interval.isdigit() and int(interval) > 1:
        unit_map = {'DAILY': 'day', 'WEEKLY': 'week', 'MONTHLY': 'month', 'YEARLY': 'year'}
        unit = unit_map.get(parts.get('FREQ', '').upper(), 'interval')
        chunks.append(f"every {interval} {unit}s")

    byday = parts.get("BYDAY")
    if byday:
        days = [day_map.get(d.strip().upper(), d.strip().upper()) for d in byday.split(",") if d.strip()]
        if days:
            chunks.append("on " + ", ".join(days))

    bymonthday = parts.get("BYMONTHDAY")
    if bymonthday:
        chunks.append(f"on day {bymonthday} of the month")

    until = parts.get("UNTIL")
    if until:
        until_text = until
        try:
            cleaned = until.rstrip("Z")
            if "T" in cleaned:
                until_dt = dt.datetime.strptime(cleaned, "%Y%m%dT%H%M%S")
                until_text = until_dt.strftime("%b %-d, %Y %-I:%M %p")
            else:
                until_date = dt.datetime.strptime(cleaned, "%Y%m%d").date()
                until_text = until_date.strftime("%b %-d, %Y")
        except ValueError:
            until_text = until
        chunks.append(f"until {until_text}")

    count = parts.get("COUNT")
    if count:
        chunks.append(f"for {count} occurrence{'s' if count != '1' else ''}")

    return " · ".join(chunks)


templates.env.globals["humanize_rrule"] = humanize_rrule


def load_dotenv_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


load_dotenv_file(BASE_DIR / ".env")


def database_path() -> Path:
    return Path(os.getenv("ICS_GEN_DB_PATH", str(DB_PATH)))


def connect_db() -> sqlite3.Connection:
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(path)


@dataclass
class Event:
    name: str
    date: str
    operation: str = "create"
    uid: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    location: str | None = None
    description: str | None = None
    url: str | None = None
    attachments: list[str] | None = None
    recurrence_rule: str | None = None
    recurrence_id: str | None = None
    exdates: list[str] | None = None
    timezone: str | None = None
    geo: str | None = None
    alerts: list[int] | None = None


def init_db() -> None:
    with connect_db() as conn:
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
                url TEXT,
                attachments TEXT,
                recurrence_rule TEXT,
                recurrence_id TEXT,
                exdates TEXT,
                timezone TEXT,
                geo TEXT,
                alerts TEXT
            )
            """
        )
        existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(events)").fetchall()}
        for column, column_type in [
            ("url", "TEXT"),
            ("attachments", "TEXT"),
            ("recurrence_rule", "TEXT"),
            ("recurrence_id", "TEXT"),
            ("exdates", "TEXT"),
            ("timezone", "TEXT"),
            ("geo", "TEXT"),
        ]:
            if column not in existing_columns:
                conn.execute(f"ALTER TABLE events ADD COLUMN {column} {column_type}")
        conn.commit()


def normalize_event(raw: dict[str, Any]) -> Event:
    def clean_optional(value: Any) -> str | None:
        text = str(value or "").strip()
        if not text or text.lower() in {"none", "null", "n/a", "na"}:
            return None
        return text

    alerts_raw = raw.get("alerts")
    alerts: list[int] = []
    if isinstance(alerts_raw, list):
        alerts = [int(v) for v in alerts_raw if str(v).strip()]
    elif isinstance(alerts_raw, str):
        alerts = [int(v.strip()) for v in alerts_raw.split(",") if v.strip()]

    attachments_raw = raw.get("attachments")
    exdates_raw = raw.get("exdates")
    return Event(
        operation=str(raw.get("operation", "create")).strip().lower() or "create",
        uid=clean_optional(raw.get("uid")),
        name=normalize_title(str(raw.get("name", "")).strip()),
        date=str(raw.get("date", "")).strip(),
        start_time=clean_optional(raw.get("start_time")),
        end_time=clean_optional(raw.get("end_time")),
        location=clean_optional(raw.get("location")),
        description=normalize_description(clean_optional(raw.get("description")), normalize_title(str(raw.get("name", "")).strip())),
        timezone=clean_optional(raw.get("timezone")),
        url=clean_optional(raw.get("url")),
        geo=clean_optional(raw.get("geo")),
        attachments=[str(v).strip() for v in attachments_raw if str(v).strip()] if isinstance(attachments_raw, list) else None,
        recurrence_rule=str(raw.get("recurrence_rule", "")).strip() or None,
        recurrence_id=str(raw.get("recurrence_id", "")).strip() or None,
        exdates=[str(v).strip() for v in exdates_raw if str(v).strip()] if isinstance(exdates_raw, list) else None,
        alerts=alerts or None,
    )


def normalize_title(name: str) -> str:
    cleaned = " ".join(name.split()).strip()
    if not cleaned:
        return "Scheduled Event"
    titled = cleaned if cleaned.isupper() else cleaned.title()
    if len(titled.split()) == 1:
        return f"{titled} Appointment"
    return titled


def normalize_description(description: str | None, title: str) -> str | None:
    if description and description.strip():
        desc = " ".join(description.split()).strip()
        return desc[0].upper() + desc[1:] if len(desc) > 1 else desc.upper()
    return f"Auto-created calendar entry for {title}."


def parse_time(value: str | None) -> dt.time | None:
    if not value:
        return None
    try:
        return dt.time.fromisoformat(value)
    except ValueError:
        return None


def add_minutes_to_time(time_value: str, minutes: int) -> str | None:
    parsed = parse_time(time_value)
    if not parsed:
        return None
    combined = dt.datetime.combine(dt.date.today(), parsed) + dt.timedelta(minutes=minutes)
    return combined.time().strftime("%H:%M")


def event_start_datetime(event: Event) -> dt.datetime | None:
    try:
        event_date = dt.date.fromisoformat(event.date)
    except ValueError:
        return None
    start = parse_time(event.start_time) or dt.time.min
    return dt.datetime.combine(event_date, start)


def deterministic_alerts(event: Event, now: dt.datetime | None = None) -> list[int] | None:
    if not event.date:
        return None
    now = now or dt.datetime.now()
    start = event_start_datetime(event)
    if not start:
        return None

    candidates = DEFAULT_TIMED_EVENT_ALERTS if event.start_time else DEFAULT_ALL_DAY_ALERTS
    minutes_until = int((start - now).total_seconds() // 60)
    chosen = [minutes for minutes in candidates if minutes < minutes_until]

    if len(chosen) < 2 and minutes_until > 15:
        for minutes in NEAR_TERM_ALERT_CANDIDATES:
            if minutes < minutes_until and minutes not in chosen:
                chosen.append(minutes)
            if len(chosen) >= 2:
                break

    return sorted(set(chosen[:3]), reverse=True) or None


def clean_alerts(alerts: list[int] | None) -> list[int] | None:
    if not alerts:
        return None
    values = sorted({int(v) for v in alerts if int(v) > 0}, reverse=True)
    return values or None


def first_url_for_platform(text: str, label: str) -> str | None:
    for url in URL_PATTERN.findall(text):
        lowered = url.lower().rstrip(".,;)")
        if label == "Zoom" and "zoom" in lowered:
            return lowered
        if label == "Google Meet" and "meet.google" in lowered:
            return lowered
        if label == "Microsoft Teams" and "teams.microsoft" in lowered:
            return lowered
        if label == "Cisco Webex" and "webex" in lowered:
            return lowered
        if label == "Discord" and ("discord.gg" in lowered or "discord.com" in lowered):
            return lowered
        if label == "GoTo Meeting" and "gotomeeting" in lowered:
            return lowered
        if label == "BlueJeans" and "bluejeans" in lowered:
            return lowered
    return None


def extract_virtual_location(text: str) -> tuple[str | None, str | None]:
    for label, pattern in VIRTUAL_LOCATION_PATTERNS:
        if pattern.search(text):
            url = first_url_for_platform(text, label)
            return (f"{label}: {url}" if url else label), url
    return None, None


def apply_event_policies(events: list[Event], source_text: str) -> list[Event]:
    no_reminders = bool(NO_REMINDER_PATTERN.search(source_text))
    virtual_location, virtual_url = extract_virtual_location(source_text)

    for event in events:
        if event.start_time and not event.end_time:
            event.end_time = add_minutes_to_time(event.start_time, DEFAULT_TIMED_EVENT_MINUTES)

        if virtual_location:
            if not event.location or event.location.strip().lower() in REMOTE_LOCATION_VALUES:
                event.location = virtual_location
            if virtual_url and not event.url:
                event.url = virtual_url
        elif event.location and event.location.strip().lower() in REMOTE_LOCATION_VALUES:
            event.location = None

        event.alerts = None if no_reminders else clean_alerts(event.alerts) or deterministic_alerts(event)

    return events


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
        '{"events":[{"operation":"create|override|cancel_instance","uid":"existing-id-or-null","name":"...","date":"YYYY-MM-DD","start_time":"HH:MM or null","end_time":"HH:MM or null","timezone":"IANA timezone or null","location":"... or null","description":"... or null","url":"https://... or null","geo":"lat;lon or null","attachments":["https://..."],"recurrence_rule":"RRULE:FREQ=WEEKLY;... or null","recurrence_id":"YYYY-MM-DDTHH:MM:SS or null","exdates":["YYYY-MM-DD",...],"alerts":[minutes_before,...]}]}\n'
        "Rules: Return ONLY JSON. Use 24-hour time. If no time, set start_time/end_time to null.\n"
        "If multiple events are present, return multiple items in events.\n"
        "Capture all user-provided details and place them in the most logical fields; do not omit useful details.\n"
        "Do not invent URLs or links. Set url to null unless a real link appears in user text.\n"
        "If a remote meeting service is named or linked (Zoom, Google Meet, Microsoft Teams, Webex, FaceTime, Skype, GoTo Meeting, BlueJeans, Discord, Slack huddle, or similar), put the exact service/link in location; never replace it with Remote/Online/Virtual.\n"
        "If no time is specified, set start_time/end_time to null because the app will treat it as all-day.\n"
        "If a start time is specified but no end time or duration is specified, leave end_time null because the app will apply its default duration.\n"
        "Write professional, properly-cased titles and concise useful descriptions.\n"
        "Avoid titles that are too generic or too specific.\n"
        "Alerts policy: only return alert offsets that the user explicitly requested. If no reminders are mentioned, use an empty alerts array so the app can apply deterministic defaults.\n"
        "Always return explicit alert offsets in minutes in the alerts array.\n"
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
    events = [e for e in events if e.name and e.date]
    return apply_event_policies(events, text)


def write_events(events: Iterable[Event]) -> None:
    with connect_db() as conn:
        for event in events:
            event_id = event.uid or str(uuid.uuid4())
            if event.operation == "cancel_instance" and event.uid:
                existing = conn.execute("SELECT exdates FROM events WHERE id = ?", (event.uid,)).fetchone()
                if existing:
                    existing_exdates = {v.strip() for v in (existing[0] or "").split(",") if v.strip()}
                    existing_exdates.add(event.date)
                    conn.execute("UPDATE events SET exdates = ? WHERE id = ?", (",".join(sorted(existing_exdates)), event.uid))
                continue
            conn.execute(
                "INSERT OR REPLACE INTO events (id, name, date, start_time, end_time, location, description, url, attachments, recurrence_rule, recurrence_id, exdates, timezone, geo, alerts) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event_id,
                    event.name,
                    event.date,
                    event.start_time,
                    event.end_time,
                    event.location,
                    event.description,
                    event.url,
                    ",".join(event.attachments) if event.attachments else None,
                    event.recurrence_rule,
                    event.recurrence_id,
                    ",".join(event.exdates) if event.exdates else None,
                    event.timezone,
                    event.geo,
                    ",".join(str(a) for a in event.alerts) if event.alerts else None,
                ),
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

    lines = ["BEGIN:VEVENT", f"UID:{event_row['id']}", f"DTSTAMP:{dt.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}", f"SUMMARY:{event_row['name']}"]
    if all_day:
        lines.extend([f"DTSTART;VALUE=DATE:{dtstart}", f"DTEND;VALUE=DATE:{dtend}"])
    elif event_row["timezone"]:
        lines.extend([f"DTSTART;TZID={event_row['timezone']}:{dtstart}", f"DTEND;TZID={event_row['timezone']}:{dtend}"])
    else:
        lines.extend([f"DTSTART:{dtstart}", f"DTEND:{dtend}"])
    if event_row["location"]:
        lines.append(f"LOCATION:{event_row['location']}")
    if event_row["description"]:
        description = event_row["description"].replace(chr(10), "\\n")
        lines.append(f"DESCRIPTION:{description}")
    if event_row["url"]:
        lines.append(f"URL:{event_row['url']}")
    if event_row["geo"]:
        lines.append(f"GEO:{event_row['geo']}")
    if event_row["attachments"]:
        for attachment in event_row["attachments"].split(","):
            if attachment.strip():
                lines.append(f"ATTACH:{attachment.strip()}")
    if event_row["recurrence_rule"]:
        rule = event_row["recurrence_rule"].strip()
        lines.append(rule if rule.startswith("RRULE:") else f"RRULE:{rule}")
    if event_row["recurrence_id"]:
        lines.append(f"RECURRENCE-ID:{event_row['recurrence_id'].replace('-', '').replace(':', '')}")
    if event_row["exdates"]:
        exdate_values = [v.strip().replace("-", "") for v in event_row["exdates"].split(",") if v.strip()]
        if exdate_values:
            lines.append(f"EXDATE;VALUE=DATE:{','.join(exdate_values)}")
    if event_row["alerts"]:
        for minutes in event_row["alerts"].split(","):
            lines.extend(["BEGIN:VALARM", f"TRIGGER:-PT{minutes}M", "ACTION:DISPLAY", f"DESCRIPTION:Reminder: {event_row['name']}", "END:VALARM"])
    lines.append("END:VEVENT")
    return "\r\n".join(lines)


def build_calendar(events: list[sqlite3.Row]) -> str:
    out = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//ics-gen//Event Scheduler//EN", "CALSCALE:GREGORIAN", "METHOD:PUBLISH"]
    for tzid in sorted({e["timezone"] for e in events if e["timezone"]}):
        out.extend(["BEGIN:VTIMEZONE", f"TZID:{tzid}", "END:VTIMEZONE"])
    out.extend(event_to_ics(e) for e in events)
    out.append("END:VCALENDAR")
    return "\r\n".join(out) + "\r\n"


def fetch_rows() -> list[sqlite3.Row]:
    with connect_db() as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute("SELECT * FROM events ORDER BY date, start_time").fetchall()


@app.on_event("startup")
def startup() -> None:
    init_db()


def static_icon_response(file_name: str) -> Response:
    if file_name not in ICON_FILE_NAMES:
        return Response(status_code=404)
    path = STATIC_DIR / file_name
    if not path.is_file():
        return Response(status_code=404)
    return FileResponse(path)


def calendar_feed_token() -> str:
    return os.getenv("CALENDAR_FEED_TOKEN", "").strip()


def calendar_feed_path() -> str:
    token = calendar_feed_token()
    if token:
        return f"/calendar/{token}.ics"
    return "/calendar.ics"


def calendar_feed_response() -> Response:
    return Response(content=build_calendar(fetch_rows()), media_type="text/calendar")


@app.get("/favicon.ico", include_in_schema=False)
def favicon_ico() -> Response:
    return static_icon_response("favicon.ico")


@app.get("/favicon.png", include_in_schema=False)
def favicon_png() -> Response:
    return static_icon_response("favicon.png")


@app.get("/apple-touch-icon.png", include_in_schema=False)
def apple_touch_icon() -> Response:
    return static_icon_response("apple-touch-icon.png")


@app.get("/apple-touch-icon-precomposed.png", include_in_schema=False)
def apple_touch_icon_precomposed() -> Response:
    return static_icon_response("apple-touch-icon-precomposed.png")

@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "events": fetch_rows(),
            "error": None,
            "provider": os.getenv("LLM_PROVIDER", "openai").strip().lower(),
            "calendar_feed_path": calendar_feed_path(),
        },
    )


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
    if calendar_feed_token():
        return Response(status_code=404)
    return calendar_feed_response()


@app.get("/calendar/{token}.ics")
def token_calendar_feed(token: str) -> Response:
    expected_token = calendar_feed_token()
    if not expected_token or not secrets.compare_digest(token, expected_token):
        return Response(status_code=404)
    return calendar_feed_response()


@app.get("/shortcuts/add")
def shortcuts_add(text: str) -> RedirectResponse:
    provider = os.getenv("LLM_PROVIDER", "openai").strip().lower()
    events = parse_with_provider(provider, text.strip()) if text.strip() else []
    if events:
        write_events(events)
    return RedirectResponse(url="/", status_code=303)
