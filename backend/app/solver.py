"""CP-SAT timetable solver.

Replaces the old retry-and-hope JS generator. Every hard constraint from the
department spec is a first-class solver constraint:

  1. No section/faculty/room is ever double-booked (implicit in the model).
  2. Exactly one 2-period practical per section per active day; each subject
     gets exactly one practical per week and its lecture hours per week.
  3. Room stability: within a day a section keeps ONE room per teaching block
     (P1-P2, P3-P5, P6-P8); room changes only across the two breaks.
  4. Lab rule: practicals only in Lab rooms; lectures may use a lab
     (they share the practical's lab) but never the reverse.
  5. Room capacity: a room can only host a section that fits.
  6. Faculty daily cap (max_per_day, default 4).
  7. One free day per section; teaching days are a solver decision.

If a valid schedule exists CP-SAT finds it; if not it returns INFEASIBLE
instead of failing silently after N retries.
"""

from __future__ import annotations

from typing import Any

from ortools.sat.python import cp_model

# Slots after which the short break / lunch break fall (0-indexed periods).
# Blocks: 0 = P1-P2 (slots 0-1), 1 = P3-P5 (slots 2-4), 2 = P6-P8 (slots 5-7).
BLOCK_STARTS = (0, 2, 5)
PRACTICAL_STARTS = (0, 2, 5, 6)  # continuous, never spanning a break


def block_of(slot: int) -> int:
    if slot < 2:
        return 0
    return 1 if slot < 5 else 2


