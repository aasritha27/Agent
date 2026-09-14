# University Room Scheduler

Full-stack university timetable generator: a CP-SAT constraint solver for
34 sections / 34 rooms / 102 faculty with practicals, labs, faculty caps
and one free day per section.

Stack: FastAPI + Google OR-Tools CP-SAT solver, SQLite storage (single
file, no external database service), self-hosted token auth with two
roles (Timetable Coordinator / Faculty Viewer).

Deployed: backend on Railway (persistent volume for the database and
uploaded letters), frontend on Vercel.

## Layout

    backend/
      app/main.py       API routes, token auth, roles.
      app/auth.py       PBKDF2 password hashing + HMAC-signed session tokens.
      app/db.py         SQLite schema and queries; letters stored as files.
      app/solver.py     CP-SAT model (hard constraints only, infeasibility is explicit).
      app/importer.py   CSV/XLSX import with row-level error reporting.
      seed/             Bundled department data, auto-loaded on first boot.
      tools/            Solver and API verification scripts.
    frontend/           Preact UI (no build step; loads preact from esm.sh).

## Running locally

    cd backend
    pip install -r requirements.txt
    uvicorn app.main:app --reload
    # open frontend/index.html with apiUrl set to http://127.0.0.1:8000

The database (backend/data/app.db) and letters folder are created on
first boot and preloaded with the bundled department data.

## Deploying

1. **Backend (Railway)**: deploy `backend/` (Procfile starts uvicorn).
   Attach a volume mounted at `/data` and set `DATA_DIR=/data` so the
   database and letters survive redeploys. Set `SECRET_KEY` (any long
   random string) and `ALLOWED_ORIGINS` (the frontend URL or `*`).
2. **Frontend (Vercel)**: static deploy of `frontend/` (framework
   "Other"). Fill `frontend/config.js` with the backend URL.

## Auth model

The first account created becomes the Timetable Coordinator (generate
schedules, approve room requests, promote users). Everyone else signs up
as a viewer: view timetables, submit room requests with an attached
letter.
