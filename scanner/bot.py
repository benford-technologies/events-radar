"""Issue bot: everything the dashboard's buttons send arrives as a GitHub issue.

  [triage] …  flags and plans (Interested / Pass / We went / Plan)  → data/triage.yaml
              + writes speaker pitch drafts for newly pitched speakers → data/pitches.json
  [add] …     add an event from a pasted link                        → data/events.json / hackathons.json
  [pitch] …   edit a speaker pitch draft (admins + that speaker only)  → data/pitches.json

Env: ISSUE_TITLE, ISSUE_BODY, ISSUE_USER, ANTHROPIC_API_KEY (for [add] and drafts).
Prints a one-line summary. Exit 1 with the reason on stdout if the request is rejected.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import apply_triage  # noqa: E402
import pitch  # noqa: E402


def main() -> int:
    title = os.environ.get("ISSUE_TITLE", "")
    body = os.environ.get("ISSUE_BODY", "")
    user = os.environ.get("ISSUE_USER", "")
    has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    try:
        if title.startswith("[triage]"):
            summary, event_id, entry = apply_triage.apply(body, user)
            if entry.get("speakers") and entry.get("status") != "new":
                if has_key:
                    try:
                        done = pitch.generate_missing(event_id, entry["speakers"])
                        if done:
                            summary += f"; pitch drafts written for {', '.join(done)}"
                    except Exception as exc:  # the flag itself is saved; drafts can be retried
                        summary += f"; pitch drafts failed ({str(exc)[:120]})"
                else:
                    summary += "; pitch drafts skipped (no ANTHROPIC_API_KEY)"
        elif title.startswith("[add]"):
            if not has_key:
                raise ValueError("adding events needs the ANTHROPIC_API_KEY secret")
            import add_event
            summary = add_event.add(body, user)
        elif title.startswith("[pitch]"):
            summary = pitch.apply_edit(body, user)
        else:
            raise ValueError("unknown request")
    except (ValueError, PermissionError) as exc:
        print(str(exc).replace("\n", " "))
        return 1
    print(summary.replace("\n", " "))
    return 0


if __name__ == "__main__":
    sys.exit(main())
