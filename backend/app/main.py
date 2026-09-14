"""University Room Scheduler — backend API.

The browser never generates schedules. It signs in against these endpoints
(self-hosted auth, SQLite storage), calls the API with the returned token,
and displays what comes back. All scheduling runs here through the CP-SAT
solver; all data lives in one SQLite database file. No external services.
"""

from __future__ import annotations

import json
import os
import secrets
import uuid

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from . import db, importer
from .auth import create_token, current_user, hash_password, require_coordinator, require_user, verify_password
from .config import ALLOWED_ORIGINS, LETTERS_DIR
from .solver import solve_timetable

app = FastAPI(title="University Room Scheduler API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup():
    db.init()
    _autoseed_if_empty()


def _autoseed_if_empty():
    """A fresh database boots with the bundled department data preloaded."""
    uni = db.load_university()
    if uni["sections"] or uni["faculty"]:
        return
    seed_path = os.path.join(os.path.dirname(__file__), "..", "seed", "university_data.json")
    seed_data = json.load(open(seed_path))
    db.upsert("faculty", [
        {"id": f["id"], "name": f["name"], "max_per_day": f.get("maxPerDay", 4)}
        for f in seed_data["faculty"]], key="id")
    db.upsert("rooms", [
        {"id": r["id"], "name": r["name"], "capacity": r["capacity"], "type": r["type"]}
        for r in seed_data["rooms"]], key="id")
    db.upsert("sections", [
        {"id": s["id"], "name": s["name"], "students": s["students"]}
        for s in seed_data["sections"]], key="id")
    subjects, links = {}, []
    for g in seed_data["courseSections"]:
        subjects[g["id"]] = {"id": g["id"], "code": g["code"], "name": g["name"]}
        links.append({
            "section_id": g["sectionId"], "subject_id": g["id"],
            "faculty_id": g["facultyId"],
            "lecture_hours": g.get("lectureHours", 3),
            "practical_hours": g.get("practicalHours", 2),
        })
    db.upsert("subjects", list(subjects.values()), key="id")
    db.upsert("section_subjects", links, key="section_id,subject_id")


@app.get("/api/health")
def health():
    try:
        uni = db.load_university()
        counts = {"faculty": len(uni["faculty"]), "rooms": len(uni["rooms"]),
                  "sections": len(uni["sections"]), "subjects": len(uni["subjects"])}
    except Exception:
        counts = {}
    return {"ok": True, "data": counts}


# ---- auth ------------------------------------------------------------------

@app.post("/api/auth/signup")
def auth_signup(email: str = Form(...), password: str = Form(...),
                full_name: str = Form("")):
    if "@" not in email or len(password) < 6:
        raise HTTPException(400, "Valid email and a password of at least 6 characters required")
    if db.get_user_by_email(email):
        raise HTTPException(400, "An account with this email already exists")
    user_id = str(uuid.uuid4())
    db.create_user(user_id, email, hash_password(password), full_name)
    return {"token": create_token(user_id, email.strip().lower()), "user": {"id": user_id, "email": email.strip().lower(), "role": "viewer"}}


@app.post("/api/auth/login")
def auth_login(email: str = Form(...), password: str = Form(...)):
    user = db.get_user_by_email(email)
    if not user or not verify_password(password, user["password_hash"]):
        raise HTTPException(401, "Invalid email or password")
    return {"token": create_token(user["id"], user["email"]),
            "user": {"id": user["id"], "email": user["email"], "role": user["role"]}}


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


# ---- university data -------------------------------------------------------

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
    db.upsert("faculty", [
        {"id": f["id"], "name": f["name"], "max_per_day": f.get("maxPerDay", 4)}
        for f in seed_data["faculty"]], key="id")
    db.upsert("rooms", [
        {"id": r["id"], "name": r["name"], "capacity": r["capacity"], "type": r["type"]}
        for r in seed_data["rooms"]], key="id")
    db.upsert("sections", [
        {"id": s["id"], "name": s["name"], "students": s["students"]}
        for s in seed_data["sections"]], key="id")
    subjects, links = {}, []
    for g in seed_data["courseSections"]:
        subjects[g["id"]] = {"id": g["id"], "code": g["code"], "name": g["name"]}
        links.append({
            "section_id": g["sectionId"], "subject_id": g["id"],
            "faculty_id": g["facultyId"],
            "lecture_hours": g.get("lectureHours", 3),
            "practical_hours": g.get("practicalHours", 2),
        })
    db.upsert("subjects", list(subjects.values()), key="id")
    db.upsert("section_subjects", links, key="section_id,subject_id")
    counts = {k: len(v) for k, v in {
        "faculty": seed_data["faculty"], "rooms": seed_data["rooms"],
        "sections": seed_data["sections"], "subjects": list(subjects.values()),
        "assignments": links}.items()}
    return {"seeded": counts}


# ---- scheduling ------------------------------------------------------------

@app.post("/api/solve")
def solve(_: dict = Depends(require_coordinator)):
    uni = db.load_university()
    if not uni["sections"] or not uni["section_subjects"]:
        raise HTTPException(400, "No teaching data yet — import or seed the university data first")
    days, num_slots, sections, rooms, faculty_max, assignments = db.solver_inputs(uni)
    result = solve_timetable(days, num_slots, sections, rooms, faculty_max, assignments)
    if result["status"] != "ok":
        return result  # infeasible / unknown, with a human-readable detail

    db.archive_schedules()
    schedule_id = db.insert_schedule(result["sessions"], result["stats"])
    return {"status": "ok", "scheduleId": schedule_id, "stats": result["stats"]}


@app.get("/api/schedule")
def schedule(user: dict = Depends(require_user)):
    row = db.active_schedule()
    if not row:
        return {"status": "empty"}
    return {"status": "ok", "scheduleId": row["id"],
            "createdAt": row["createdAt"], "stats": row["stats"],
            "sessions": row["sessions"]}


# ---- room requests ---------------------------------------------------------

def _letter_file_path(stored_name: str) -> str:
    return os.path.join(LETTERS_DIR, stored_name)


@app.post("/api/requests")
async def create_request(section_id: str = Form(...),
                         preferred_room: str = Form(""),
                         reason: str = Form(...),
                         letter: UploadFile | None = File(None),
                         user: dict = Depends(require_user)):
    letter_path = None
    if letter and letter.filename:
        os.makedirs(LETTERS_DIR, exist_ok=True)
        safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in letter.filename)
        stored_name = f"{user['id'][:8]}-{secrets.token_hex(6)}-{safe}"
        with open(_letter_file_path(stored_name), "wb") as fh:
            fh.write(await letter.read())
        letter_path = stored_name
    request_id = db.insert_request(user["id"], section_id,
                                   preferred_room or None, reason, letter_path)
    return {"id": request_id}


@app.get("/api/requests")
def list_requests(user: dict = Depends(require_user)):
    rows = (db.list_requests() if user["role"] == "coordinator"
            else db.list_requests(user_id=user["id"]))
    return {"requests": rows}


@app.get("/api/requests/{request_id}/letter")
def request_letter(request_id: int, user: dict = Depends(require_user)):
    row = db.get_request(request_id)
    if not row or not row["letter_path"]:
        raise HTTPException(404, "No letter attached")
    if user["role"] != "coordinator" and row["user_id"] != user["id"]:
        raise HTTPException(403, "Not your request")
    path = _letter_file_path(row["letter_path"])
    if not os.path.exists(path):
        raise HTTPException(404, "Letter file missing")
    return FileResponse(path, filename=row["letter_path"].split("-", 2)[-1])


@app.post("/api/requests/{request_id}/decision")
def decide(request_id: int, status: str = Form(...),
           user: dict = Depends(require_coordinator)):
    if status not in ("approved", "rejected"):
        raise HTTPException(400, "Decision must be approved or rejected")
    if not db.get_request(request_id):
        raise HTTPException(404, "Request not found")
    db.decide_request(request_id, status, user["id"])
    return {"id": request_id, "status": status}
