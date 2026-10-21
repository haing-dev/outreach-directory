#!/usr/bin/env python3
"""
Weekly refresh for the Outreach Directory.

For every entry in config/seeds.yaml, asks Claude (Sonnet, medium effort, with web search)
to research the lab / institute / company and return structured JSON. Validates links,
compares with last week's data, and writes:

  public/data/directory.json   - all entries (what the site shows)
  public/data/changes.json     - weekly change log + suggested additions

Your notes are NOT here - they live in Cloudflare D1 and are never touched by this script.

Usage:
  python scripts/refresh.py                  # refresh everything
  python scripts/refresh.py --only stanford-curtis,st-chai
  python scripts/refresh.py --category startup
  python scripts/refresh.py --skeleton       # no API calls: build empty entries from seeds
  python scripts/refresh.py --discover       # also suggest new labs per university
  python scripts/refresh.py --stale-days 6   # skip entries refreshed in the last N days

Env:
  CLAUDE_TOKEN         your Claude API key or internal token (auth style auto-detected)
  CLAUDE_BASE_URL      default https://api.anthropic.com (set for an internal gateway)
  CLAUDE_MODEL         default claude-sonnet-5-5
  CLAUDE_EFFORT        default medium
  MAX_SEARCHES         web searches allowed per entry, default 8
  WORKERS              parallel requests, default 4
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import json
import os
import re
import sys
import time
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
SEEDS = ROOT / "config" / "seeds.yaml"
PROFILE = ROOT / "config" / "profile.yaml"
DATA_DIR = ROOT / "public" / "data"
DIRECTORY = DATA_DIR / "directory.json"
CHANGES = DATA_DIR / "changes.json"

def _env(*names, default=""):
    for n in names:
        v = os.environ.get(n, "").strip()
        if v:
            return v
    return default


BASE_URL = _env("CLAUDE_BASE_URL", "ANTHROPIC_BASE_URL", default="https://api.anthropic.com").rstrip("/")
MODEL = _env("CLAUDE_MODEL", default="claude-sonnet-5-5")
EFFORT = _env("CLAUDE_EFFORT", default="medium")
MAX_SEARCHES = int(_env("MAX_SEARCHES", default="8"))
WORKERS = int(_env("WORKERS", default="4"))
WEB_SEARCH_TOOL = _env("WEB_SEARCH_TOOL", default="web_search_20260318")
TOKEN = _env("CLAUDE_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
# Standard keys go in x-api-key; many internal gateways want "Authorization: Bearer".
# We try x-api-key first and switch automatically on a 401.
AUTH = {"mode": "bearer" if _env("ANTHROPIC_AUTH_TOKEN") and not _env("CLAUDE_TOKEN", "ANTHROPIC_API_KEY") else "x-api-key"}

CATEGORY_LABEL = {
    "academic": "Academic lab",
    "institute": "Independent institute",
    "bigtech": "Big tech AI-bio team",
    "biotech": "Biotech / pharma",
    "startup": "AI-bio startup",
}
HAS_CONTACTS = {"academic", "institute"}
PROGRAM_STATUSES = {"open", "opens_soon", "rolling", "closed", "unknown"}

TODAY = dt.date.today()


# ----------------------------------------------------------------------------- seeds

def load_yaml(path: Path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_seeds() -> list[dict]:
    raw = load_yaml(SEEDS)
    out: list[dict] = []
    for region, unis in (raw.get("academic") or {}).items():
        for inst, labs in (unis or {}).items():
            for lab in labs or []:
                # "Notable PIs" group: show the hint (their actual institution) instead of the group name
                shown = lab.get("hint") if region == "Other" and lab.get("hint") else inst
                out.append({**lab, "category": "academic", "region": region, "institution": shown})
    for region, items in (raw.get("institute") or {}).items():
        for i, it in enumerate(items or []):
            out.append({**it, "category": "institute", "region": region, "institution": it["name"], "rank": i + 1})
    for cat in ("bigtech", "biotech", "startup"):
        for i, it in enumerate(raw.get(cat) or []):
            out.append({**it, "category": cat, "region": it.get("region", ""), "institution": it["name"], "rank": i + 1})
    seen = set()
    for s in out:
        if s["id"] in seen:
            sys.exit(f"Duplicate id in seeds.yaml: {s['id']}")
        seen.add(s["id"])
    return [s for s in out if s.get("enabled", True)]


# ----------------------------------------------------------------------------- Claude

def _headers() -> dict:
    if not TOKEN:
        sys.exit("Missing Claude token: add the CLAUDE_TOKEN secret in GitHub.")
    h = {"content-type": "application/json", "anthropic-version": "2023-06-01"}
    if AUTH["mode"] == "bearer":
        h["authorization"] = f"Bearer {TOKEN}"
    else:
        h["x-api-key"] = TOKEN
    return h


def call_claude(system: str, user: str, max_searches: int = MAX_SEARCHES, max_tokens: int = 8000) -> tuple[str, list[dict]]:
    """Run one research turn with web search. Returns (final text, cited sources)."""
    messages: list[dict] = [{"role": "user", "content": user}]
    body = {
        "model": MODEL,
        "max_tokens": max_tokens,
        "system": system,
        "output_config": {"effort": EFFORT},
        "tools": [{"type": WEB_SEARCH_TOOL, "name": "web_search", "max_uses": max_searches}],
    }
    texts: list[str] = []
    cites: dict[str, str] = {}
    for _hop in range(6):  # pause_turn continuations
        resp = None
        for attempt in range(5):
            try:
                resp = requests.post(f"{BASE_URL}/v1/messages", headers=_headers(),
                                     json={**body, "messages": messages}, timeout=600)
            except requests.RequestException as e:
                print(f"  network error ({e}); retrying", file=sys.stderr)
                time.sleep(10 * (attempt + 1))
                continue
            if resp.status_code in (401, 403) and AUTH["mode"] == "x-api-key" and not AUTH.get("tried_bearer"):
                AUTH["mode"], AUTH["tried_bearer"] = "bearer", True
                print("  token rejected as x-api-key; switching to Authorization: Bearer", file=sys.stderr)
                continue
            if resp.status_code in (429, 500, 502, 503, 529):
                wait = int(resp.headers.get("retry-after", 20 * (attempt + 1)))
                print(f"  API {resp.status_code}; waiting {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            break
        if resp is None or resp.status_code != 200:
            raise RuntimeError(f"API error {getattr(resp, 'status_code', '?')}: {getattr(resp, 'text', '')[:400]}")
        data = resp.json()
        texts = []  # keep only the latest turn's text (the final answer)
        for block in data.get("content", []):
            if block.get("type") == "text":
                texts.append(block.get("text", ""))
                for c in block.get("citations") or []:
                    if c.get("url"):
                        cites[c["url"]] = c.get("title", "")
        if data.get("stop_reason") == "pause_turn":
            messages.append({"role": "assistant", "content": data["content"]})
            continue
        break
    return "".join(texts), [{"url": u, "title": t} for u, t in cites.items()]


def extract_json(text: str):
    m = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.S)
    candidates = m[::-1] if m else []
    if not candidates:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            candidates = [text[start:end + 1]]
    for c in candidates:
        try:
            return json.loads(c)
        except json.JSONDecodeError:
            continue
    raise ValueError("No valid JSON in model output")


# ----------------------------------------------------------------------------- prompts

SYSTEM = """You are a careful research assistant building a networking directory for a PhD student.
Use web search to check OFFICIAL, CURRENT sources (lab/company websites, university pages, official program pages,
Google Scholar, recent papers). Today's date is {today}.

