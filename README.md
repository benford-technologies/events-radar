# Benford Events Radar

Every morning a GitHub Action searches the web for events Benford should attend: CFO and finance-leader
events, audit and accounting conferences, fintech, founder and investor events, open calls for speakers,
and university hackathons in London and Norway. It scores each event for fit, stores the results in
this repo and publishes a dashboard on GitHub Pages. The dashboard has four tabs: **Events** (what's coming up),
**Hackathons** (by university), **Track record** (where we went and where we didn't) and **Scan log**.

```
config/        profile.md (who we are), searches.yaml (search groups), universities.yaml, team.yaml (people, roles, speakers)
scanner/       scan.py (daily research + extraction), build.py (site), bot.py (handles dashboard buttons),
               add_event.py, pitch.py, apply_triage.py, ids.py
data/          output, committed by the bot: events.json, hackathons.json, runs.json, pitches.json
               edited by the team: triage.yaml (decisions), manual.yaml (hand-added events)
site/          the dashboard (static HTML/JS, no build step)
.github/       daily-scan.yml: cron 05:30 UTC → scan → commit → publish to Pages
```

## How a scan works

For each group in `config/searches.yaml`:
1. **Research.** Claude (`claude-opus-5-5`) runs its own web searches from the group's brief.
   If `FIRECRAWL_API_KEY` is set, Firecrawl searches and scrapes the group's `queries` instead.
2. **Extract.** Claude turns the findings into structured events (JSON schema): dates, city, organiser,
   URL, sources, speaker/sponsor opportunity, a fit score from 0 to 100 and a one-line reason.
3. **Merge.** Events are de-duplicated by URL, or by name and year, so repeat finds update the existing entry.
   `first_seen` drives the "New" badge.

LinkedIn: the scanner can only find **public** `linkedin.com/events` pages that appear in search results.
It does not log in to LinkedIn or scrape it.

## Deploy (about 10 minutes)

1. Create a GitHub repo, e.g. `benford-tech/events-radar`, and push this folder:
   ```bash
   git remote add origin git@github.com:<org>/events-radar.git && git push -u origin main
   ```
2. **Settings → Secrets and variables → Actions**
   - Secret `ANTHROPIC_API_KEY` (required)
   - Secret `FIRECRAWL_API_KEY` (optional)
   - Secret `SLACK_WEBHOOK_URL` (optional; posts a morning digest of new events with fit ≥ 60)
   - Variable `SITE_URL` (optional; the Pages URL, used as the link in Slack posts)
   - Variable `SCAN_MODEL` (optional; e.g. `claude-sonnet-5-5` for roughly half the cost)
3. **Settings → Pages → Source: GitHub Actions.** On a private repo this requires a paid GitHub plan
   (Team/Enterprise), and you can restrict the site to org members.
4. **Actions → Daily scan + publish → Run workflow** to run the first scan now instead of waiting for tomorrow.

## Using it

- **Flag events.** Every card has **Interested** and **Pass** buttons. Pass asks why: wrong audience, too small,
  too expensive, wrong location, not our topic, date clash or other. Past events have **We went** and **Didn't go**.
  - The page updates straight away in your browser, then opens a pre-filled GitHub issue. Click **Create** to save the flag for the team.
  - The `Triage flag` workflow writes it to `data/triage.yaml`, closes the issue and republishes in about a minute.
  - Only the repo owner, collaborators and org members can save flags, so add teammates under Settings → Collaborators.
    They need GitHub accounts.
- **Plan who goes.** **Plan** on a card opens a panel:
  - Pick one or more people from `config/team.yaml`, each with a role: Attendee, Speaker, Panelist, Booth / sponsor,
    Judge, Mentor or Recruiting.
  - Pick who we're pitching as speaker (John, Niosha, Andreas, Mads or Thibault) and where the pitch stands:
    to pitch, pitched, accepted or declined.
  - Saving marks the event as attending. Who's going and the speaker pitch show on the card and in the calendar feed.
  - The **Anyone** filter shows everything one person is going to or pitched for.
  - To change the team or the speaker pool, edit `config/team.yaml`. Only names, titles and roles are published, never emails.
- **Add an event from a link.** Click **Add event** and paste a link: a LinkedIn post, event page, Luma or Eventbrite.
  For LinkedIn, also paste the post text, because LinkedIn often hides posts from bots. The bot reads the link,
  falls back to the pasted text and a web search, adds the event flagged as interested, and republishes in about two minutes.
  If it can't find an event, it closes the issue and says why.
- **Speaker pitch emails.** When a plan pitches someone as speaker, the bot drafts a short first-person email from
  them to the organiser, and looks up the speaker-programme contact if it's published. Open it with **Pitch emails** on the card.
  - **Open in Gmail** opens the draft in the sender's own Gmail to review and send. Nothing is sent automatically.
  - **Save changes for the team** saves edits, but only for admins (`admins` in `config/team.yaml`) and that speaker.
    People are matched by their GitHub username (`github:` in `team.yaml`).
  - Add a `bio:` line per speaker in `team.yaml` to make the drafts more specific.
- **Passed events disappear** from Events and Hackathons for everyone. The Track record tab still lists them under "We didn't".
- **It learns.** Each morning the scanner reads `triage.yaml`. Events marked interested or attended become examples
  of "more like this". Passes, with their reasons, become "avoid these and anything similar". Both feed into the
  search and the fit scores. The Scan log shows how many flags each run learned from.
- **After an event**, mark it **We went** and add leads and a 1–5 rating in the issue note or in `triage.yaml`,
  or mark it **Didn't go**. Past events still flagged interested show under Track record → **Did we go?**.
- **Add an event by hand:** add it to `data/manual.yaml` (the **+ Add event** link opens it).
- **Change what it looks for:** edit `config/searches.yaml` or `config/profile.md`.
- **Calendars:** subscribe to `…/calendar/team.ics` (attending + shortlisted) or `…/calendar/radar.ics`
  (everything with fit ≥ 60) in Google Calendar or Outlook.

## Integrations

Everything the dashboard shows is plain JSON. Other tools (Notion, HubSpot, Zapier/Make, Slack bots, the
UNCL MCP) can read it from:

| URL | Contents |
|---|---|
| `/api/events.json` | all events with triage status, owner, note |
| `/api/hackathons.json` | hackathons with their university |
| `/api/universities.json` | tracked universities |
| `/api/runs.json` | scan log (date, new count, errors) |
| `/calendar/*.ics` | calendar feeds |

To push data out (e.g. create a Notion page or CRM task for each new event), add a step to `scanner/scan.py`
next to `post_slack()`. The `all_new` list contains exactly what that morning's scan added.

## Run locally

```bash
python3 -m venv .venv && .venv/bin/pip install -r scanner/requirements.txt
export ANTHROPIC_API_KEY=...
.venv/bin/python scanner/scan.py --only cfo-uk --dry-run   # try one group, writes nothing
.venv/bin/python scanner/scan.py                           # full scan into data/
.venv/bin/python scanner/build.py --demo                   # build _site/ (demo data if data/ is empty)
python3 -m http.server 8765 --directory _site
```

## Cost

The full run covers 10 groups. Each group uses up to 8 web searches plus two Claude calls. Expect very roughly
$2–4 per day on Opus 5.5 ($10 per 1,000 searches plus tokens). Setting `SCAN_MODEL=claude-sonnet-5-5` or removing
groups lowers this. Check real spend in the Anthropic Console after the first few runs.

## Not built yet

- Speaker outreach for John (Gmail drafts per shortlisted event). It was deliberately left for later.
- Triage from inside the dashboard. Today it goes through `triage.yaml`. If the team wants one-click
  buttons, the next step is a small database (Supabase or Notion) behind the page.
