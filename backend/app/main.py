"""University Room Scheduler — backend API.

The browser never generates schedules. It logs in via Supabase Auth, calls
these endpoints, and displays what comes back. All scheduling runs here
through the CP-SAT solver; all data lives in Supabase Postgres.
"""

from __future__ import annotations

import json
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from . import db, importer
from .auth import current_user, require_coordinator, require_user
from .config import ALLOWED_ORIGINS
from .config import LETTERS_BUCKET
from .solver import solve_timetable

app = FastAPI(title="University Room Scheduler API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/me")
def me(user: dict = Depends(require_user)):
    return user


@app.post("/api/claim-coordinator")
def claim_coordinator(user: dict = Depends(current_user)):
    """First person in becomes the Timetable Coordinator. After that, only a
    coordinator can promote others."""
    if db.coordinator_count() > 0:
        raise HTTPException(403, "A coordinator already exists; ask them to promote you")
    db.set_role(user["id"], "coordinator")
    return {"role": "coordinator"}


@app.post("/api/promote/{user_id}")
def promote_by_id(user_id: str, role: str = Form("viewer"),
                  _: dict = Depends(require_coordinator)):
    if role not in ("coordinator", "viewer"):
        raise HTTPException(400, "Role must be coordinator or viewer")
    db.set_role(user_id, role)
    return {"id": user_id, "role": role}


@app.get("/api/university")
def university(user: dict = Depends(require_user)):
    return db.load_university()


@app.post("/api/import/{kind}")
async def import_data(kind: str, file: UploadFile = File(...),
                      _: dict = Depends(require_coordinator)):
    content = await file.read()
    result = importer.import_kind(kind, file.filename or "upload.csv", content)
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


@app.post("/api/seed")
def seed(_: dict = Depends(require_coordinator)):
    """Load the bundled department data (34 sections, 34 rooms, 102 faculty)."""
    seed_data = json.load(open("seed/university_data.json"))
    sb = db.client()
    sb.table("faculty").upsert(
        [{"id": f["id"], "name": f["name"], "max_per_day": f.get("maxPerDay", 4)}
         for f in seed_data["faculty"]]
    ).execute()
    sb.table("rooms").upsert(
        [{"id": r["id"], "name": r["name"], "capacity": r["capacity"], "type": r["type"]}
         for r in seed_data["rooms"]]
    ).execute()
    sb.table("sections").upsert(
        [{"id": s["id"], "name": s["name"], "students": s["students"]}
         for s in seed_data["sections"]]
    ).execute()
    subjects, links = {}, []
    for g in seed_data["courseSections"]:
        subjects[g["id"]] = {"id": g["id"], "code": g["code"], "name": g["name"]}
        links.append({
            "section_id": g["sectionId"], "subject_id": g["id"],
            "faculty_id": g["facultyId"],
            "lecture_hours": g.get("lectureHours", 3),
            "practical_hours": g.get("practicalHours", 2),
        })
    sb.table("subjects").upsert(list(subjects.values())).execute()
    sb.table("section_subjects").upsert(links).execute()
    counts = {k: len(v) for k, v in {
        "faculty": seed_data["faculty"], "rooms": seed_data["rooms"],
        "sections": seed_data["sections"], "subjects": list(subjects.values()),
        "assignments": links}.items()}
    return {"seeded": counts}


@app.post("/api/solve")
def solve(_: dict = Depends(require_coordinator)):
    uni = db.load_university()
    if not uni["sections"] or not uni["section_subjects"]:
        raise HTTPException(400, "No teaching data yet — import or seed the university data first")
    days, num_slots, sections, rooms, faculty_max, assignments = db.solver_inputs(uni)
    result = solve_timetable(days, num_slots, sections, rooms, faculty_max, assignments)
    if result["status"] != "ok":
        return result  # infeasible / unknown, with a human-readable detail

    sb = db.client()
    sb.table("schedules").update({"status": "archived"}).eq("status", "active").execute()
    row = sb.table("schedules").insert({
        "status": "active",
        "sessions": result["sessions"],
        "stats": result["stats"],
    }).execute()
    return {"status": "ok", "scheduleId": row.data[0]["id"], "stats": result["stats"]}


@app.get("/api/schedule")
def schedule(user: dict = Depends(require_user)):
    row = (db.client().table("schedules").select("*").eq("status", "active")
           .order("created_at", desc=True).limit(1).execute())
    if not row.data:
        return {"status": "empty"}
    return {"status": "ok", "scheduleId": row.data[0]["id"],
            "createdAt": row.data[0]["created_at"],
            "stats": row.data[0].get("stats"), "sessions": row.data[0]["sessions"]}


# ---- room requests ---------------------------------------------------------

@app.post("/api/requests")
async def create_request(section_id: str = Form(...),
                         preferred_room: str = Form(""),
                         reason: str = Form(...),
                         letter: Optional[UploadFile] = File(None),
                         user: dict = Depends(require_user)):
    letter_path = None
    if letter and letter.filename:
        letter_path = f"{user['id']}/{letter.filename}"
        db.client().storage.from_(LETTERS_BUCKET).upload(
            letter_path, await letter.read(),
            {"content-type": letter.content_type or "application/octet-stream",
             "upsert": "true"})
    row = db.client().table("room_requests").insert({
        "user_id": user["id"], "section_id": section_id,
        "preferred_room": preferred_room or None, "reason": reason,
        "letter_path": letter_path, "status": "pending",
    }).execute()
    return {"id": row.data[0]["id"]}


@app.get("/api/requests")
def list_requests(user: dict = Depends(require_user)):
    q = db.client().table("room_requests").select(
        "id, user_id, section_id, preferred_room, reason, letter_path, status, "
        "created_at, profiles(full_name)"
    )
    rows = (q.execute().data if user["role"] == "coordinator"
            else q.eq("user_id", user["id"]).execute().data)
    return {"requests": rows or []}


@app.get("/api/requests/{request_id}/letter")
def request_letter(request_id: str, user: dict = Depends(require_user)):
    row = (db.client().table("room_requests").select("letter_path, user_id")
           .eq("id", request_id).single().execute())
    if not row.data or not row.data["letter_path"]:
        raise HTTPException(404, "No letter attached")
    if user["role"] != "coordinator" and row.data["user_id"] != user["id"]:
        raise HTTPException(403, "Not your request")
    url = db.client().storage.from_(LETTERS_BUCKET).create_signed_url(
        row.data["letter_path"], 600)
    return {"url": url.get("signedURL") or url.get("signedUrl")}


@app.post("/api/requests/{request_id}/decision")
def decide(request_id: str, status: str = Form(...),
           _: dict = Depends(require_coordinator)):
    if status not in ("approved", "rejected"):
        raise HTTPException(400, "Decision must be approved or rejected")
    db.client().table("room_requests").update({
        "status": status, "decided_by": _["id"], "decided_at": "now()",
    }).eq("id", request_id).execute()
    return {"id": request_id, "status": status}