Rules:
- Never invent people, programs, dates or URLs. If you cannot verify something, leave it out or use null.
- Every URL you return must be one you actually saw in search results.
- Prefer official program pages for program links.
- For people, use only information that is publicly posted in a professional context (lab members pages,
  university profiles, Google Scholar, GitHub, personal academic sites). Never include email addresses,
  phone numbers or home locations.
- Keep the summary to 2-3 sentences, plain language.
- Finish with ONE fenced ```json block that matches the requested schema exactly, and nothing after it."""

SCHEMA = """{
  "name": "display name",
  "institution": "university / company name",
  "department": "department, institute or team name, or null",
  "location": "city, country",
  "website": "main URL",
  "summary": "2-3 sentence summary of what they do",
  "topics": ["3-6 short topic tags"],
  "fit": {"score": 1, "reason": "one sentence on overlap with the student's interests (1=low, 2=medium, 3=high)"},
  "people": [
    {"name": "", "role": "PI / founder / team lead title", "background": "one line: training or prior roles",
     "links": [{"label": "Profile|Scholar|LinkedIn|X|Website", "url": ""}]}
  ],
  "recent_papers": [{"title": "", "venue": "", "year": 2026, "url": ""}],
  "programs": [
    {"name": "", "type": "internship|fellowship|visiting|residency|workshop|summer_school|phd_position|other",
     "eligibility": "who can apply, incl. whether non-US/UK students are eligible if stated",
     "deadline": "YYYY-MM-DD or null", "deadline_text": "e.g. 'Rolling', 'Usually opens in September'",
     "status": "open|opens_soon|rolling|closed|unknown", "url": ""}
  ],
  "contacts": [
    {"name": "", "role": "PhD student (year) | Postdoc", "focus": "what they work on",
     "opener": "one specific, genuine conversation opener tied to their work", "profile_url": "", "best": true}
  ],
  "flags": ["anything the student should know, e.g. 'PI moved to X in 2025', 'lab not taking visitors'"],
  "sources": [{"title": "", "url": ""}]
}"""


def build_prompt(seed: dict, profile: dict, prev: dict | None) -> str:
    cat = seed["category"]
    who = seed.get("pi")
    lines = [f"Research this {CATEGORY_LABEL[cat].lower()} for a networking directory."]
    lines.append(f"Name: {seed['name']}")
    if cat == "academic":
        lines.append(f"Principal investigator: {who}")
        lines.append(f"Listed under: {seed['institution']} ({seed['region']})")
        lines.append("First confirm the PI's CURRENT affiliation. If they have moved, research the lab at the new "
                     "institution and add a flag saying so.")
    if seed.get("hint"):
        lines.append(f"Hint: {seed['hint']}")

    lines.append("\nThe student: " + json.dumps(profile, ensure_ascii=False))

    lines.append("\nWhat to collect:")
    if cat == "academic":
        lines.append("- people: the PI (plus a co-lead or staff scientist if prominent). 1-3 entries.")
        lines.append("- recent_papers: 2-3 recent notable papers (last ~2 years) useful as talking points.")
    elif cat == "institute":
        lines.append("- people: director/president plus 2-3 group leaders whose work is closest to cancer genomics / "
                     "computational biology.")
        lines.append("- recent_papers: 0-2 items, optional.")
    else:
        lines.append("- people: founders and/or leaders of the bio / science / computational team (2-4).")
        lines.append("- recent_papers: 0-2 notable papers or model releases, optional.")
    lines.append("- programs: every current program a PhD student could use: internships, PhD fellowships, visiting "
                 "student/researcher schemes, residencies, summer schools, workshops. Include the official link. "
                 "If a program exists but dates for the next cycle are not posted, use status 'opens_soon' or "
                 "'unknown' with deadline_text explaining the usual timing.")
    if cat in HAS_CONTACTS:
        lines.append("- contacts: 2-3 CURRENT PhD students or postdocs (NOT the PI) from the public lab/group members "
                     "page whose work overlaps the student's interests. Mark the single best first contact with "
                     "best=true. Link to their public profile page. No emails.")
    else:
        lines.append("- contacts: return an empty list (not needed for companies).")
    lines.append("- fit: score against the student's interests.")

    if prev and prev.get("programs"):
        lines.append("\nLast week's programs (re-check each one is still accurate):\n"
                     + json.dumps(prev["programs"], ensure_ascii=False)[:3000])

    lines.append(f"\nReturn JSON with this schema:\n{SCHEMA}")
    return "\n".join(lines)


# ----------------------------------------------------------------------------- cleaning

def _url_ok_shape(u) -> bool:
    return isinstance(u, str) and u.startswith(("http://", "https://"))


def clean_entry(seed: dict, raw: dict, cited: list[dict]) -> dict:
    def s(x, n=600):
        return x.strip()[:n] if isinstance(x, str) else None

    people = []
    for p in (raw.get("people") or [])[:5]:
        if not s(p.get("name")):
            continue
        people.append({
            "name": s(p.get("name"), 120), "role": s(p.get("role"), 200), "background": s(p.get("background"), 300),
            "links": [{"label": s(l.get("label"), 30) or "Link", "url": l["url"]}
                      for l in (p.get("links") or []) if _url_ok_shape(l.get("url"))][:4],
        })

    programs = []
    for p in (raw.get("programs") or [])[:12]:
        if not s(p.get("name")):
            continue
        dl = p.get("deadline")
        try:
            dl = dt.date.fromisoformat(dl).isoformat() if dl else None
        except (TypeError, ValueError):
            dl = None
        status = p.get("status") if p.get("status") in PROGRAM_STATUSES else "unknown"
        if dl and dt.date.fromisoformat(dl) < TODAY and status in ("open", "opens_soon"):
            status = "closed"
        programs.append({
            "name": s(p.get("name"), 200), "type": s(p.get("type"), 40) or "other",
            "eligibility": s(p.get("eligibility"), 300), "deadline": dl,
            "deadline_text": s(p.get("deadline_text"), 120), "status": status,
            "url": p.get("url") if _url_ok_shape(p.get("url")) else None,
        })

    contacts = []
    if seed["category"] in HAS_CONTACTS:
        for c in (raw.get("contacts") or [])[:4]:
            if not s(c.get("name")):
                continue
            contacts.append({
                "name": s(c.get("name"), 120), "role": s(c.get("role"), 120), "focus": s(c.get("focus"), 250),
                "opener": s(c.get("opener"), 300), "best": bool(c.get("best")),
                "profile_url": c.get("profile_url") if _url_ok_shape(c.get("profile_url")) else None,
            })

    fit = raw.get("fit") or {}
    try:
        score = max(1, min(3, int(fit.get("score", 0))))
    except (TypeError, ValueError):
        score = None

    sources = [x for x in (raw.get("sources") or []) if _url_ok_shape(x.get("url"))]
    known = {x["url"] for x in sources}
    sources += [c for c in cited if c["url"] not in known]

    return {
        "summary": s(raw.get("summary"), 700),
        "department": s(raw.get("department"), 200),
        "current_institution": s(raw.get("institution"), 200),
        "location": s(raw.get("location"), 120),
        "website": raw.get("website") if _url_ok_shape(raw.get("website")) else None,
        "topics": [s(t, 40) for t in (raw.get("topics") or []) if s(t)][:6],
        "fit": {"score": score, "reason": s(fit.get("reason"), 300)},
        "people": people,
        "recent_papers": [
            {"title": s(p.get("title"), 300), "venue": s(p.get("venue"), 120), "year": p.get("year"),
             "url": p.get("url") if _url_ok_shape(p.get("url")) else None}
            for p in (raw.get("recent_papers") or [])[:3] if s(p.get("title"))
        ],
        "programs": programs,
        "contacts": contacts,
        "flags": [s(f, 300) for f in (raw.get("flags") or []) if s(f)][:5],
        "sources": [{"title": s(x.get("title"), 200) or x["url"], "url": x["url"]} for x in sources][:12],
    }


def check_links(entry: dict) -> None:
    """Mark links that do not resolve. Keeps them, but flags them for review."""
    urls = set()
    for p in entry.get("programs", []):
        if p.get("url"):
            urls.add(p["url"])
    if entry.get("website"):
        urls.add(entry["website"])
    broken = []
    for u in urls:
        try:
            r = requests.head(u, allow_redirects=True, timeout=12, headers={"user-agent": "Mozilla/5.0 outreach-directory"})
            if r.status_code in (403, 405, 429) or r.status_code >= 500:  # many sites block HEAD/bots; try GET
                r = requests.get(u, allow_redirects=True, timeout=15, stream=True,
                                 headers={"user-agent": "Mozilla/5.0 outreach-directory"})
            if r.status_code in (404, 410):
                broken.append(u)
        except requests.RequestException:
            pass  # network hiccup / bot wall: don't punish
    entry["broken_links"] = sorted(broken)


# ----------------------------------------------------------------------------- diff

def program_key(p):
    return (p.get("name") or "").lower().strip()


def diff_entry(old: dict | None, new: dict) -> list[dict]:
    items = []
    eid, name = new["id"], new["name"]
    if not old or not old.get("refreshed_at"):
        if new.get("refreshed_at"):
            items.append({"type": "new_entry", "entry_id": eid, "entry_name": name, "text": "Added to the directory"})
        return items
    old_p = {program_key(p): p for p in old.get("programs", [])}
    new_p = {program_key(p): p for p in new.get("programs", [])}
    for k, p in new_p.items():
        if k not in old_p:
            items.append({"type": "new_program", "entry_id": eid, "entry_name": name,
                          "text": f"New program: {p['name']}" + (f" (deadline {p['deadline']})" if p.get("deadline") else ""),
                          "url": p.get("url")})
        else:
            o = old_p[k]
            if (o.get("deadline") or "") != (p.get("deadline") or ""):
                items.append({"type": "date_changed", "entry_id": eid, "entry_name": name,
                              "text": f"{p['name']}: deadline {o.get('deadline') or 'n/a'} -> {p.get('deadline') or 'n/a'}",
                              "url": p.get("url")})
            if o.get("status") != p.get("status") and p.get("status") == "open":
                items.append({"type": "opened", "entry_id": eid, "entry_name": name,
                              "text": f"{p['name']} is now open", "url": p.get("url")})
    for k, p in old_p.items():
        if k not in new_p:
            items.append({"type": "program_removed", "entry_id": eid, "entry_name": name,
                          "text": f"No longer listed: {p['name']}"})
    old_c = {c["name"] for c in old.get("contacts", [])}
    new_c = {c["name"] for c in new.get("contacts", [])}
    if old_c and new_c and old_c != new_c:
        gone, added = old_c - new_c, new_c - old_c
        items.append({"type": "contact_changed", "entry_id": eid, "entry_name": name,
                      "text": "Suggested contacts changed" + (f"; added {', '.join(sorted(added))}" if added else "")
                      + (f"; removed {', '.join(sorted(gone))}" if gone else "")})
    for f in new.get("flags", []):
        if f not in old.get("flags", []):
            items.append({"type": "flag", "entry_id": eid, "entry_name": name, "text": f})
    for u in new.get("broken_links", []):
        if u not in old.get("broken_links", []):
            items.append({"type": "needs_review", "entry_id": eid, "entry_name": name,
                          "text": "Link not found (404) - kept last known info", "url": u})
    return items


# ----------------------------------------------------------------------------- discovery

def discover(seeds: list[dict], profile: dict, per_university: int = 4) -> list[dict]:
    """Suggest additional labs per university (shown in What's new > Suggestions)."""
    unis: dict[tuple, list[str]] = {}
    for s in seeds:
        if s["category"] == "academic" and s["region"] in ("US", "UK"):
            unis.setdefault((s["institution"], s["region"]), []).append(s.get("pi") or s["name"])
    out = []
    for (uni, region), existing in unis.items():
        prompt = (
            f"Suggest up to {per_university} additional faculty labs at {uni} that are among the strongest for a PhD "
            f"student interested in: {', '.join(profile.get('interests', []))}. Exclude these PIs already listed: "
            f"{', '.join(existing)}. Only include PIs whose current appointment at {uni} you verified.\n"
            'Return ```json {"suggestions": [{"pi": "", "lab_name": "", "department": "", "why": "one line", '
            '"url": ""}]}```'
        )
        try:
            text, _ = call_claude(SYSTEM.format(today=TODAY.isoformat()), prompt, max_searches=6, max_tokens=4000)
            for sug in extract_json(text).get("suggestions", [])[:per_university]:
                if sug.get("pi"):
                    out.append({**sug, "institution": uni, "region": region})
            print(f"  discover {uni}: ok")
        except Exception as e:  # noqa: BLE001
            print(f"  discover {uni}: failed ({e})", file=sys.stderr)
    return out


# ----------------------------------------------------------------------------- main

def base_entry(seed: dict) -> dict:
    return {
        "id": seed["id"], "name": seed["name"], "category": seed["category"], "region": seed.get("region", ""),
        "institution": seed.get("institution", ""), "pi": seed.get("pi"), "rank": seed.get("rank"),
        "summary": None, "department": None, "location": None, "website": None, "topics": [],
        "fit": {"score": None, "reason": None}, "people": [], "recent_papers": [], "programs": [],
        "contacts": [], "flags": [], "sources": [], "broken_links": [], "refreshed_at": None, "error": None,
    }


def research(seed: dict, profile: dict, prev: dict | None) -> dict:
    entry = base_entry(seed)
    try:
        text, cited = call_claude(SYSTEM.format(today=TODAY.isoformat()), build_prompt(seed, profile, prev))
        raw = extract_json(text)
        entry.update(clean_entry(seed, raw, cited))
        if raw.get("name") and seed["category"] != "academic":
            entry["name"] = seed["name"]  # keep the seed's display name for companies
        check_links(entry)
        entry["refreshed_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        print(f"  ok   {seed['id']}  ({len(entry['programs'])} programs, {len(entry['contacts'])} contacts)")
    except Exception as e:  # noqa: BLE001 - keep last week's data on any failure
        print(f"  FAIL {seed['id']}: {e}", file=sys.stderr)
        if prev:
            entry = {**prev, **{k: base_entry(seed)[k] for k in ("name", "category", "region", "institution", "pi", "rank")}}
        entry["error"] = str(e)[:300]
    return entry


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="comma-separated ids")
    ap.add_argument("--category", choices=list(CATEGORY_LABEL))
    ap.add_argument("--skeleton", action="store_true", help="no API calls; build empty entries from seeds")
    ap.add_argument("--discover", action="store_true", help="suggest new labs per university")
    ap.add_argument("--stale-days", type=float, default=0, help="skip entries refreshed within N days")
    args = ap.parse_args()

    seeds = load_seeds()
    profile = load_yaml(PROFILE)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    prev_dir = json.loads(DIRECTORY.read_text()) if DIRECTORY.exists() else {"entries": []}
    prev_by_id = {e["id"]: e for e in prev_dir.get("entries", [])}
    changes = json.loads(CHANGES.read_text()) if CHANGES.exists() else {"weeks": [], "suggestions": []}

    targets = seeds
    if args.only:
        ids = set(args.only.split(","))
        targets = [s for s in seeds if s["id"] in ids]
    if args.category:
        targets = [s for s in targets if s["category"] == args.category]
    if args.stale_days:
        cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=args.stale_days)
        def fresh(s):
            r = (prev_by_id.get(s["id"]) or {}).get("refreshed_at")
            return r and dt.datetime.fromisoformat(r) > cutoff
        targets = [s for s in targets if not fresh(s)]

    new_by_id: dict[str, dict] = {}
    if args.skeleton:
        pass  # the merge below builds every entry from seeds + last data, no API calls
    else:
        print(f"Refreshing {len(targets)} entries with {MODEL} (effort={EFFORT}, workers={WORKERS})")
        with cf.ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futs = {pool.submit(research, s, profile, prev_by_id.get(s["id"])): s for s in targets}
            for f in cf.as_completed(futs):
                e = f.result()
                new_by_id[e["id"]] = e

    # Merge: untouched seeds keep last data; removed seeds disappear (notes stay in D1)
    entries = []
    week_items: list[dict] = []
    for s in seeds:
        if s["id"] in new_by_id:
            e = new_by_id[s["id"]]
            week_items += diff_entry(prev_by_id.get(s["id"]), e)
        else:
            e = prev_by_id.get(s["id"]) or base_entry(s)
            e.update({k: base_entry(s)[k] for k in ("name", "category", "region", "institution", "pi", "rank")})
        entries.append(e)
    for e in entries:
        if e.get("error"):
            week_items.append({"type": "needs_review", "entry_id": e["id"], "entry_name": e["name"],
                               "text": f"Refresh failed, showing last known data ({e['error'][:120]})"})

    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    out = {"generated_at": now, "model": MODEL, "effort": EFFORT, "entries": entries}
    DIRECTORY.write_text(json.dumps(out, ensure_ascii=False, indent=1))

    if not args.skeleton:
        week = TODAY.isoformat()
        # Several runs in one week (e.g. "Refresh now") merge into the same week entry
        same = next((w for w in changes.get("weeks", []) if w["week"] == week), None)
        if same:
            seen = {(i["entry_id"], i["type"], i["text"]) for i in week_items}
            week_items += [i for i in same["items"] if (i["entry_id"], i["type"], i["text"]) not in seen]
        changes["weeks"] = [w for w in changes.get("weeks", []) if w["week"] != week]
        changes["weeks"].insert(0, {"week": week, "generated_at": now,
                                    "checked": max(len(targets), (same or {}).get("checked", 0)), "items": week_items})
        changes["weeks"] = changes["weeks"][:26]
    if args.discover:
        changes["suggestions"] = discover(seeds, profile)
        changes["suggestions_at"] = now
    CHANGES.write_text(json.dumps(changes, ensure_ascii=False, indent=1))
    print(f"Wrote {len(entries)} entries, {len(week_items)} changes.")
    failed = sum(1 for e in entries if e.get("error"))
    if targets and failed == len(targets) and not args.skeleton:
        sys.exit("Every entry failed - check the API key / base URL.")


if __name__ == "__main__":
    main()
