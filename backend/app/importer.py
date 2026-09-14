"""Real CSV/Excel import: pandas parses the upload, every row is validated,
good rows are upserted into the database, bad rows come back with reasons.

Supported kinds and expected columns (order doesn't matter, case-insensitive,
extra columns ignored):

  faculty:    name, max_per_day (optional, default 4)
  rooms:      name, capacity, type (Class or Lab)
  sections:   name, students
  subjects:   code, name
  assignments: section, subject_code, faculty_name, lecture_hours, practical_hours
"""

from __future__ import annotations

import io
import re
from typing import Any

import pandas as pd

from . import db

KINDS = ("faculty", "rooms", "sections", "subjects", "assignments")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).strip().lower()).strip("-")


def read_frame(filename: str, content: bytes) -> pd.DataFrame:
    if filename.lower().endswith((".xlsx", ".xls")):
        return pd.read_excel(io.BytesIO(content))
    return pd.read_csv(io.BytesIO(content))


def _clean(df: pd.DataFrame) -> list[dict[str, Any]]:
    cols = {c.strip().lower().replace(" ", "_"): c for c in df.columns}
    rows = []
    for _, raw in df.iterrows():
        row = {}
        for key, original in cols.items():
            v = raw[original]
            if pd.isna(v):
                row[key] = None
            elif hasattr(v, "item"):
                row[key] = v.item()
            else:
                row[key] = v
        rows.append(row)
    return rows


def import_kind(kind: str, filename: str, content: bytes) -> dict[str, Any]:
    if kind not in KINDS:
        return {"error": f"Unknown import type '{kind}'. Use one of: {', '.join(KINDS)}."}
    try:
        df = read_frame(filename, content)
    except Exception as e:
        return {"error": f"Could not parse the file: {e}"}

    rows = _clean(df)
    sb = db.client()
    valid, errors = [], []

    def fail(n, reason):
        errors.append({"row": n + 2, "reason": reason})  # +2: header + 1-index

    if kind == "faculty":
        for n, r in enumerate(rows):
            name = (r.get("name") or "").strip()
            if not name:
                fail(n, "name is required")
                continue
            mpd = r.get("max_per_day") or 4
            if not isinstance(mpd, (int, float)) or mpd < 1:
                fail(n, f"max_per_day '{mpd}' is not a positive number")
                continue
            valid.append({"id": r.get("id") or f"f-{_slug(name)}", "name": name,
                          "max_per_day": int(mpd)})
        if valid:
            sb.table("faculty").upsert(valid).execute()
    elif kind == "rooms":
        for n, r in enumerate(rows):
            name = (r.get("name") or "").strip()
            cap = r.get("capacity")
            rtype = (r.get("type") or "Class").strip().capitalize()
            if not name:
                fail(n, "name is required")
                continue
            if not isinstance(cap, (int, float)) or cap <= 0:
                fail(n, f"capacity '{cap}' is not a positive number")
                continue
            if rtype not in ("Class", "Lab"):
                fail(n, f"type must be Class or Lab, got '{rtype}'")
                continue
            valid.append({"id": r.get("id") or f"room-{_slug(name)}", "name": name,
                          "capacity": int(cap), "type": rtype})
        if valid:
            sb.table("rooms").upsert(valid).execute()
    elif kind == "sections":
        for n, r in enumerate(rows):
            name = (r.get("name") or "").strip()
            students = r.get("students")
            if not name:
                fail(n, "name is required")
                continue
            if not isinstance(students, (int, float)) or students <= 0:
                fail(n, f"students '{students}' is not a positive number")
                continue
            valid.append({"id": r.get("id") or f"sec-{_slug(name)}", "name": name,
                          "students": int(students)})
        if valid:
            sb.table("sections").upsert(valid).execute()
    elif kind == "subjects":
        for n, r in enumerate(rows):
            code = str(r.get("code") or "").strip()
            name = (r.get("name") or "").strip()
            if not code or not name:
                fail(n, "code and name are required")
                continue
            valid.append({"id": r.get("id") or f"sub-{_slug(code)}", "code": code,
                          "name": name})
        if valid:
            sb.table("subjects").upsert(valid).execute()
    elif kind == "assignments":
        uni = db.load_university()
        sec_by_name = {s["name"].strip().lower(): s["id"] for s in uni["sections"]}
        sub_by_code = {str(s["code"]).strip().lower(): s["id"] for s in uni["subjects"]}
        fac_by_name = {f["name"].strip().lower(): f["id"] for f in uni["faculty"]}
        for n, r in enumerate(rows):
            sec = sec_by_name.get(str(r.get("section") or "").strip().lower())
            sub = sub_by_code.get(str(r.get("subject_code") or "").strip().lower())
            fac = fac_by_name.get(str(r.get("faculty_name") or "").strip().lower())
            if not sec:
                fail(n, f"section '{r.get('section')}' not found — import sections first")
                continue
            if not sub:
                fail(n, f"subject code '{r.get('subject_code')}' not found — import subjects first")
                continue
            if not fac:
                fail(n, f"faculty '{r.get('faculty_name')}' not found — import faculty first")
                continue
            lh = r.get("lecture_hours") or 3
            ph = r.get("practical_hours") or 2
            if not isinstance(lh, (int, float)) or not isinstance(ph, (int, float)):
                fail(n, "lecture_hours and practical_hours must be numbers")
                continue
            valid.append({"section_id": sec, "subject_id": sub, "faculty_id": fac,
                          "lecture_hours": int(lh), "practical_hours": int(ph)})
        if valid:
            sb.table("section_subjects").upsert(valid).execute()

    return {"imported": len(valid), "errors": errors}
