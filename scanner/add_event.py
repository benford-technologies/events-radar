"""Add an event from a link someone pasted (LinkedIn post, event page, Luma, Eventbrite…).

Issue body (written by the dashboard's "Add event" box):

    url: https://www.linkedin.com/posts/...
    text:
    <optional pasted post text, used when the page can't be read>

Claude reads the link (web fetch), falls back to the pasted text and a web search for the event,
then the scanner's normal extraction turns it into an event, which is merged into the radar and
flagged "interested" for the person who added it.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import apply_triage  # noqa: E402

MAX_TEXT = 6000

ADD_PROMPT = """Someone on the Benford team wants to add this event to our events radar:

URL: {url}

{pasted}
Find out everything about the event this link is about. Fetch the URL first. If the page can't be read
(LinkedIn often requires a login), use the pasted text and search the web for the event's official page.
Then write a plain description with: name, organiser, start and end date, city, country, venue, format,
audience, ticket cost, call for speakers / sponsorship (deadline and link), organiser or speaker-programme
contact (name and email, only if published), the official URL, and the source URLs you used.
If the link is about several events, describe each. If it is not about an event at all, say so."""


def parse(body: str) -> tuple[str, str]:
    body = body.split("<!--")[0]
    m = re.search(r"^\s*url:\s*(\S+)", body, re.M)
    url = m.group(1).strip() if m else ""
    t = re.search(r"^\s*text:\s*\n?(.*)\Z", body, re.M | re.S)
    text = (t.group(1).strip() if t else "")[:MAX_TEXT]
    return url, text


def research(url: str, text: str, profile: str, today: str) -> str:
    import scan  # needs ANTHROPIC_API_KEY; imported here so triage-only runs don't need it

    pasted = f"Pasted text from the person who added it:\n<pasted>\n{text}\n</pasted>\n" if text else ""
    messages = [{"role": "user", "content": ADD_PROMPT.format(url=url, pasted=pasted)}]
    tools = [
        {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 4},
        {"type": "web_search_20260209", "name": "web_search", "max_uses": 4},
    ]
    system = scan.RESEARCH_SYSTEM.format(profile=profile, today=today, feedback="")
    for _ in range(scan.MAX_CONTINUATIONS + 1):
        with scan.client.beta.messages.stream(
            model=scan.MODEL, betas=[scan.FALLBACK_BETA], fallbacks="default",
            max_tokens=16000, system=system, tools=tools,
            output_config={"effort": "medium"}, messages=messages,
        ) as stream:
            response = stream.get_final_message()
        if response.stop_reason == "refusal":
            raise RuntimeError("Claude declined to read this link")
        if response.stop_reason != "pause_turn":
            return scan.final_text(response)
        messages = [messages[0], {"role": "assistant", "content": response.content}]
    raise RuntimeError("reading the link took too long")


def add(body: str, user: str) -> str:
    import scan

    url, text = parse(body)
    if not re.match(r"^https?://", url) and not text:
        raise ValueError("no link or text to add")
    today = dt.date.today().isoformat()
    profile = (scan.CONFIG / "profile.md").read_text()
    uni_names = [u["name"] for u in scan.load_yaml(scan.CONFIG / "universities.yaml")["universities"]]

    notes = research(url, text, profile, today)
    found = scan.extract(notes, profile, today, uni_names)
    if not found:
        raise ValueError("couldn't find an upcoming event at that link")

    names = []
    for e in found:
        kind = "hackathon" if e.get("category") == "hackathon" else "event"
        e["added_by"] = user
        e["added_from"] = url
        if url and url not in e.get("source_urls", []):
            e["source_urls"] = [url] + e.get("source_urls", [])
        path = scan.DATA / ("hackathons.json" if kind == "hackathon" else "events.json")
        merged, _ = scan.merge(scan.load_json(path, []), [e], "added", kind, today)
        scan.write_json(path, merged)
        eid = next((m["id"] for m in merged if m.get("added_from") == url and m.get("name") == e["name"]), scan.event_id(e))
        # someone took the trouble to add it: flag it interested (keeps any existing plan)
        entry = apply_triage.read_entry(eid)
        if entry.get("status") in (None, "", "new", "skip", "maybe"):
            entry.update(status="interested", by=user, on=today)
            apply_triage.write_entry(eid, entry)
        names.append(f"{e['name']} ({e.get('start_date') or 'date TBC'})")
    return "added " + "; ".join(names)


if __name__ == "__main__":
    import os
    print(add(os.environ.get("ISSUE_BODY", ""), os.environ.get("ISSUE_USER", "")))
