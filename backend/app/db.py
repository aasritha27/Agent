"""Supabase access: one service-role client, plus small helpers that map
database rows to the solver's data format."""

from __future__ import annotations

from typing import Any

from supabase import create_client

from .config import SUPABASE_SERVICE_KEY, SUPABASE_URL

_client = None


def client():
    global _client
    if _client is None:
        if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
            raise RuntimeError(
                "SUPABASE_URL and SUPABASE_SERVICE_KEY must be set in the environment"
            )
        _client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    return _client


def get_role(user_id: str) -> str:
    row = client().table("profiles").select("role").eq("id", user_id).single().execute()
    return row.data["role"] if row.data else "viewer"


def coordinator_count() -> int:
    row = (
        client().table("profiles").select("id", count="exact").eq("role", "coordinator").execute()
    )
    return row.count or 0


def set_role(user_id: str, role: str) -> None:
    client().table("profiles").update({"role": role}).eq("id", user_id).execute()


def load_university() -> dict[str, Any]:
    """Everything the solver and the UI need, in one round of queries."""
    sb = client()
    faculty = sb.table("faculty").select("*").order("name").execute().data or []
    rooms = sb.table("rooms").select("*").order("name").execute().data or []
    sections = sb.table("sections").select("*").order("name").execute().data or []
    subjects = sb.table("subjects").select("*").order("code").execute().data or []
    links = (
        sb.table("section_subjects")
        .select("section_id, subject_id, faculty_id, lecture_hours, practical_hours")
        .execute()
        .data
        or []
    )
    return {
        "faculty": faculty,
        "rooms": rooms,
        "sections": sections,
        "subjects": subjects,
        "section_subjects": links,
    }


def solver_inputs(uni: dict[str, Any]) -> tuple:
    """(days, num_slots, sections, rooms, faculty_max, assignments) from DB rows."""
    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
    num_slots = 8
    sections = [
        {"id": s["id"], "name": s["name"], "students": s["students"]} for s in uni["sections"]
    ]
    rooms = [{"id": r["id"], "capacity": r["capacity"], "type": r["type"]} for r in uni["rooms"]]
    faculty_max = {f["id"]: f.get("max_per_day", 4) for f in uni["faculty"]}
    assignments = [
        {
            "section_id": g["section_id"],
            "subject_id": g["subject_id"],
            "faculty_id": g["faculty_id"],
            "lecture_hours": g.get("lecture_hours", 3),
            "practical_hours": g.get("practical_hours", 2),
        }
        for g in uni["section_subjects"]
    ]
    return days, num_slots, sections, rooms, faculty_max, assignments
