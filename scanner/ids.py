"""Stable event ids, shared by the scanner and the site build."""
from __future__ import annotations

import hashlib
import re


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def event_id(e: dict) -> str:
    """Same event name in the same year -> same id, so re-finds merge and triage sticks."""
    year = (str(e.get("start_date") or ""))[:4]
    name = re.sub(r"\b20\d\d\b", "", norm(e.get("name", "")))
    return hashlib.sha1(f"{name}|{year}".encode()).hexdigest()[:10]
