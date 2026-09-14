import json
import sys

sys.path.insert(0, "/workspace/github-agent/backend")
import app.solver as S

data = json.load(open("/workspace/github-agent/backend/seed/university_data.json"))
N = int(sys.argv[1]) if len(sys.argv) > 1 else 2
days = data["days"]
sections = data["sections"][:N]
sids = [s["id"] for s in sections]
rooms = data["rooms"]
faculty_max = {f["id"]: f.get("maxPerDay", 4) for f in data["faculty"]}
assign_all = [
    {"section_id": g["sectionId"], "subject_id": g["id"], "faculty_id": g["facultyId"],
     "lecture_hours": g["lectureHours"], "practical_hours": g["practicalHours"]}
    for g in data["courseSections"]
]
assign_by_sec = {}
for a in assign_all:
    assign_by_sec.setdefault(a["section_id"], []).append(a)

# ---- construct a valid schedule --------------------------------------------
# Active day k: practical of subject k at slots (0,1); lectures of subjects
# (k+1..k+3) mod 5 at slots 2,3,4 (block 1). Distinct lab/class per section/day.
free_day = {sid: 4 + idx % 2 for idx, sid in enumerate(sids)}
labs = [r["id"] for r in rooms if r["type"] == "Lab"]
classes = [r["id"] for r in rooms if r["type"] == "Class"]
sched = {}
for idx, sid in enumerate(sids):
    for k in range(5):
        d = k if k < free_day[sid] else k + 1
        lab = labs[(idx * 6 + k) % len(labs)]
        cls1 = classes[(idx * 8 + k) % len(classes)]
        sess = [{"subject": k, "slots": (0, 1), "room": lab, "type": "practical"}]
        for j, sl in enumerate((2, 3, 4)):
            sess.append({"subject": (k + 1 + j) % 5, "slots": (sl,), "room": cls1, "type": "lecture"})
        sched[sid, d] = sess

# validity of the construction
occ_map = {}
for (sid, d), sess in sched.items():
    for x in sess:
        for sl in x["slots"]:
            key = (sid, d, sl)
            assert key not in occ_map, f"section clash {key}"
            occ_map[key] = x["room"]
by_slot = {}
for (sid, d, sl), room in occ_map.items():
    by_slot.setdefault((d, sl), []).append(room)
for (d, sl), rs in by_slot.items():
    assert len(rs) == len(set(rs)), f"room clash day {d} slot {sl}"
print("constructed schedule: clash-free, practicals in labs")

# ---- rebuild the model, expose internals -----------------------------------
src = open("/workspace/github-agent/backend/app/solver.py").read()
src = src.replace(
    "    solver = cp_model.CpSolver()",
    "    if _DBG.get('expose'):\n"
    "        _DBG.update(model=model, P=P, L=L, F=F, occ=occ, B=B, R=R, W=W,\n"
    "                    subjects_by_section=subjects_by_section)\n"
    "        return {'status': 'exposed'}\n"
    "    solver = cp_model.CpSolver()"
)
mod = type(sys)("solver_dbg")
exec(compile(src, "solver_dbg", "exec"), mod.__dict__)
mod._DBG = {"expose": True}

r = mod.solve_timetable(days, 8, sections, rooms, faculty_max, assign_all, 30)
assert r["status"] == "exposed"
model = mod._DBG["model"]
P, L, F, occ, B, R, W = (mod._DBG[k] for k in ("P", "L", "F", "occ", "B", "R", "W"))
subs_map = mod._DBG["subjects_by_section"]

def bOf(sl):
    return 0 if sl < 2 else (1 if sl < 5 else 2)

# ---- variable values from the constructed schedule -------------------------
val = {}
for s in sections:
    sid = s["id"]
    subs = subs_map[sid]
    for d in range(len(days)):
        free = d == free_day[sid]
        val[f"F_{sid}_{d}"] = free
        day_sess = sched.get((sid, d), [])
        occ_by_slot = {}
        for x in day_sess:
            for sl in x["slots"]:
                occ_by_slot[sl] = x
        for sl in range(8):
            x = occ_by_slot.get(sl)
            val[f"occ_{sid}_{d}_{sl}"] = x is not None
            for room in rooms:
                wk = f"W_{sid}_{d}_{sl}_{room['id']}"
                if wk in val or True:
                    val[wk] = x is not None and x["room"] == room["id"]
        for b in range(3):
            rooms_b = {x["room"] for x in day_sess if bOf(x["slots"][0]) == b} if day_sess else set()
            val[f"B_{sid}_{d}_{b}"] = bool(rooms_b)
            for room in rooms:
                k = f"R_{sid}_{d}_{b}_{room['id']}"
                val[k] = room["id"] in rooms_b
    for i, a in enumerate(subs):
        for d in range(len(days)):
            for st in (0, 2, 5, 6):
                val[f"P_{sid}_{i}_{d}_{st}"] = any(
                    x["subject"] == i and x["type"] == "practical" and x["slots"][0] == st
                    for x in sched.get((sid, d), []))
            for sl in range(8):
                val[f"L_{sid}_{i}_{d}_{sl}"] = any(
                    x["subject"] == i and x["type"] == "lecture" and sl in x["slots"]
                    for x in sched.get((sid, d), []))

# ---- evaluate every linear constraint against these values -----------------
proto = model.Proto()
names = [v.name for v in proto.variables]
violated = 0
for ci, c in enumerate(proto.constraints):
    if not c.has_linear:
        continue
    lc = c.linear
    total = 0
    for vi, coef in zip(lc.vars, lc.coeffs):
        nm = names[vi]
        v = val.get(nm)
        if v is None:
            print(f"UNHINTED VAR {nm} in constraint {ci}")
            v = 0
        total += coef * int(v)
    dom = lc.domain
    ivs = [(dom[i], dom[i + 1]) for i in range(0, len(dom), 2)]
    ok = any(lo <= total <= hi for lo, hi in ivs)
    if not ok:
        violated += 1
        if violated <= 8:
            terms = "+".join(f"{coef}*{names[vi]}" for vi, coef in zip(lc.vars, lc.coeffs) if coef)
            print(f"VIOLATED c{ci}: {terms} = {total}, allowed {ivs}")
print(f"violated constraints: {violated} of {len(proto.constraints)}")
