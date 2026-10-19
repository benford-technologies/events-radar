"""Speaker pitch email drafts.

generate_missing(event_id, speakers)
    Writes a first-person pitch email from each speaker to the event organiser, for any speaker who
    doesn't have a draft yet. Stored in data/pitches.json. Needs ANTHROPIC_API_KEY.

apply_edit(body, github_user)
    Saves an edited draft from a "[pitch]" issue. Only admins (config/team.yaml → admins) and the
    speaker themself may edit, matched by GitHub username (team.yaml → github).

data/pitches.json:
    {event_id: {speaker: {to, to_name, subject, body, generated_on, edited_by, edited_on}}}
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
PITCHES = ROOT / "data" / "pitches.json"
TEAM = ROOT / "config" / "team.yaml"


def load() -> dict:
    return json.loads(PITCHES.read_text()) if PITCHES.exists() else {}


def save(data: dict) -> None:
    PITCHES.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def team() -> dict:
    return yaml.safe_load(TEAM.read_text()) if TEAM.exists() else {}


def find_event(event_id: str) -> dict:
    for name in ("events.json", "hackathons.json"):
        path = ROOT / "data" / name
        if path.exists():
            for e in json.loads(path.read_text()):
                if e.get("id") == event_id:
                    return e
    return {}


# ---------------------------------------------------------------- generate

DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        "to": {"type": "string", "description": "organiser / speaker-programme email if published, else empty"},
        "to_name": {"type": "string", "description": "contact person's name if known, else empty"},
        "subject": {"type": "string"},
        "body": {"type": "string", "description": "plain-text email body, signed with the speaker's first name"},
    },
    "required": ["to", "to_name", "subject", "body"],
    "additionalProperties": False,
}

DRAFT_PROMPT = """Write a short email in which {speaker} from Benford Technologies pitches themself as a
speaker or panelist at this event. It is sent by {speaker}, in the first person.

About Benford:
{profile}

About {speaker}: {about}

The event:
{event}

{research}
Guidelines:
- 120–180 words, warm and specific, UK English, plain text (no markdown).
- Open with why this event and its audience matter to us, in one sentence.
- Propose one concrete talk or panel angle that fits the audience and {speaker}'s role
  (e.g. what modern finance teams get wrong about audit, automating audit evidence, what founders
  should know before their first statutory audit).
- If there is a call for speakers, refer to it; otherwise ask who runs the programme.
- Don't invent facts about {speaker} beyond what's given. Don't promise anything on Benford's behalf.
- End with a simple ask and sign off with "{speaker}" and "Benford Technologies".
- "to" / "to_name": the organiser or speaker-programme contact, only if it is published."""


def find_contact(event: dict) -> str:
    """Look up the event's speaker / organiser contact on its page. Returns notes (may be empty)."""
    import scan

    if not event.get("url") or event.get("contact_email"):
        return ""
    prompt = (f"Find the speaker-programme or organiser contact (name and email) for this event, and how "
              f"to apply to speak. Fetch {event['url']} first; search only if needed. Reply in 2–4 lines and "
              f"only give contact details that are actually published.\n\nEvent: {event.get('name')}")
    messages = [{"role": "user", "content": prompt}]
    tools = [{"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 3},
             {"type": "web_search_20260209", "name": "web_search", "max_uses": 2}]
    for _ in range(scan.MAX_CONTINUATIONS + 1):
        with scan.client.beta.messages.stream(
            model=scan.MODEL, betas=[scan.FALLBACK_BETA], fallbacks="default", max_tokens=8000,
            tools=tools, output_config={"effort": "low"}, messages=messages,
        ) as stream:
            r = stream.get_final_message()
        if r.stop_reason != "pause_turn":
            return scan.final_text(r) if r.stop_reason != "refusal" else ""
        messages = [messages[0], {"role": "assistant", "content": r.content}]
    return ""


def draft(event: dict, speaker: dict, profile: str) -> dict:
    import scan

    fields = ["name", "organiser", "start_date", "end_date", "city", "country", "venue", "audience",
              "url", "speaker_info", "contact_name", "contact_email", "why_relevant"]
    event_text = "\n".join(f"{k}: {event[k]}" for k in fields if event.get(k))
    about = ", ".join(x for x in [speaker.get("title"), speaker.get("bio")] if x) or "member of the Benford team"
    research = find_contact(event)
    research = f"What we found about the speaker programme / contact:\n{research}\n" if research else ""

    with scan.client.beta.messages.stream(
        model=scan.MODEL, betas=[scan.FALLBACK_BETA], fallbacks="default", max_tokens=8000,
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": DRAFT_SCHEMA}},
        messages=[{"role": "user", "content": DRAFT_PROMPT.format(
            speaker=speaker["name"], profile=profile, about=about, event=event_text, research=research)}],
    ) as stream:
        r = stream.get_final_message()
    if r.stop_reason == "refusal":
        raise RuntimeError("Claude declined to write this draft")
    d = json.loads(scan.final_text(r))
    if not d["to"] and event.get("contact_email"):
        d["to"], d["to_name"] = event["contact_email"], event.get("contact_name", "")
    return d


def generate_missing(event_id: str, speakers: list[str]) -> list[str]:
    data = load()
    have = data.get(event_id, {})
    todo = [s for s in speakers if s not in have]
    if not todo:
        return []
    event = find_event(event_id)
    if not event:
        return []
    people = {p["name"]: p for p in team().get("team", [])}
    profile = (ROOT / "config" / "profile.md").read_text()
    today = dt.date.today().isoformat()
    for name in todo:
        d = draft(event, people.get(name, {"name": name}), profile)
        have[name] = {**d, "generated_on": today}
    data[event_id] = have
    save(data)
    return todo


# ---------------------------------------------------------------- edit

def parse_edit(body: str) -> dict:
    body = body.split("<!--")[0]
    head, _, text = body.partition("\n---\n")
    f = {}
    for line in head.splitlines():
        m = re.match(r"^\s*(id|speaker|to|to_name|subject)\s*:\s*(.*?)\s*$", line)
        if m:
            f[m.group(1)] = m.group(2)
    f["body"] = text.strip()
    return f


def can_edit(github_user: str, speaker: str) -> bool:
    t = team()
    by_github = {(p.get("github") or "").lower(): p["name"] for p in t.get("team", []) if p.get("github")}
    who = by_github.get(github_user.lower())
    return bool(who) and (who in t.get("admins", []) or who == speaker)


def apply_edit(body: str, github_user: str) -> str:
    f = parse_edit(body)
    event_id, speaker = f.get("id", ""), f.get("speaker", "")
    if not re.fullmatch(r"[a-z0-9]{6,20}", event_id) or not speaker or not f["body"]:
        raise ValueError("invalid pitch edit")
    if not can_edit(github_user, speaker):
        raise PermissionError(f"@{github_user} can't edit {speaker}'s pitch. Only admins and {speaker} can "
                              "(GitHub usernames are set in config/team.yaml).")
    data = load()
    d = data.setdefault(event_id, {}).setdefault(speaker, {})
    d.update(
        to=f.get("to", d.get("to", ""))[:200], to_name=f.get("to_name", d.get("to_name", ""))[:120],
        subject=f.get("subject", d.get("subject", ""))[:200], body=f["body"][:5000],
        edited_by=github_user, edited_on=dt.date.today().isoformat(),
    )
    save(data)
    return f"{speaker}'s pitch for {event_id} updated"


if __name__ == "__main__":
    print(generate_missing(sys.argv[1], sys.argv[2].split(",")))
