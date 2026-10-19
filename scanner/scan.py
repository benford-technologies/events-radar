"""Daily event scanner.

For every search group in config/searches.yaml:
  1. research  - Claude searches the web (or Firecrawl searches + scrapes, if FIRECRAWL_API_KEY is set)
  2. extract   - Claude turns the findings into structured events (JSON schema output)
  3. merge     - events are de-duplicated and merged into data/events.json / data/hackathons.json

Usage:
  python scanner/scan.py                 # all groups
  python scanner/scan.py --only cfo-uk   # one or more groups (comma separated)
  python scanner/scan.py --dry-run       # print results, don't write data/

Env:
  ANTHROPIC_API_KEY   required
  FIRECRAWL_API_KEY   optional; switches research to Firecrawl search + scrape
  SCAN_MODEL          optional; defaults to claude-opus-5-5
  SLACK_WEBHOOK_URL   optional; posts a morning digest of new relevant events
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

import anthropic
import requests
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ids import event_id, norm  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"
DATA = ROOT / "data"

MODEL = os.environ.get("SCAN_MODEL", "claude-opus-5-5")
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_CONTINUATIONS = 4
SLACK_MIN_RELEVANCE = 60

CATEGORIES = ["cfo", "audit_accounting", "finance_tech", "founders_investors", "hackathon", "other"]

client = anthropic.Anthropic()


# ---------------------------------------------------------------- config

def load_yaml(path: Path) -> dict:
    with path.open() as f:
        return yaml.safe_load(f) or {}


def load_json(path: Path, default):
    if not path.exists():
        return default
    with path.open() as f:
        return json.load(f)


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


# ---------------------------------------------------------------- team feedback (learning)

LIKED = {"interested", "shortlisted", "attending", "attended"}
MAX_FEEDBACK = 40


def team_feedback(items: list[dict]) -> tuple[str, dict]:
    """Summarise what the team flagged so Claude finds more of what we like and less of what we pass on."""
    triage = load_yaml(DATA / "triage.yaml")
    by_id = {e["id"]: e for e in items}

    def line(eid: str, t: dict) -> str:
        e = by_id.get(eid, {})
        parts = [e.get("name", eid), e.get("organiser"), e.get("city"), e.get("category")]
        extra = [f"reason: {t['reason']}" if t.get("reason") else "",
                 f"rated {t['rating']}/5" if t.get("rating") else "",
                 f"{t['leads']} leads" if t.get("leads") else "",
                 f"note: {t['note']}" if t.get("note") else ""]
        extra = "; ".join(x for x in extra if x)
        return "- " + " — ".join(str(p) for p in parts if p) + (f" [{extra}]" if extra else "")

    recent = sorted(triage.items(), key=lambda kv: str((kv[1] or {}).get("on", "")), reverse=True)
    liked = [line(k, v) for k, v in recent if (v or {}).get("status") in LIKED][:MAX_FEEDBACK]
    passed = [line(k, v) for k, v in recent if (v or {}).get("status") == "skip"][:MAX_FEEDBACK]
    missed = [line(k, v) for k, v in recent if (v or {}).get("status") == "missed"][:10]
    if not (liked or passed or missed):
        return "", {"liked": 0, "passed": 0}

    text = "## What the team has told us (learn from this)\n"
    if liked:
        text += "\nThe team flagged these as interesting or went to them. Find more events like these and score similar ones higher:\n" + "\n".join(liked) + "\n"
    if passed:
        text += ("\nThe team passed on these. Don't list them again, and score events that are similar"
                 " (same organiser, format, size or the stated reason) lower:\n" + "\n".join(passed) + "\n")
    if missed:
        text += "\nWe wanted to go to these but missed them, so flag their next editions early:\n" + "\n".join(missed) + "\n"
    return text, {"liked": len(liked), "passed": len(passed)}


# ---------------------------------------------------------------- Claude helpers

def final_text(response) -> str:
    return "\n".join(b.text for b in response.content if b.type == "text").strip()


# ---------------------------------------------------------------- 1. research

RESEARCH_SYSTEM = """You research upcoming events for Benford Technologies' marketing team.

{profile}

{feedback}
Today is {today}. Only include events that start on or after today and within the next 12 months.
Prefer official event pages over aggregators. Never invent an event, date or URL: if you can't
confirm a detail, say it is unconfirmed."""

RESEARCH_PROMPT = """Find upcoming events matching this brief:

{brief}

