import json
import sys

sys.path.insert(0, "/workspace/github-agent/backend")

from app.solver import solve_timetable, block_of

data = json.load(open("/workspace/github-agent/backend/seed/university_data.json"))

days = data["days"]
num_slots = len(data["periods"])
sections = data["sections"]
rooms = data["rooms"]
faculty_max = {f["id"]: f.get("maxPerDay", 4) for f in data["faculty"]}
assignments = [
    {
        "section_id": g["sectionId"],
        "subject_id": g["id"],
        "faculty_id": g["facultyId"],
        "lecture_hours": g["lectureHours"],
        "practical_hours": g["practicalHours"],
    }
    for g in data["courseSections"]
]

result = solve_timetable(days, num_slots, sections, rooms, faculty_max, assignments, time_limit_s=60)
print("status:", result["status"])
if result["status"] != "ok":
    print("detail:", result.get("detail"))
    sys.exit(1)

print("stats:", result["stats"])
sessions = result["sessions"]

# ---- independent validation ------------------------------------------------
room_type = {r["id"]: r["type"] for r in rooms}
room_cap = {r["id"]: r["capacity"] for r in rooms}
sec_students = {s["id"]: s["students"] for s in sections}
errors = []

# 1. double-booking: room/faculty/section never at two places in one slot
seen = {}
for s in sessions:
    for w in range(s["duration"]):
        slot = s["slot"] + w
        for key in (s["roomId"], s["facultyId"], s["sectionId"]):
            k = (key, s["day"], slot)
            if k in seen:
                errors.append(f"double-booked {k}")
            seen[k] = True

# 2. room stability: one room per (section, day, block)
blocks = {}
for s in sessions:
    for w in range(s["duration"]):
        b = block_of(s["slot"] + w)
        key = (s["sectionId"], s["day"], b)
        blocks.setdefault(key, set()).add(s["roomId"])
for key, rs in blocks.items():
    if len(rs) > 1:
        errors.append(f"mid-block room change at {key}: {rs}")

# 3. lab rule: practicals in Labs only
for s in sessions:
    if "practical" in s["courseId"]:
        for w in range(s["duration"]):
            if room_type[s["roomId"]] != "Lab":
                errors.append(f"practical in non-lab {s['roomId']}")
                break
        if room_cap[s["roomId"]] < sec_students[s["sectionId"]]:
            errors.append(f"lab too small {s['roomId']}")

# 4. faculty daily cap
fac_day = {}
for s in sessions:
    for w in range(s["duration"]):
        fac_day.setdefault((s["facultyId"], s["day"]), set()).add(s["slot"] + w)
for (f, d), slots in fac_day.items():
    if len(slots) > 4:
        errors.append(f"faculty {f} over cap on {d}: {len(slots)}")

# 5. per-section weekly load: 25 periods, one practical per subject (2h) + 3 lectures
per_section = {}
for s in sessions:
    per_section.setdefault(s["sectionId"], []).append(s)
for sid, ss in per_section.items():
    hours = sum(x["duration"] for x in ss)
    if hours != 25:
        errors.append(f"{sid} has {hours} hours")
    # free days
    taught_days = {x["day"] for x in ss}
    if len(taught_days) != 5:
        errors.append(f"{sid} teaches {len(taught_days)} days")
    # each subject: exactly one practical
    pracs = [x for x in ss if "practical" in x["courseId"]]
    subjects = {x["courseId"] for x in pracs}
    if len(pracs) != 5 or len(subjects) != 5:
        errors.append(f"{sid} has {len(pracs)} practicals over {len(subjects)} subjects")

# 6. no practical spans a break
BREAKS_AFTER = {1, 4}
for s in sessions:
    if s["duration"] == 2 and (s["slot"] in BREAKS_AFTER):
        errors.append(f"practical spans break at slot {s['slot']}")

print("validation errors:", len(errors))
for e in errors[:10]:
    print("  ", e)
print("RESULT:", "PASS" if not errors else "FAIL")

# save a sample for inspection
json.dump(sessions, open("/tmp/solver_output.json", "w"), indent=1)
