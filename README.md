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
