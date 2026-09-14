# University Room Scheduler

Full-stack timetable system for the department: Preact frontend, FastAPI backend,
Google OR-Tools CP-SAT solver, Supabase (Postgres + Auth + Storage).

Deployed: backend on Railway, frontend on Vercel, data in Supabase.

## Architecture

    frontend/   Preact UI (no build step; loads preact/supabase-js from esm.sh).
                Talks to the backend API only — never generates schedules.
    backend/    FastAPI service. Owns all scheduling logic.
      app/solver.py     CP-SAT model: no section/faculty/room double-booking,
                        one practical per section per day, room stability per
                        teaching block (P1-2, P3-5, P6-8), labs only for
                        practicals, room capacity, faculty daily caps (4),
                        one free day per section. Returns a valid timetable or
                        an explicit infeasibility report.
      app/importer.py   CSV/XLSX upload parsing via pandas with row-level
                        validation errors.
      app/main.py       API routes, Supabase Auth JWT verification, roles.
      seed/             Bundled department data (34 sections, 34 rooms,
                        102 faculty, 170 section-subject assignments).
    supabase/schema.sql   Tables, RLS policies, storage bucket, signup trigger.

## Roles

- **Timetable Coordinator** — imports/seeds data, generates schedules,
  approves/rejects room requests. The first account created claims the role.
- **Faculty / Viewer** — views timetables, downloads XLSX, submits room
  requests with an attached letter.

## Deploy / configure

1. **Supabase**: create a project, run `supabase/schema.sql` in the SQL editor.
   Copy the URL + anon key (Settings → API) into `frontend/config.js`, and set
   `SUPABASE_URL` + `SUPABASE_SERVICE_KEY` (service_role key) on the backend.
2. **Railway** (backend): new project → deploy from this repo, root directory
   `backend`. Railway auto-detects Python (NIXPACKS); start command is in
   `railway.json`. Set the two Supabase env vars.
3. **Vercel** (frontend): import the repo, root directory `frontend`, framework
   "Other" (static). Fill `frontend/config.js` with the Supabase URL/anon key
   and the Railway API URL. Deployment Protection: Project → Settings →
   Deployment Protection → disable, so the college can reach the link directly.

## Local development

    cd backend
    pip install -r requirements.txt
    uvicorn app.main:app --reload    # http://localhost:8000

Serve `frontend/` with any static server and set `apiUrl` to
`http://localhost:8000` in `config.js`.

## Verify the solver locally

    python3 tools/test_solver.py     # solves the 34-section department data
                                     # and independently validates every constraint
