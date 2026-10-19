"""Build the static dashboard into _site/.

Combines scanned data, hand-added events (data/manual.yaml) and team decisions
(data/triage.yaml), then writes:
  _site/index.html, app.js, styles.css      the dashboard
  _site/api/events.json                     upcoming + past events (with triage)
  _site/api/hackathons.json                 hackathons pinned to universities
  _site/api/universities.json, runs.json, meta.json
  _site/calendar/team.ics                   events flagged interested / attending
  _site/calendar/radar.ics                  every upcoming event with relevance >= 60

Usage: python scanner/build.py [--demo]   (--demo uses sample data when data/ is empty)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ids import event_id  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA, CONFIG, SITE, OUT = ROOT / "data", ROOT / "config", ROOT / "site", ROOT / "_site"
RADAR_MIN = 60


def load_json(path: Path, default):
    return json.loads(path.read_text()) if path.exists() else default


def load_yaml(path: Path) -> dict:
    return (yaml.safe_load(path.read_text()) or {}) if path.exists() else {}


def ics_escape(s: str) -> str:
    return (s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def ics(events: list[dict], name: str) -> str:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Benford//Events Radar//EN",
             f"X-WR-CALNAME:{ics_escape(name)}", "CALSCALE:GREGORIAN"]
    for e in events:
        if not e.get("start_date"):
            continue
        start = dt.date.fromisoformat(e["start_date"])
        end = dt.date.fromisoformat(e.get("end_date") or e["start_date"]) + dt.timedelta(days=1)
        going = ", ".join(f"{a['name']} ({a.get('role', 'Attendee')})" for a in e.get("attendees") or [])
        pitch = ", ".join(e.get("speakers") or [])
        desc = " · ".join(x for x in [
            f"Going: {going}" if going else "", f"Speaker pitch: {pitch} ({e.get('pitch') or 'to pitch'})" if pitch else "",
            e.get("organiser"), e.get("why_relevant"), e.get("url")] if x)
        lines += [
            "BEGIN:VEVENT",
            f"UID:{e['id']}@benford-events",
            f"DTSTAMP:{stamp}",
            f"DTSTART;VALUE=DATE:{start:%Y%m%d}",
            f"DTEND;VALUE=DATE:{end:%Y%m%d}",
            f"SUMMARY:{ics_escape(e['name'])}",
            f"LOCATION:{ics_escape(', '.join(x for x in [e.get('venue'), e.get('city'), e.get('country')] if x))}",
            f"DESCRIPTION:{ics_escape(desc)}",
        ]
        if e.get("url"):
            lines.append(f"URL:{e['url']}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    args = ap.parse_args()

    events = load_json(DATA / "events.json", [])
    hackathons = load_json(DATA / "hackathons.json", [])
    demo = False
    if args.demo and not events and not hackathons:
        sample = load_json(ROOT / "scanner" / "sample_data.json", {})
        events, hackathons, demo = sample.get("events", []), sample.get("hackathons", []), True

    for m in load_yaml(DATA / "manual.yaml").get("events") or []:
        m = {**m, "source": "manual"}
        m.setdefault("id", event_id(m))
        m.setdefault("kind", "hackathon" if m.get("category") == "hackathon" else "event")
        m.setdefault("relevance", 100)
        (hackathons if m["kind"] == "hackathon" else events).append(m)

    triage = load_yaml(DATA / "triage.yaml")
    # triage.yaml wins; manual.yaml / sample entries may carry their own status and outcome
    for e in events + hackathons:
        t = triage.get(e["id"]) or {}
        e["status"] = t.get("status", e.get("status", "new"))
        e["owner"] = t.get("owner") or t.get("by") or e.get("owner", "")
        e["team_note"] = t.get("note", e.get("notes", ""))
        e["leads"] = t.get("leads", e.get("leads"))
        e["rating"] = t.get("rating", e.get("rating"))
        e["reason"] = t.get("reason", e.get("reason", ""))
        e["attendees"] = t.get("attendees", e.get("attendees", []))
        e["speakers"] = t.get("speakers", e.get("speakers", []))
        e["pitch"] = t.get("pitch", e.get("pitch", ""))

    key = lambda x: (x.get("start_date") or "9999", x.get("name", ""))
    events.sort(key=key)
    hackathons.sort(key=key)

    if OUT.exists():
        shutil.rmtree(OUT)
    shutil.copytree(SITE, OUT)
    (OUT / "api").mkdir()
    (OUT / "calendar").mkdir()

    def dump(name, data):
        (OUT / "api" / name).write_text(json.dumps(data, indent=2, ensure_ascii=False))

    repo = os.environ.get("GITHUB_REPOSITORY", "")
    dump("events.json", events)
    dump("hackathons.json", hackathons)
    dump("universities.json", load_yaml(CONFIG / "universities.yaml").get("universities", []))
    dump("runs.json", load_json(DATA / "runs.json", []))
    team = load_yaml(CONFIG / "team.yaml")
    dump("team.json", {  # names only: the site is public, so no emails
        "team": [{"name": p["name"], "title": p.get("title", ""), "speaker": bool(p.get("speaker"))} for p in team.get("team", [])],
        "roles": team.get("roles", ["Attendee"]),
        "pitch_statuses": team.get("pitch_statuses", []),
        "admins": team.get("admins", []),
    })
    dump("pitches.json", load_json(DATA / "pitches.json", {}))
    dump("meta.json", {
        "built_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "repo": repo,
        "triage_edit_url": f"https://github.com/{repo}/edit/main/data/triage.yaml" if repo else "",
        "manual_edit_url": f"https://github.com/{repo}/edit/main/data/manual.yaml" if repo else "",
        "demo": demo,
    })

    today = dt.date.today().isoformat()
    upcoming = [e for e in events + hackathons if (e.get("end_date") or e.get("start_date") or "9999") >= today]
    team = [e for e in upcoming if e["status"] in ("interested", "attending", "shortlisted")]
    radar = [e for e in upcoming if e.get("relevance", 0) >= RADAR_MIN and e["status"] != "skip"]
    (OUT / "calendar" / "team.ics").write_text(ics(team, "Benford – events we're attending"))
    (OUT / "calendar" / "radar.ics").write_text(ics(radar, "Benford – events radar"))

    print(f"built _site: {len(events)} events, {len(hackathons)} hackathons{' (demo data)' if demo else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
