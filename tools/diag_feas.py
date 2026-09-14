import json
import sys

sys.path.insert(0, "/workspace/github-agent/backend")
sys.path.insert(0, "/workspace/github-agent/tools")

# monkey-flag bisect: rebuild solver with relaxations by editing module constants
import importlib
import app.solver as solver_mod

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

# check faculty sharing structure
from collections import Counter
fac_load = Counter(a["faculty_id"] for a in assignments)
print("faculty assignment counts:", sorted(Counter(fac_load.values()).items()))
print("faculty with most assignments:", fac_load.most_common(3))

# reduce to a tiny subset to see if even 2 sections are feasible
def try_subset(n_sections, relax=frozenset()):
    sub_sections = sections[:n_sections]
    sub_ids = {s["id"] for s in sub_sections}
    sub_assign = [a for a in assignments if a["section_id"] in sub_ids]
    r = solver_mod.solve_timetable(
        days, num_slots, sub_sections, rooms, faculty_max, sub_assign, time_limit_s=30
    )
    return r["status"]

for n in (1, 2, 4, 8, 16, 34):
    print(f"{n} sections ->", try_subset(n))
