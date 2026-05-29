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
- Apple-compatible calendar feed with multiple `VALARM` reminders and optional tokenized feed URL protection.
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
Use the HTTP feed URL shown by the app. Without `CALENDAR_FEED_TOKEN`, the local feed is:

```text
http://127.0.0.1:8000/calendar.ics
```

With `CALENDAR_FEED_TOKEN` set, the plain `/calendar.ics` endpoint is disabled and the feed moves to an unguessable URL path:

```text
http://127.0.0.1:8000/calendar/<token>.ics
```

For Apple Calendar, either paste the HTTP feed URL using **File > New Calendar Subscription**, or use the app's **Open in Apple Calendar** button. If you build a `webcal://` launch link yourself, replace only the URL scheme:

```text
webcal://127.0.0.1:8000/calendar/<token>.ics
```

Do not prepend `webcal://` to the whole HTTP URL. For example, `webcal://http//127.0.0.1:8000/calendar.ics` opens Calendar with a malformed subscription address (`http//127...`, missing `:`). The actual subscription feed remains an HTTP or HTTPS URL.

## Deploy on Ubuntu behind Cloudflare Tunnel
This app can run as a local `systemd` service on your Ubuntu box while Cloudflare Tunnel publishes a HTTPS hostname without opening inbound firewall ports.

> **Privacy note:** Apple Calendar subscriptions need to fetch the `.ics` URL without an interactive login page, so Cloudflare Access can break subscriptions. Set `CALENDAR_FEED_TOKEN` to publish the feed at an unguessable path like `/calendar/<token>.ics`; anyone with that full URL can still read the feed, so treat it like a password and rotate the token if it leaks.

### 1. Put the app on the server
```bash
sudo adduser --system --group --home /opt/ics-gen icsgen
sudo apt update
sudo apt install -y git python3 python3-venv
sudo -u icsgen git clone <your-repo-url> /opt/ics-gen/app
cd /opt/ics-gen/app
sudo -u icsgen python3 -m venv .venv
sudo -u icsgen .venv/bin/pip install -r requirements.txt
```

Create `/opt/ics-gen/app/.env` with your provider settings:

```bash
sudo -u icsgen tee /opt/ics-gen/app/.env >/dev/null <<'ENV'
LLM_PROVIDER=openai
OPENAI_API_KEY=replace-me
# Protect the calendar feed with an unguessable URL path. Generate with: openssl rand -hex 32
CALENDAR_FEED_TOKEN=replace-with-a-long-random-token
# ANTHROPIC_API_KEY=replace-me
# OLLAMA_BASE_URL=http://localhost:11434
ENV
sudo chmod 600 /opt/ics-gen/app/.env
```

### 2. Run the FastAPI app with systemd
Create `/etc/systemd/system/ics-gen.service`:

```ini
[Unit]
Description=ics-gen family calendar app
After=network-online.target
Wants=network-online.target

[Service]
User=icsgen
Group=icsgen
WorkingDirectory=/opt/ics-gen/app
EnvironmentFile=/opt/ics-gen/app/.env
ExecStart=/opt/ics-gen/app/.venv/bin/uvicorn app:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Enable it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ics-gen
sudo systemctl status ics-gen
curl -I http://127.0.0.1:8000/calendar/<your-token>.ics
```

### 3. Create the Cloudflare Tunnel
The dashboard-managed flow is the simplest:

1. In Cloudflare Zero Trust, go to **Networks > Tunnels > Create a tunnel**.
2. Choose **Cloudflared** and name the tunnel, for example `ics-gen`.
3. Select the Debian/Ubuntu connector instructions for your CPU architecture and run the generated commands on the Ubuntu box. They install `cloudflared` as a service with a tunnel token.
4. Add a public hostname, for example `calendar.example.com`, with service type `HTTP` and URL `http://localhost:8000`.

After the connector is healthy, open:

```text
https://calendar.example.com/
https://calendar.example.com/calendar/<your-token>.ics
```

For Apple Calendar, subscribe to:

```text
https://calendar.example.com/calendar/<your-token>.ics
```

or use the app's **Open in Apple Calendar** button, which launches:

```text
webcal://calendar.example.com/calendar/<your-token>.ics
```

### Securing the public hostname
Use a split approach so Apple Calendar can still refresh the feed:

- Keep `CALENDAR_FEED_TOKEN` enabled; this makes the feed URL unguessable and disables the public `/calendar.ics` endpoint.
- Do not require an interactive Cloudflare Access login for the tokenized `.ics` URL, because Apple Calendar cannot complete a browser login flow while refreshing subscriptions.
- If you want to protect the web UI with Cloudflare Access, configure Access so the app pages require login but the tokenized feed path, such as `/calendar/<your-token>.ics`, is bypassed. A separate hostname just for the calendar feed also works.
- Rotate the token if it is shared accidentally: update `CALENDAR_FEED_TOKEN`, restart `ics-gen`, then resubscribe devices to the new URL.

### 4. Updating later
```bash
cd /opt/ics-gen/app
sudo -u icsgen git pull
sudo -u icsgen .venv/bin/pip install -r requirements.txt
sudo systemctl restart ics-gen
sudo journalctl -u ics-gen -n 100 --no-pager
```
