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
STATUSES = {"new", "interested", "shortlisted", "attending", "maybe", "skip", "attended", "missed"}


def parse(body: str) -> dict:
    fields = {}
    for line in body.splitlines():
        if line.strip().startswith("<!--"):
            break
        m = re.match(r"^\s*(id|status|reason|note|leads|rating|owner)\s*:\s*(.*?)\s*$", line, re.I)
        if m:
            fields[m.group(1).lower()] = m.group(2)
    return fields


def find_name(event_id: str) -> str:
    for name in ("events.json", "hackathons.json"):
        path = ROOT / "data" / name
        if path.exists():
            for e in json.loads(path.read_text()):
                if e.get("id") == event_id:
                    return e.get("name", "")
    return ""


def main() -> int:
    f = parse(os.environ.get("ISSUE_BODY", ""))
    event_id, status = f.get("id", ""), f.get("status", "").lower()
    if not re.fullmatch(r"[a-z0-9]{6,20}", event_id) or status not in STATUSES:
        print(f"invalid triage issue: id={event_id!r} status={status!r}")
        return 1

    text = TRIAGE.read_text() if TRIAGE.exists() else ""
    existing = (yaml.safe_load(text) or {}).get(event_id) or {}

    entry = dict(existing)
    entry["status"] = status
    for key, limit in (("reason", 80), ("note", 500), ("owner", 60)):
        if f.get(key):
            entry[key] = f[key][:limit]
    if status != "skip":
        entry.pop("reason", None)
    for key in ("leads", "rating"):
        if (f.get(key) or "").isdigit():
            entry[key] = int(f[key])
    entry["by"] = os.environ.get("ISSUE_USER", "")[:40]
    entry["on"] = dt.date.today().isoformat()

    # Replace this id's line, keep every other line (and comments) as they were.
    lines = [l for l in text.splitlines() if not re.match(rf"^{re.escape(event_id)}\s*:", l)]
    if status != "new":
        name = find_name(event_id).replace("\n", " ").replace("#", "")
        lines.append(f"{event_id}: {json.dumps(entry, ensure_ascii=False)}" + (f"  # {name}" if name else ""))
    TRIAGE.write_text("\n".join(lines).rstrip() + "\n")
    print(f"{event_id} → {status}" + (f" ({entry['reason']})" if entry.get("reason") else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