Search broadly (several queries, both English and Norwegian where relevant), then write a
plain list. For each event give: name, organiser, start and end date, city, country, venue,
format (in person / online / hybrid), audience, ticket cost, whether there is a call for speakers
or sponsorship option (with deadline and link), the organiser or speaker-programme contact (name and
email, only if published), the official URL, and the source URLs you used.
{kind_note}
Aim for completeness: list every relevant event you find, up to about 25."""

HACKATHON_NOTE = (
    "These are hackathons: for each one also name the university (or student society and its "
    "university) that runs or hosts it."
)


def research_with_claude(group: dict, profile: str, today: str, feedback: str = "") -> str:
    messages = [{
        "role": "user",
        "content": RESEARCH_PROMPT.format(
            brief=group["brief"].strip(),
            kind_note=HACKATHON_NOTE if group["kind"] == "hackathon" else "",
        ),
    }]
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": 8}]
    system = RESEARCH_SYSTEM.format(profile=profile, today=today, feedback=feedback)

    for _ in range(MAX_CONTINUATIONS + 1):
        with client.beta.messages.stream(
            model=MODEL,
            betas=[FALLBACK_BETA],
            fallbacks="default",
            max_tokens=32000,
            system=system,
            tools=tools,
            output_config={"effort": "medium"},
            messages=messages,
        ) as stream:
            response = stream.get_final_message()
        if response.stop_reason == "refusal":
            raise RuntimeError(f"research refused: {response.stop_details}")
        if response.stop_reason != "pause_turn":
            return final_text(response)
        # Server-side search loop paused; send the turn back and it resumes.
        messages = [messages[0], {"role": "assistant", "content": response.content}]
    raise RuntimeError("research still paused after max continuations")


def research_with_firecrawl(group: dict) -> str:
    key = os.environ["FIRECRAWL_API_KEY"]
    chunks = []
    for query in group.get("queries", []):
        r = requests.post(
            "https://api.firecrawl.dev/v1/search",
            headers={"Authorization": f"Bearer {key}"},
            json={"query": query, "limit": 5, "scrapeOptions": {"formats": ["markdown"]}},
            timeout=120,
        )
        r.raise_for_status()
        for item in r.json().get("data", []):
            body = (item.get("markdown") or item.get("description") or "")[:6000]
            chunks.append(f"### {item.get('title', '')}\nURL: {item.get('url', '')}\n\n{body}")
    return "\n\n---\n\n".join(chunks)


# ---------------------------------------------------------------- 2. extract

def event_schema(university_names: list[str]) -> dict:
    s = {"type": "string"}
    event = {
        "type": "object",
        "properties": {
            "name": s,
            "organiser": s,
            "start_date": {"type": "string", "description": "YYYY-MM-DD, or empty if unknown"},
            "end_date": {"type": "string", "description": "YYYY-MM-DD, or empty if unknown"},
            "date_confirmed": {"type": "boolean"},
            "city": s,
            "country": s,
            "venue": s,
            "format": {"type": "string", "enum": ["in_person", "online", "hybrid", "unknown"]},
            "category": {"type": "string", "enum": CATEGORIES},
            "audience": s,
            "cost": s,
            "url": {"type": "string", "description": "official event URL"},
            "source_urls": {"type": "array", "items": s},
            "speaker_opportunity": {"type": "boolean"},
            "speaker_info": {"type": "string", "description": "CFP link / deadline / sponsor info, or empty"},
            "contact_name": {"type": "string", "description": "organiser / speaker-programme contact person, or empty"},
            "contact_email": {"type": "string", "description": "contact email exactly as published, or empty"},
            "relevance": {"type": "integer", "description": "0-100 fit for Benford"},
            "why_relevant": {"type": "string", "description": "one sentence"},
            "university": {"type": "string", "enum": university_names + ["Other", "None"]},
        },
        "required": [
            "name", "organiser", "start_date", "end_date", "date_confirmed", "city", "country",
            "venue", "format", "category", "audience", "cost", "url", "source_urls",
            "speaker_opportunity", "speaker_info", "contact_name", "contact_email", "relevance", "why_relevant", "university",
        ],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {"events": {"type": "array", "items": event}},
        "required": ["events"],
        "additionalProperties": False,
    }


EXTRACT_PROMPT = """Turn these research notes into structured events for Benford Technologies.

{profile}

{feedback}
Today is {today}. Rules:
- Only events starting on or after today. Drop anything past, undated beyond 12 months, or not a real event.
- Use only facts present in the notes. Unknown text fields are empty strings; never guess URLs.
- relevance: 0-100 fit for Benford using the profile and the team's feedback above. why_relevant: one short sentence.
- university: for hackathons, the matching university from the allowed list ("Other" if it is a
  university not on the list, "None" if no university is involved). For non-hackathons use "None".
- category for hackathons is always "hackathon".

