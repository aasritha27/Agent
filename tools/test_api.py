import json
import sys

sys.path.insert(0, "/workspace/github-agent/backend")

from fastapi.testclient import TestClient
import app.db as db
import app.main as main_mod

seed = json.load(open("/workspace/github-agent/backend/seed/university_data.json"))

# ---- fake supabase client ---------------------------------------------------
class Table:
    def __init__(self, name, store):
        self.name, self.store = name, store
        self.q = {}
        self.payload = None
        self.single = False

    def select(self, *a, **k):
        self.count = k.get("count")
        return self

    def eq(self, col, val):
        self.q.setdefault(col, val)
        return self

    def order(self, *a, **k):
        return self

    def limit(self, n):
        return self

    def upsert(self, rows):
        self.payload = rows
        return self

    def insert(self, row):
        self.payload = row
        return self

    def update(self, row):
        self.payload = row
        return self

    def execute(self):
        rows = self.store.setdefault(self.name, [])
        if self.payload is not None:
            if isinstance(self.payload, list):
                for r in self.payload:
                    if self.name == "schedules" and "sessions" in r:
                        rows[:] = [x for x in rows if x.get("status") != r.get("status", "active") or x.get("status") != "active"]
                        rows.append({"id": "sched-1", "created_at": "now", **r})
                    elif isinstance(r, dict):
                        keys = [k for k in r if k in rows[0]] if rows else []
                        if keys:
                            for x in rows:
                                if all(x.get(k) == r[k] for k in keys):
                                    x.update(r)
                        else:
                            rows.extend(r if isinstance(r, list) else [r])
                    else:
                        rows.append(r)
            elif self.payload and self.name == "schedules" and "sessions" in self.payload:
                rows.append({"id": "sched-1", "created_at": "now", **self.payload})
            elif self.payload and "status" in self.payload and self.name == "schedules":
                for x in rows:
                    if x.get("status") == "active":
                        x["status"] = self.payload["status"]
            elif self.payload and "role" in self.payload and self.name == "profiles":
                uid = self.q.get("id")
                for x in rows:
                    if x["id"] == uid:
                        x["role"] = self.payload["role"]
            elif self.payload and self.name == "room_requests" and "user_id" in self.payload:
                rows.append({"id": "req-1", "created_at": "now",
                             "profiles": {"full_name": "Test"}, **self.payload})
                for x in rows:
                    if x["id"] == self.q.get("id"):
                        x.update(self.payload)
            self.payload = None
        data = rows
        if self.q:
            data = [x for x in rows if all(x.get(k) == v for k, v in self.q.items())]
        count = len(data) if getattr(self, "count", None) else None
        if self.single and data:
            data = data[0]
        return type("R", (), {"data": data, "count": count})()


store = {
    "faculty": [{"id": f["id"], "name": f["name"], "max_per_day": f.get("maxPerDay", 4)} for f in seed["faculty"]],
    "rooms": [{"id": r["id"], "name": r["name"], "capacity": r["capacity"], "type": r["type"]} for r in seed["rooms"]],
    "sections": [{"id": s["id"], "name": s["name"], "students": s["students"]} for s in seed["sections"]],
    "subjects": [{"id": g["id"], "code": g["code"], "name": g["name"]} for g in seed["courseSections"]],
    "section_subjects": [
        {"section_id": g["sectionId"], "subject_id": g["id"], "faculty_id": g["facultyId"],
         "lecture_hours": g.get("lectureHours", 3), "practical_hours": g.get("practicalHours", 2)}
        for g in seed["courseSections"]],
    "profiles": [{"id": "u1", "role": "coordinator", "full_name": "Test"}],
    "schedules": [],
    "room_requests": [],
}

class FakeStorage:
    def from_(self, bucket):
        return type("B", (), {"upload": staticmethod(lambda p, b, o=None: None),
                              "create_signed_url": staticmethod(lambda p, e=0: {"signedURL": "https://signed/" + p})})()

class FakeClient:
    def table(self, name):
        return Table(name, store)

    @property
    def storage(self):
        return FakeStorage()

db._client = FakeClient()

USER = {"id": "u1", "email": "t@x.ac.in", "role": "coordinator"}
app = main_mod.app
app.dependency_overrides[main_mod.require_user] = lambda: USER
app.dependency_overrides[main_mod.require_coordinator] = lambda: USER
app.dependency_overrides[main_mod.current_user] = lambda: USER

c = TestClient(app)
out = []

r = c.get("/api/health")
out.append(("health", r.status_code, r.json()))

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