def solve_timetable(
    days: list[str],
    num_slots: int,
    sections: list[dict[str, Any]],
    rooms: list[dict[str, Any]],
    faculty_max: dict[str, int],
    assignments: list[dict[str, Any]],
    time_limit_s: float = 30.0,
) -> dict[str, Any]:
    """assignments: [{section_id, subject_id, faculty_id, lecture_hours, practical_hours}].

    Returns {status: "ok", sessions: [...], stats: {...}} or
    {status: "infeasible", detail: "..."} / {status: "unknown", detail: "..."}.
    """
    model = cp_model.CpModel()

    subjects_by_section: dict[str, list[dict[str, Any]]] = {}
    for a in assignments:
        subjects_by_section.setdefault(a["section_id"], []).append(a)

    section_by_id = {s["id"]: s for s in sections}

    # ---- decision variables -------------------------------------------------
    # P[(s,i,d,st)]: section s's subject i has its weekly practical on day d
    # starting at slot st.
    P: dict[tuple, cp_model.IntVar] = {}
    # L[(s,i,d,sl)]: lecture of subject i of section s at day d, slot sl.
    L: dict[tuple, cp_model.IntVar] = {}
    # F[(s,d)]: day d is section s's free day.
    F: dict[tuple, cp_model.IntVar] = {}
    # occ[(s,d,sl)]: section s has any class at day d, slot sl.
    occ: dict[tuple, cp_model.IntVar] = {}
    # B[(s,d,b)]: block b of day d is active for section s.
    B: dict[tuple, cp_model.IntVar] = {}
    # R[(s,d,b,r)]: block b of day d for section s is held in room r.
    R: dict[tuple, cp_model.IntVar] = {}
    # W[(s,d,sl,r)]: section s occupies room r at day d slot sl.
    W: dict[tuple, cp_model.IntVar] = {}

    for s in sections:
        sid = s["id"]
        subs = subjects_by_section.get(sid, [])
        if not subs:
            continue
        for d in range(len(days)):
            F[sid, d] = model.NewBoolVar(f"F_{sid}_{d}")
            for sl in range(num_slots):
                occ[sid, d, sl] = model.NewBoolVar(f"occ_{sid}_{d}_{sl}")
            for b in range(len(BLOCK_STARTS)):
                B[sid, d, b] = model.NewBoolVar(f"B_{sid}_{d}_{b}")
                for r in rooms:
                    if r["capacity"] >= s["students"]:
                        R[sid, d, b, r["id"]] = model.NewBoolVar(
                            f"R_{sid}_{d}_{b}_{r['id']}"
                        )
            for sl in range(num_slots):
                for r in rooms:
                    key = (sid, d, sl, r["id"])
                    if (sid, d, block_of(sl), r["id"]) in R:
                        W[key] = model.NewBoolVar(f"W_{sid}_{d}_{sl}_{r['id']}")
        for i, a in enumerate(subs):
            for d in range(len(days)):
                for st in PRACTICAL_STARTS:
                    P[sid, i, d, st] = model.NewBoolVar(f"P_{sid}_{i}_{d}_{st}")
                for sl in range(num_slots):
                    L[sid, i, d, sl] = model.NewBoolVar(f"L_{sid}_{i}_{d}_{sl}")

    for s in sections:
        sid = s["id"]
        subs = subjects_by_section.get(sid, [])
        if not subs:
            continue
        # exactly one free day; teaching happens on the other days
        model.Add(sum(F[sid, d] for d in range(len(days))) == 1)
        for d in range(len(days)):
            # exactly one practical per active day
            model.Add(
                sum(P[sid, i, d, st] for i in range(len(subs)) for st in PRACTICAL_STARTS)
                + F[sid, d]
                == 1
            )
            # exactly three lecture periods per active day
            model.Add(
                sum(L[sid, i, d, sl] for i in range(len(subs)) for sl in range(num_slots))
                + 3 * F[sid, d]
                == 3
            )
            for sl in range(num_slots):
                covering = [
                    P[sid, i, d, st]
                    for i in range(len(subs))
                    for st in PRACTICAL_STARTS
                    if st <= sl < st + 2
                ]
                model.Add(
                    occ[sid, d, sl]
                    == sum(L[sid, i, d, sl] for i in range(len(subs))) + sum(covering)
                )
            for b, bstart in enumerate(BLOCK_STARTS):
                bslots = [
                    sl
                    for sl in range(num_slots)
                    if block_of(sl) == b
                ]
                # block is active iff it holds any session
                for sl in bslots:
                    model.Add(B[sid, d, b] >= occ[sid, d, sl])
                model.Add(
                    B[sid, d, b] <= sum(occ[sid, d, sl] for sl in bslots)
                )
                # one room per block
                model.Add(
                    sum(
                        R[sid, d, b, r["id"]]
                        for r in rooms
                        if (sid, d, b, r["id"]) in R
                    )
                    == B[sid, d, b]
                )
            for sl in range(num_slots):
                # a session occupies exactly one room, from its block's room
                model.Add(
                    sum(
                        W[sid, d, sl, r["id"]]
                        for r in rooms
                        if (sid, d, sl, r["id"]) in W
                    )
                    == occ[sid, d, sl]
                )
                # and that room must be the block's chosen room
                for r in rooms:
                    wkey = (sid, d, sl, r["id"])
                    if wkey in W:
                        model.Add(W[wkey] <= R[sid, d, block_of(sl), r["id"]])
        for i, a in enumerate(subs):
            # each subject: exactly one weekly practical + its lecture hours
            model.Add(
                sum(P[sid, i, d, st] for d in range(len(days)) for st in PRACTICAL_STARTS)
                == (a["practical_hours"] // 2)
            )
            model.Add(
                sum(L[sid, i, d, sl] for d in range(len(days)) for sl in range(num_slots))
                == a["lecture_hours"]
            )
            for d in range(len(days)):
                # practical must sit in a lab that fits the section
                for st in PRACTICAL_STARTS:
                    if st + 1 >= num_slots:
                        continue
                    lab_rooms = [
                        R[sid, d, block_of(st), r["id"]]
                        for r in rooms
                        if r["type"] == "Lab"
                        and r["capacity"] >= s["students"]
                        and (sid, d, block_of(st), r["id"]) in R
                    ]
                    if lab_rooms:
                        model.Add(P[sid, i, d, st] <= sum(lab_rooms))
                    else:
                        model.Add(P[sid, i, d, st] == 0)

    # room uniqueness: a room hosts at most one section per slot
    for r in rooms:
        for d in range(len(days)):
            for sl in range(num_slots):
                terms = [
                    W[x["id"], d, sl, r["id"]]
                    for x in sections
                    if (x["id"], d, sl, r["id"]) in W
                ]
                if len(terms) > 1:
                    model.Add(sum(terms) <= 1)

    # faculty uniqueness per slot and daily cap
    fac_assign: dict[str, list[tuple[str, int]]] = {}
    for s in sections:
        subs = subjects_by_section.get(s["id"], [])
        for i, a in enumerate(subs):
            fac_assign.setdefault(a["faculty_id"], []).append((s["id"], i))
    for f, pairs in fac_assign.items():
        cap = faculty_max.get(f, 4)
        for d in range(len(days)):
            daily = []
            for sl in range(num_slots):
                terms = []
                for sid, i in pairs:
                    terms.append(L[sid, i, d, sl])
                    terms.extend(
                        P[sid, i, d, st]
                        for st in PRACTICAL_STARTS
                        if st <= sl < st + 2
                    )
                if len(terms) > 1:
                    model.Add(sum(terms) <= 1)
                daily.extend(terms)
            model.Add(sum(daily) <= cap)

    # ---- solve --------------------------------------------------------------
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_s
    solver.parameters.num_workers = 8
    status = solver.Solve(model)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        kind = "infeasible" if status == cp_model.INFEASIBLE else "unknown"
        return {
            "status": kind,
            "detail": (
                "No timetable satisfies all constraints (room capacities, faculty "
                "daily limits, lab rule, room stability). Check the imported data."
                if kind == "infeasible"
                else "Solver timed out; try again or relax a constraint."
            ),
        }

    sessions = []
    for s in sections:
        sid = s["id"]
        subs = subjects_by_section.get(sid, [])
        for i, a in enumerate(subs):
            for d in range(len(days)):
                for st in PRACTICAL_STARTS:
                    if solver.Value(P[sid, i, d, st]):
                        room = _block_room(solver, R, sid, d, block_of(st), rooms)
                        sessions.append(
                            {
                                "id": f"{sid}-{a['subject_id']}-practical-{d}",
                                "day": days[d],
                                "slot": st,
                                "sectionId": sid,
                                "courseId": f"{a['subject_id']}-practical",
                                "facultyId": a["faculty_id"],
                                "roomId": room,
                                "duration": 2,
                            }
                        )
                for sl in range(num_slots):
                    if solver.Value(L[sid, i, d, sl]):
                        room = _block_room(solver, R, sid, d, block_of(sl), rooms)
                        sessions.append(
                            {
                                "id": f"{sid}-{a['subject_id']}-lecture-{d}-{sl}",
                                "day": days[d],
                                "slot": sl,
                                "sectionId": sid,
                                "courseId": f"{a['subject_id']}-lecture",
                                "facultyId": a["faculty_id"],
                                "roomId": room,
                                "duration": 1,
                            }
                        )

    hours = sum(x["duration"] for x in sessions)
    return {
        "status": "ok",
        "sessions": sessions,
        "stats": {
            "sections": len(sections),
            "sessions": len(sessions),
            "weeklyHours": hours,
            "solveSeconds": round(solver.WallTime(), 2),
        },
    }


def _block_room(
    solver: cp_model.CpSolver,
    R: dict[tuple, cp_model.IntVar],
    sid: str,
    d: int,
    b: int,
    rooms: list[dict[str, Any]],
) -> str:
    for r in rooms:
        key = (sid, d, b, r["id"])
        if key in R and solver.Value(R[key]):
            return r["id"]
    return ""