<notes>
{notes}
</notes>"""


def extract(notes: str, profile: str, today: str, university_names: list[str], feedback: str = "") -> list[dict]:
    if not notes.strip():
        return []
    with client.beta.messages.stream(
        model=MODEL,
        betas=[FALLBACK_BETA],
        fallbacks="default",
        max_tokens=32000,
        output_config={
            "effort": "low",
            "format": {"type": "json_schema", "schema": event_schema(university_names)},
        },
        messages=[{
            "role": "user",
            "content": EXTRACT_PROMPT.format(profile=profile, today=today, notes=notes, feedback=feedback),
        }],
    ) as stream:
        response = stream.get_final_message()
    if response.stop_reason == "refusal":
        raise RuntimeError(f"extract refused: {response.stop_details}")
    return json.loads(final_text(response))["events"]


# ---------------------------------------------------------------- 3. merge

def merge(existing: list[dict], found: list[dict], group_id: str, kind: str, today: str) -> tuple[list[dict], list[dict]]:
    by_id = {e["id"]: e for e in existing}
    by_url = {norm(e.get("url")): e["id"] for e in existing if e.get("url")}
    new = []
    for e in found:
        eid = by_url.get(norm(e.get("url"))) if e.get("url") else None
        eid = eid or event_id(e)
        if eid in by_id:
            old = by_id[eid]
            sources = sorted(set(old.get("source_urls", [])) | set(e.get("source_urls", [])))
            groups = sorted(set(old.get("groups", [])) | {group_id})
            # keep earlier non-empty values when the new scan didn't find them
            for k, v in e.items():
                if v not in ("", None, []) or k not in old:
                    old[k] = v
            old.update(source_urls=sources, groups=groups, last_seen=today)
        else:
            e.update(id=eid, kind=kind, groups=[group_id], first_seen=today, last_seen=today)
            by_id[eid] = e
            if e.get("url"):
                by_url[norm(e["url"])] = eid
            new.append(e)
    merged = sorted(by_id.values(), key=lambda x: (x.get("start_date") or "9999", x.get("name", "")))
    return merged, new


# ---------------------------------------------------------------- Slack digest

def post_slack(new_events: list[dict], site_url: str) -> None:
    url = os.environ.get("SLACK_WEBHOOK_URL")
    picks = [e for e in new_events if e.get("relevance", 0) >= SLACK_MIN_RELEVANCE]
    if not url or not picks:
        return
    picks.sort(key=lambda e: -e.get("relevance", 0))
    lines = [f"*{len(picks)} new events found this morning*" + (f" · <{site_url}|open dashboard>" if site_url else "")]
    for e in picks[:15]:
        when = e.get("start_date") or "date TBC"
        where = ", ".join(x for x in [e.get("city"), e.get("country")] if x)
        mic = " :microphone: speaker slot" if e.get("speaker_opportunity") else ""
        lines.append(f"• <{e.get('url') or '#'}|{e['name']}> — {when}, {where} ({e.get('relevance')}){mic}")
    requests.post(url, json={"text": "\n".join(lines)}, timeout=30)


# ---------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="comma-separated group ids")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    today = dt.date.today().isoformat()
    profile = (CONFIG / "profile.md").read_text()
    groups = load_yaml(CONFIG / "searches.yaml")["groups"]
    universities = load_yaml(CONFIG / "universities.yaml")["universities"]
    uni_names = [u["name"] for u in universities]
    if args.only:
        wanted = set(args.only.split(","))
        groups = [g for g in groups if g["id"] in wanted]

    use_firecrawl = bool(os.environ.get("FIRECRAWL_API_KEY"))
    stores = {
        "event": load_json(DATA / "events.json", []),
        "hackathon": load_json(DATA / "hackathons.json", []),
    }
    all_new, errors = [], []
    feedback, feedback_stats = team_feedback(stores["event"] + stores["hackathon"])
    print(f"team feedback: {feedback_stats['liked']} liked, {feedback_stats['passed']} passed", flush=True)

    for g in groups:
        print(f"→ {g['id']} ({'firecrawl' if use_firecrawl else 'claude web search'})", flush=True)
        try:
            notes = research_with_firecrawl(g) if use_firecrawl else research_with_claude(g, profile, today, feedback)
            found = extract(notes, profile, today, uni_names, feedback)
        except Exception as exc:  # one failing group shouldn't sink the whole morning scan
            print(f"  ! {g['id']} failed: {exc}", file=sys.stderr)
            errors.append({"group": g["id"], "error": str(exc)[:500]})
            continue
        for e in found:
            if g["kind"] == "hackathon":
                e["category"] = "hackathon"
        stores[g["kind"]], new = merge(stores[g["kind"]], found, g["id"], g["kind"], today)
        all_new += new
        print(f"  {len(found)} found, {len(new)} new", flush=True)
        if args.dry_run:
            print(json.dumps(found, indent=2, ensure_ascii=False))

    if args.dry_run:
        return 1 if errors and len(errors) == len(groups) else 0

    write_json(DATA / "events.json", stores["event"])
    write_json(DATA / "hackathons.json", stores["hackathon"])
    runs = load_json(DATA / "runs.json", [])
    runs.insert(0, {
        "date": today,
        "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "groups": [g["id"] for g in groups],
        "new": len(all_new),
        "source": "firecrawl" if use_firecrawl else "claude_web_search",
        "model": MODEL,
        "feedback": feedback_stats,
        "errors": errors,
    })
    write_json(DATA / "runs.json", runs[:60])

    post_slack(all_new, os.environ.get("SITE_URL", ""))
    print(f"done: {len(all_new)} new, {len(errors)} group errors")
    return 1 if errors and len(errors) == len(groups) else 0


if __name__ == "__main__":
    sys.exit(main())
