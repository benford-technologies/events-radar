"""Apply a triage flag from a GitHub issue to data/triage.yaml.

The dashboard's Interested / Pass / We went / Didn't go buttons open a pre-filled issue whose
body looks like:

    id: 3f9a1c2b7d
    status: skip
    reason: Too small
    note: optional free text

Env: ISSUE_BODY, ISSUE_USER. Prints a one-line summary; exits 1 if the body is invalid.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
TRIAGE = ROOT / "data" / "triage.yaml"
TEAM = ROOT / "config" / "team.yaml"
STATUSES = {"new", "interested", "shortlisted", "attending", "maybe", "skip", "attended", "missed"}


def parse(body: str) -> dict:
    fields = {}
    for line in body.splitlines():
        if line.strip().startswith("<!--"):
            break
        m = re.match(r"^\s*(id|status|reason|note|leads|rating|owner|attendees|speakers|pitch)\s*:\s*(.*?)\s*$", line, re.I)
        if m:
            fields[m.group(1).lower()] = m.group(2)
    return fields


def parse_plan(f: dict, entry: dict) -> None:
    """attendees: "John (Speaker), Niosha (Attendee)" · speakers: "John, Niosha" · pitch: "Pitched".
    Only names from config/team.yaml are kept; a key that is present but empty clears it."""
    team = yaml.safe_load(TEAM.read_text()) if TEAM.exists() else {}
    names = {p["name"] for p in team.get("team", [])}
    speakers_ok = {p["name"] for p in team.get("team", []) if p.get("speaker")}
    roles = set(team.get("roles", ["Attendee"]))
    if "attendees" in f:
        people = []
        for m in re.finditer(r"([^,()]+?)\s*(?:\(([^)]*)\))?\s*(?:,|$)", f["attendees"]):
            name, role = m.group(1).strip(), (m.group(2) or "Attendee").strip()
            if name in names and name not in [p["name"] for p in people]:
                people.append({"name": name, "role": role if role in roles else "Attendee"})
        entry["attendees"] = people
    if "speakers" in f:
        entry["speakers"] = [n for n in dict.fromkeys(x.strip() for x in f["speakers"].split(",")) if n in speakers_ok]
    if "pitch" in f:
        entry["pitch"] = f["pitch"] if f["pitch"] in team.get("pitch_statuses", []) else ""
    for key in ("attendees", "speakers", "pitch"):
        if key in entry and not entry[key]:
            del entry[key]


def find_name(event_id: str) -> str:
    for name in ("events.json", "hackathons.json"):
        path = ROOT / "data" / name
        if path.exists():
            for e in json.loads(path.read_text()):
                if e.get("id") == event_id:
                    return e.get("name", "")
    return ""


def write_entry(event_id: str, entry: dict | None) -> None:
    """Replace this id's line in triage.yaml (None removes it); every other line and comment stays as it was."""
    text = TRIAGE.read_text() if TRIAGE.exists() else ""
    lines = [l for l in text.splitlines() if not re.match(rf"^{re.escape(event_id)}\s*:", l)]
    if entry is not None:
        name = find_name(event_id).replace("\n", " ").replace("#", "")
        lines.append(f"{event_id}: {json.dumps(entry, ensure_ascii=False)}" + (f"  # {name}" if name else ""))
    TRIAGE.write_text("\n".join(lines).rstrip() + "\n")


def read_entry(event_id: str) -> dict:
    text = TRIAGE.read_text() if TRIAGE.exists() else ""
    return dict((yaml.safe_load(text) or {}).get(event_id) or {})


def apply(body: str, user: str) -> tuple[str, str, dict]:
    """Apply one triage issue. Returns (summary, event id, entry). Raises ValueError on a bad body."""
    f = parse(body)
    event_id, status = f.get("id", ""), f.get("status", "").lower()
    if not re.fullmatch(r"[a-z0-9]{6,20}", event_id) or status not in STATUSES:
        raise ValueError(f"invalid triage issue: id={event_id!r} status={status!r}")

    entry = read_entry(event_id)
    entry["status"] = status
    for key, limit in (("reason", 80), ("note", 500), ("owner", 60)):
        if f.get(key):
            entry[key] = f[key][:limit]
    if status != "skip":
        entry.pop("reason", None)
    for key in ("leads", "rating"):
        if (f.get(key) or "").isdigit():
            entry[key] = int(f[key])
    parse_plan(f, entry)
    entry["by"] = user[:40]
    entry["on"] = dt.date.today().isoformat()

    write_entry(event_id, entry if status != "new" else None)
    going = ", ".join(a["name"] for a in entry.get("attendees", []))
    summary = (f"{event_id} → {status}" + (f" ({entry['reason']})" if entry.get("reason") else "")
               + (f", going: {going}" if going else "")
               + (f", speaker pitch: {', '.join(entry['speakers'])}" if entry.get("speakers") else ""))
    return summary, event_id, entry


def main() -> int:
    try:
        summary, _, _ = apply(os.environ.get("ISSUE_BODY", ""), os.environ.get("ISSUE_USER", ""))
    except ValueError as exc:
        print(exc)
        return 1
    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
