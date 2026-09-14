import json
import os
import sys
import tempfile

sys.path.insert(0, "/workspace/github-agent/backend")

# Fresh SQLite database in a temp dir, before app.config reads DATA_DIR.
_tmp = tempfile.mkdtemp()
os.environ["DATA_DIR"] = _tmp

import app.db as db
import app.main as main_mod

db.init()

seed = json.load(open("/workspace/github-agent/backend/seed/university_data.json"))

# Seed the DB directly (same shape /api/seed produces).
db.upsert("faculty", [{"id": f["id"], "name": f["name"], "max_per_day": f.get("maxPerDay", 4)} for f in seed["faculty"]], key="id")
db.upsert("rooms", [{"id": r["id"], "name": r["name"], "capacity": r["capacity"], "type": r["type"]} for r in seed["rooms"]], key="id")
db.upsert("sections", [{"id": s["id"], "name": s["name"], "students": s["students"]} for s in seed["sections"]], key="id")
subjects, links = {}, []
for g in seed["courseSections"]:
    subjects[g["id"]] = {"id": g["id"], "code": g["code"], "name": g["name"]}
    links.append({"section_id": g["sectionId"], "subject_id": g["id"], "faculty_id": g["facultyId"],
                  "lecture_hours": g.get("lectureHours", 3), "practical_hours": g.get("practicalHours", 2)})
db.upsert("subjects", list(subjects.values()), key="id")
db.upsert("section_subjects", links, key="section_id,subject_id")

# A real user row so role lookups resolve.
import uuid as _uuid
from app.auth import hash_password
COORD_ID = str(_uuid.uuid4())
db.create_user(COORD_ID, "t@x.ac.in", hash_password("secret123"), "Test")
db.set_role(COORD_ID, "coordinator")

USER = {"id": COORD_ID, "email": "t@x.ac.in", "role": "coordinator", "full_name": "Test"}
app = main_mod.app
app.dependency_overrides[main_mod.require_user] = lambda: USER
app.dependency_overrides[main_mod.require_coordinator] = lambda: USER
app.dependency_overrides[main_mod.current_user] = lambda: USER

from fastapi.testclient import TestClient
c = TestClient(app)
out = []

r = c.get("/api/health")
out.append(("health", r.status_code, r.json()))

# Real signup + login round trip (unoverridden auth endpoints).
c2 = TestClient(main_mod.app)
r = c2.post("/api/auth/signup", data={"email": "new@x.ac.in", "password": "secret123"})
out.append(("signup", r.status_code, list(r.json().keys())))
r = c2.post("/api/auth/login", data={"email": "new@x.ac.in", "password": "secret123"})
out.append(("login", r.status_code, list(r.json().keys())))
r = c2.post("/api/auth/login", data={"email": "new@x.ac.in", "password": "wrong"})
out.append(("bad login", r.status_code, ""))

r = c.get("/api/university")
out.append(("university", r.status_code, f"{len(r.json()['sections'])} sections"))

r = c.post("/api/solve")
out.append(("solve", r.status_code, r.json()))

r = c.get("/api/schedule")
sched = r.json()
out.append(("schedule", r.status_code,
            f"{sched['status']}, {len(sched.get('sessions', []))} sessions"))

r = c.post("/api/requests", data={"section_id": seed["sections"][0]["id"],
                                  "preferred_room": "", "reason": "need lab"})
out.append(("request", r.status_code, r.json()))

r = c.get("/api/requests")
out.append(("requests list", r.status_code, f"{len(r.json()['requests'])} request(s)"))

r = c.post(f"/api/requests/{r.json()['requests'][0]['id']}/decision", data={"status": "approved"})
out.append(("decide", r.status_code, r.json()))

with open("/tmp/api_smoke.txt", "w") as f:
    for name, code, info in out:
        f.write(f"{name}: {code} {info}\n")
print("done")
