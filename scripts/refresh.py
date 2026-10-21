#!/usr/bin/env python3
"""
Refresh job for the Outreach Directory.

Two steps:
  1. DISCOVER (optional): for each university / institute in config/seeds.yaml, Claude finds every
     bioinformatics / computational-biology lab and writes config/labs.discovered.yaml.
  2. REFRESH: for each lab and company, Claude (Sonnet, medium effort, web search) collects
     the details and the site data is written to public/data/directory.json + changes.json.

Your notes live in Cloudflare D1 and are never touched here.

Usage:
  python scripts/refresh.py                         # refresh everything
  python scripts/refresh.py --only stanford-curtis,st-chai
  python scripts/refresh.py --category startup
  python scripts/refresh.py --discover "Stanford University" --skip-refresh
  python scripts/refresh.py --discover all
  python scripts/refresh.py --request config/run-request.yaml   # what CI uses
  python scripts/refresh.py --skeleton              # no API calls

Env:
  CLAUDE_TOKEN      API key, internal token, or subscription token (sk-ant-oat..., via Claude Code)
  CLAUDE_BASE_URL   default https://api.anthropic.com
  CLAUDE_MODEL      default claude-sonnet-5-5
  CLAUDE_EFFORT     default medium
  COLLECT_EMAILS    "true" to store publicly listed institutional emails (CI sets this only for private repos)
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
SEEDS = ROOT / "config" / "seeds.yaml"
DISCOVERED = ROOT / "config" / "labs.discovered.yaml"
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
MAX_SEARCHES = int(_env("MAX_SEARCHES", default="10"))
WEB_SEARCH_TOOL = _env("WEB_SEARCH_TOOL", default="web_search_20260318")
TOKEN = _env("CLAUDE_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
# Subscription tokens from `claude setup-token` (sk-ant-oat...) only work through Claude Code,
# so then we run the Claude Code CLI headless instead of calling the API directly.
USE_CLI = TOKEN.startswith("sk-ant-oat") or bool(_env("CLAUDE_CODE_OAUTH_TOKEN"))
WORKERS = int(_env("WORKERS", default="3" if USE_CLI else "4"))
# Standard keys go in x-api-key; many internal gateways want "Authorization: Bearer" (auto-switch on 401).
AUTH = {"mode": "bearer" if _env("ANTHROPIC_AUTH_TOKEN") and not _env("CLAUDE_TOKEN", "ANTHROPIC_API_KEY") else "x-api-key"}
COLLECT_EMAILS = _env("COLLECT_EMAILS").lower() == "true"

LAB_CATS = {"academic", "institute"}
COMPANY_CATS = {"bigtech", "biotech", "startup"}
CATEGORY_LABEL = {
    "academic": "academic lab", "institute": "lab at an independent research institute",
    "bigtech": "big-tech AI-for-biology team", "biotech": "biotech / pharma company", "startup": "AI-bio startup",
}
PROGRAM_STATUSES = {"open", "opens_soon", "rolling", "closed", "unknown"}
TODAY = dt.date.today()
META_KEYS = ("name", "category", "region", "institution", "pi", "rank", "group", "department_hint")


def load_yaml(path: Path):
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")


INST_SHORT = {
    "stanford university": "stanford", "harvard university": "harvard", "mit": "mit", "princeton university": "princeton",
    "caltech": "caltech", "university of cambridge": "cam", "university college london": "ucl",
    "university of oxford": "ox", "imperial college london": "imperial", "arc institute": "arc",
    "broad institute": "broad", "scripps research": "scripps", "new york genome center": "nygc",
    "chan zuckerberg biohub": "czb", "wellcome sanger institute": "sanger", "francis crick institute": "crick",
    "embl-ebi": "ebi", "the institute of cancer research": "icr", "mrc laboratory of molecular biology": "lmb",
}


def lab_id(institution: str, pi: str) -> str:
    short = INST_SHORT.get(institution.lower(), slug(institution)[:12])
    return f"{short}-{slug(pi.split()[-1] if pi else 'lab')}"


def load_seeds() -> list[dict]:
    raw = load_yaml(SEEDS)
    out: list[dict] = []
    for lab in raw.get("labs") or []:
        out.append({**lab, "category": lab.get("category", "academic")})
    for lab in (load_yaml(DISCOVERED).get("labs") or []):
        out.append(lab)
    for cat in ("bigtech", "biotech", "startup"):
        for i, it in enumerate((raw.get("companies") or {}).get(cat) or []):
            out.append({**it, "category": cat, "region": it.get("region", ""), "institution": it["name"], "rank": i + 1})
    seen, uniq = set(), []
    for s in out:
        if s["id"] in seen:
            continue  # manual entry wins over a discovered duplicate
        seen.add(s["id"])
        uniq.append(s)
    return [s for s in uniq if s.get("enabled", True)]


def institutions() -> list[dict]:
    raw = load_yaml(SEEDS)
    out = []
    for region, names in (raw.get("universities") or {}).items():
        out += [{"name": n, "region": region, "category": "academic"} for n in names or []]
    for region, names in (raw.get("institutes") or {}).items():
        out += [{"name": n, "region": region, "category": "institute"} for n in names or []]
    return out


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


def call_claude_cli(system: str, user: str, max_searches: int) -> tuple[str, list[dict]]:
    """Same research turn via Claude Code (works with a Claude subscription token)."""
    exe = shutil.which("claude")
    if not exe:
        raise RuntimeError("Claude Code CLI not installed (npm install -g @anthropic-ai/claude-code)")
    env = {**os.environ, "CLAUDE_CODE_OAUTH_TOKEN": _env("CLAUDE_CODE_OAUTH_TOKEN") or TOKEN}
    env.pop("ANTHROPIC_API_KEY", None)
    cmd = [exe, "-p", user, "--model", MODEL, "--effort", EFFORT,
           "--append-system-prompt", system + f"\nUse at most {max_searches} web searches.",
           "--tools", "WebSearch,WebFetch", "--allowedTools", "WebSearch,WebFetch",
           "--permission-mode", "dontAsk", "--output-format", "json",
           "--max-turns", str(max_searches * 3 + 5), "--no-session-persistence"]
    last = ""
    for attempt in range(3):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=2400, env=env, cwd="/tmp")
        except subprocess.TimeoutExpired:
            last = "timed out"
            continue
        try:
            out = json.loads(r.stdout)
        except json.JSONDecodeError:
            last = (r.stderr or r.stdout)[-400:]
            time.sleep(30 * (attempt + 1))
            continue
        if out.get("is_error") or r.returncode != 0:
            last = str(out.get("result") or out.get("subtype") or r.stderr)[-400:]
            if "rate" in last.lower() or "limit" in last.lower():
                time.sleep(120 * (attempt + 1))
            continue
        return out.get("result", ""), []
    raise RuntimeError(f"Claude Code error: {last}")


def call_claude(system: str, user: str, max_searches: int = MAX_SEARCHES, max_tokens: int = 8000) -> tuple[str, list[dict]]:
    """Run one research turn with web search. Returns (final text, cited sources)."""
    if USE_CLI:
        return call_claude_cli(system, user, max_searches)
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
Use web search and web fetch to check OFFICIAL, CURRENT sources (lab / company websites, university and
department pages, lab "people" / "members" pages, official program pages, Google Scholar). Today is {today}.

Rules:
- Never invent people, emails, programs, dates or URLs. If you cannot verify something, use null or leave it out.
- Every URL you return must be one you actually saw.
- Finish with ONE fenced ```json block matching the requested schema exactly, and nothing after it."""

LAB_SCHEMA = """{
  "name": "lab / group name",
  "url": "lab website (main page)",
  "pi": {"name": "", "title": "e.g. Associate Professor of Genetics", "profile_url": "", "scholar_url": ""},
  "institution": "current institution",
  "department": "department(s) / programs",
  "location": "city, country",
  "focus": "3-4 sentences describing the lab's research topics and methods, concrete and specific",
  "topics": ["4-6 short topic tags"],
  "fit": {"score": 3, "reason": "one sentence: overlap with the student's interests. 5 = strongest fit, 1 = weakest"},
  "members": [
    {"name": "", "role": "Postdoc | PhD student", "year": "e.g. 3rd year, or null", "focus": "what they work on, one line",
     "email": "only if publicly listed on an official lab/university page, else null", "profile_url": "personal/lab profile page or null"}
  ],
  "members_page": "URL of the lab people/members page used",
  "flags": ["anything notable, e.g. 'PI moved to X in 2025', 'members page last updated 2023'"],
  "sources": [{"title": "", "url": ""}]
}"""

COMPANY_SCHEMA = """{
  "name": "",
  "url": "main website for the relevant team",
  "leads": [{"name": "", "role": "founder / head of team title", "background": "one line", "profile_url": ""}],
  "focus": "3-4 sentences on what the team/company does in biology / AI for science, concrete and specific",
  "topics": ["4-6 short topic tags"],
  "location": "HQ / main sites",
  "fit": {"score": 3, "reason": "one sentence: overlap with the student's interests. 5 = strongest fit, 1 = weakest"},
  "programs": [
    {"name": "", "type": "internship | fellowship | residency | student researcher | visiting | other",
     "requirements": "what applicants need: degree stage, skills, publications, languages, etc.",
     "eligibility": "who can apply incl. location / work authorization notes if stated",
     "location": "", "duration": "", "deadline": "YYYY-MM-DD or null",
     "deadline_text": "e.g. 'Rolling', 'Usually opens in September'", "status": "open|opens_soon|rolling|closed|unknown", "url": ""}
  ],
  "flags": [],
  "sources": [{"title": "", "url": ""}]
}"""


def build_prompt(seed: dict, profile: dict, prev: dict | None) -> tuple[str, int]:
    cat = seed["category"]
    student = "The student: " + json.dumps(profile, ensure_ascii=False)
    if cat in LAB_CATS:
        lines = [
            f"Research this {CATEGORY_LABEL[cat]} for a networking directory.",
            f"Lab: {seed.get('name')}  |  PI: {seed.get('pi')}  |  Listed at: {seed.get('institution')}",
        ]
        if seed.get("department_hint") or seed.get("hint"):
            lines.append(f"Hint: {seed.get('department_hint') or seed.get('hint')}")
        if seed.get("url"):
            lines.append(f"Lab URL (from discovery): {seed['url']}")
        lines += [
            "",
            "1. Confirm the PI's CURRENT institution (flag a move).",
            "2. Open the lab's people/members page and list EVERY current postdoc and PhD student "
            "(not alumni, not undergrads, not staff). Include their research focus and profile link. "
            "Include an email only when it is published on an official lab or university page.",
            "3. Describe the research focus in 3-4 specific sentences.",
            "4. Score fit 1-5 against the student's interests (be discriminating: use the whole range).",
            "",
            student,
            f"\nReturn JSON with this schema:\n{LAB_SCHEMA}",
        ]
        return "\n".join(lines), MAX_SEARCHES
    lines = [
        f"Research this {CATEGORY_LABEL[cat]} for a networking directory: {seed['name']}",
    ]
    if seed.get("hint"):
        lines.append(f"Focus on: {seed['hint']}")
    lines += [
        "",
        "1. Identify founders and/or leaders of the biology / AI-for-science / computational team (2-4 people).",
        "2. Describe what they do in biology in 3-4 specific sentences.",
        "3. List every current program a PhD student could use (internships, PhD fellowships, residencies, "
        "student researcher roles, visiting roles) with REQUIREMENTS, eligibility, deadline and the official link. "
        "If the next cycle isn't posted, say when it usually opens.",
        "4. Score fit 1-5 against the student's interests.",
        "",
        student,
    ]
    if prev and prev.get("programs"):
        lines.append("\nLast week's programs (re-check):\n" + json.dumps(prev["programs"], ensure_ascii=False)[:3000])
    lines.append(f"\nReturn JSON with this schema:\n{COMPANY_SCHEMA}")
    return "\n".join(lines), MAX_SEARCHES


DISCOVER_PROMPT = """List ALL faculty-led research labs/groups at {inst} whose work is substantially
bioinformatics, computational biology, genomics data science, statistical genetics, systems biology modelling,
or machine learning for biology/medicine. Include computational groups in any department (biology, genetics,
biomedical data science, computer science, statistics, bioengineering, medicine, cancer centres, affiliated
institutes) and wet/dry hybrid labs with a major computational component. Do not limit to cancer.
Aim for completeness: check department faculty lists and research-area pages. Only include PIs whose current
appointment at {inst} you verified.

Return:
```json
{{"labs": [{{"pi": "Full Name", "lab_name": "X Lab", "department": "", "url": "lab or profile URL"}}]}}
```"""


def discover(inst: dict) -> list[dict]:
    text, _ = call_claude(SYSTEM.format(today=TODAY.isoformat()), DISCOVER_PROMPT.format(inst=inst["name"]), max_searches=25)
    labs = []
    for x in extract_json(text).get("labs", []):
        pi = (x.get("pi") or "").strip()
        if not pi:
            continue
        labs.append({
            "id": lab_id(inst["name"], pi), "name": (x.get("lab_name") or f"{pi.split()[-1]} Lab").strip(),
            "pi": pi, "institution": inst["name"], "region": inst["region"], "category": inst["category"],
            "department_hint": (x.get("department") or "").strip() or None,
            "url": x.get("url") if _url(x.get("url")) else None, "discovered": TODAY.isoformat(),
        })
    return labs


def run_discovery(names: list[str]) -> list[dict]:
    insts = institutions()
    if names != ["all"]:
        wanted = {n.lower() for n in names}
        insts = [i for i in insts if i["name"].lower() in wanted]
    existing = load_yaml(DISCOVERED).get("labs") or []
    by_id = {l["id"]: l for l in existing}
    added = []

    def one(inst):
        try:
            found = discover(inst)
            print(f"  discover {inst['name']}: {len(found)} labs")
            return found
        except Exception as e:  # noqa: BLE001
            print(f"  discover {inst['name']}: FAILED ({e})", file=sys.stderr)
            return []

    with cf.ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = list(pool.map(one, insts))
    for found in results:
        for lab in found:
            if lab["id"] not in by_id:
                by_id[lab["id"]] = lab
                added.append(lab)
    labs = sorted(by_id.values(), key=lambda l: (l["institution"], l["pi"]))
    header = "# Generated by discovery. Safe to edit: delete an entry or set enabled: false.\n"
    DISCOVERED.write_text(header + yaml.safe_dump({"labs": labs}, sort_keys=False, allow_unicode=True, width=200))
    return added


# ----------------------------------------------------------------------------- cleaning

def _url(u) -> bool:
    return isinstance(u, str) and u.startswith(("http://", "https://"))


def _s(x, n=600):
    return x.strip()[:n] if isinstance(x, str) and x.strip() else None


def _fit(raw):
    fit = raw.get("fit") or {}
    try:
        score = max(1, min(5, int(fit.get("score"))))
    except (TypeError, ValueError):
        score = None
    return {"score": score, "reason": _s(fit.get("reason"), 300)}


def _sources(raw, cited):
    src = [{"title": _s(x.get("title"), 200) or x["url"], "url": x["url"]} for x in (raw.get("sources") or []) if _url(x.get("url"))]
    known = {x["url"] for x in src}
    return (src + [c for c in cited if c["url"] not in known])[:12]


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.I)


def clean_lab(raw: dict, cited: list[dict]) -> dict:
    pi = raw.get("pi") or {}
    members = []
    for m in (raw.get("members") or [])[:80]:
        name = _s(m.get("name"), 120)
        role = _s(m.get("role"), 60) or ""
        if not name or not re.search(r"post-?doc|ph\.?d|doctoral|graduate student|dphil", role, re.I):
            continue
        email = _s(m.get("email"), 120)
        members.append({
            "name": name, "role": role, "year": _s(str(m.get("year")) if m.get("year") else None, 40),
            "focus": _s(m.get("focus"), 250),
            "email": email if (COLLECT_EMAILS and email and EMAIL_RE.match(email)) else None,
            "profile_url": m.get("profile_url") if _url(m.get("profile_url")) else None,
        })
    members.sort(key=lambda m: (0 if re.search("post", m["role"], re.I) else 1, m["name"]))
    return {
        "url": raw.get("url") if _url(raw.get("url")) else None,
        "pi_profile": {"name": _s(pi.get("name"), 120), "title": _s(pi.get("title"), 200),
                       "profile_url": pi.get("profile_url") if _url(pi.get("profile_url")) else None,
                       "scholar_url": pi.get("scholar_url") if _url(pi.get("scholar_url")) else None},
        "current_institution": _s(raw.get("institution"), 200),
        "department": _s(raw.get("department"), 200),
        "location": _s(raw.get("location"), 120),
        "focus": _s(raw.get("focus"), 900),
        "topics": [t for t in (_s(t, 40) for t in raw.get("topics") or []) if t][:6],
        "fit": _fit(raw),
        "members": members,
        "members_page": raw.get("members_page") if _url(raw.get("members_page")) else None,
        "flags": [f for f in (_s(f, 300) for f in raw.get("flags") or []) if f][:5],
        "sources": _sources(raw, cited),
    }


def clean_company(raw: dict, cited: list[dict]) -> dict:
    programs = []
    for p in (raw.get("programs") or [])[:12]:
        if not _s(p.get("name")):
            continue
        dl = p.get("deadline")
        try:
            dl = dt.date.fromisoformat(dl).isoformat() if dl else None
        except (TypeError, ValueError):
            dl = None
        status = p.get("status") if p.get("status") in PROGRAM_STATUSES else "unknown"
        if dl and dl < TODAY.isoformat() and status in ("open", "opens_soon"):
            status = "closed"
        programs.append({
            "name": _s(p.get("name"), 200), "type": _s(p.get("type"), 40) or "other",
            "requirements": _s(p.get("requirements"), 500), "eligibility": _s(p.get("eligibility"), 300),
            "location": _s(p.get("location"), 120), "duration": _s(p.get("duration"), 80),
            "deadline": dl, "deadline_text": _s(p.get("deadline_text"), 120), "status": status,
            "url": p.get("url") if _url(p.get("url")) else None,
        })
    return {
        "url": raw.get("url") if _url(raw.get("url")) else None,
        "leads": [{"name": _s(l.get("name"), 120), "role": _s(l.get("role"), 200), "background": _s(l.get("background"), 300),
                   "profile_url": l.get("profile_url") if _url(l.get("profile_url")) else None}
                  for l in (raw.get("leads") or [])[:5] if _s(l.get("name"))],
        "focus": _s(raw.get("focus"), 900),
        "topics": [t for t in (_s(t, 40) for t in raw.get("topics") or []) if t][:6],
        "location": _s(raw.get("location"), 120),
        "fit": _fit(raw),
        "programs": programs,
        "flags": [f for f in (_s(f, 300) for f in raw.get("flags") or []) if f][:5],
        "sources": _sources(raw, cited),
    }


def check_links(entry: dict) -> None:
    urls = {p["url"] for p in entry.get("programs", []) if p.get("url")}
    if entry.get("url"):
        urls.add(entry["url"])
    broken = []
    for u in urls:
        try:
            r = requests.head(u, allow_redirects=True, timeout=12, headers={"user-agent": "Mozilla/5.0"})
            if r.status_code in (403, 405, 429) or r.status_code >= 500:
                r = requests.get(u, allow_redirects=True, timeout=15, stream=True, headers={"user-agent": "Mozilla/5.0"})
            if r.status_code in (404, 410):
                broken.append(u)
        except requests.RequestException:
            pass
    entry["broken_links"] = sorted(broken)


# ----------------------------------------------------------------------------- diff

def diff_entry(old: dict | None, new: dict) -> list[dict]:
    eid, name = new["id"], new["name"]
    item = lambda t, text, url=None: {"type": t, "entry_id": eid, "entry_name": name, "text": text, **({"url": url} if url else {})}
    if not old or not old.get("refreshed_at"):
        return [item("new_entry", "Added")] if new.get("refreshed_at") else []
    out = []
    op = {(p.get("name") or "").lower(): p for p in old.get("programs", [])}
    np_ = {(p.get("name") or "").lower(): p for p in new.get("programs", [])}
    for k, p in np_.items():
        if k not in op:
            out.append(item("new_program", f"New program: {p['name']}", p.get("url")))
        elif (op[k].get("deadline") or "") != (p.get("deadline") or ""):
            out.append(item("date_changed", f"{p['name']}: {op[k].get('deadline') or 'n/a'} → {p.get('deadline') or 'n/a'}", p.get("url")))
    for k, p in op.items():
        if k not in np_:
            out.append(item("program_removed", f"No longer listed: {p['name']}"))
    om = {m["name"] for m in old.get("members", [])}
    nm = {m["name"] for m in new.get("members", [])}
    if om and nm != om:
        add, gone = sorted(nm - om), sorted(om - nm)
        text = "; ".join(x for x in [f"joined: {', '.join(add)}" if add else "", f"left: {', '.join(gone)}" if gone else ""] if x)
        out.append(item("members_changed", text))
    for f in new.get("flags", []):
        if f not in old.get("flags", []):
            out.append(item("flag", f))
    for u in new.get("broken_links", []):
        if u not in old.get("broken_links", []):
            out.append(item("needs_review", "Link not found (404)", u))
    return out


# ----------------------------------------------------------------------------- main

def base_entry(seed: dict) -> dict:
    e = {k: seed.get(k) for k in META_KEYS}
    e.update({"id": seed["id"], "url": seed.get("url"), "focus": None, "topics": [], "fit": {"score": None, "reason": None},
              "flags": [], "sources": [], "broken_links": [], "refreshed_at": None, "error": None})
    if seed["category"] in LAB_CATS:
        e.update({"pi_profile": {}, "members": [], "department": seed.get("department_hint"), "location": None})
    else:
        e.update({"leads": [], "programs": [], "location": None})
    return e


def research(seed: dict, profile: dict, prev: dict | None) -> dict:
    entry = base_entry(seed)
    try:
        prompt, searches = build_prompt(seed, profile, prev)
        text, cited = call_claude(SYSTEM.format(today=TODAY.isoformat()), prompt, max_searches=searches, max_tokens=12000)
        raw = extract_json(text)
        entry.update(clean_lab(raw, cited) if seed["category"] in LAB_CATS else clean_company(raw, cited))
        entry["url"] = entry.get("url") or seed.get("url")
        check_links(entry)
        entry["refreshed_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        n = len(entry.get("members", entry.get("programs", [])))
        print(f"  ok   {seed['id']}  (fit {entry['fit']['score']}, {n} {'members' if seed['category'] in LAB_CATS else 'programs'})")
    except Exception as e:  # noqa: BLE001 - keep last data on any failure
        print(f"  FAIL {seed['id']}: {e}", file=sys.stderr)
        if prev:
            entry = {**prev, **{k: seed.get(k) for k in META_KEYS}}
        entry["error"] = str(e)[:300]
    return entry


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="comma-separated ids")
    ap.add_argument("--category", choices=sorted(LAB_CATS | COMPANY_CATS))
    ap.add_argument("--discover", action="append", default=[], help="institution name, or 'all' (repeatable)")
    ap.add_argument("--skip-refresh", action="store_true")
    ap.add_argument("--request", help="YAML run request (CI)")
    ap.add_argument("--skeleton", action="store_true", help="no API calls; rebuild from seeds + last data")
    args = ap.parse_args()

    only = set(args.only.split(",")) if args.only else set()
    category, discover_names, skip = args.category, list(args.discover), args.skip_refresh
    if args.request:
        req = load_yaml(Path(args.request))
        only |= set(req.get("only") or [])
        category = category or (req.get("category") or None)
        discover_names += [str(x) for x in (req.get("discover") or [])]
        skip = skip or bool(req.get("skip_refresh"))
        print(f"Run request #{req.get('request')}: only={sorted(only) or 'all'} category={category or 'all'} "
              f"discover={discover_names or '-'} skip_refresh={skip}")
    if not args.skeleton and not TOKEN:
        sys.exit("Missing Claude token: add the CLAUDE_TOKEN secret in GitHub.")
    print(f"Model {MODEL}, effort {EFFORT}, via {'Claude Code (subscription token)' if USE_CLI else 'Claude API'}, "
          f"workers {WORKERS}, emails {'on' if COLLECT_EMAILS else 'off (repo is public)'}")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    prev_dir = json.loads(DIRECTORY.read_text()) if DIRECTORY.exists() else {"entries": []}
    prev_by_id = {e["id"]: e for e in prev_dir.get("entries", [])}
    changes = json.loads(CHANGES.read_text()) if CHANGES.exists() else {"weeks": []}
    week_items: list[dict] = []

    if discover_names and not args.skeleton:
        added = run_discovery(discover_names)
        print(f"Discovery added {len(added)} labs")
        for lab in added:
            week_items.append({"type": "discovered", "entry_id": lab["id"], "entry_name": f"{lab['name']} ({lab['pi']})",
                               "text": f"New lab found at {lab['institution']}"})
        if not only and not category:
            only = {l["id"] for l in added}  # research the newly found labs
            if not only:
                skip = True

    seeds = load_seeds()
    targets = [] if (skip or args.skeleton) else seeds
    if only:
        targets = [s for s in targets if s["id"] in only]
    if category:
        targets = [s for s in targets if s["category"] == category]

    new_by_id: dict[str, dict] = {}
    if targets:
        print(f"Refreshing {len(targets)} entries")
        with cf.ThreadPoolExecutor(max_workers=WORKERS) as pool:
            for f in cf.as_completed([pool.submit(research, s, load_yaml(PROFILE), prev_by_id.get(s["id"])) for s in targets]):
                e = f.result()
                new_by_id[e["id"]] = e

    entries = []
    for s in seeds:
        if s["id"] in new_by_id:
            e = new_by_id[s["id"]]
            week_items += diff_entry(prev_by_id.get(s["id"]), e)
        else:
            old = prev_by_id.get(s["id"])
            e = {**base_entry(s), **(old or {}), **{k: s.get(k) for k in META_KEYS}} if old and old.get("refreshed_at") else base_entry(s)
            if not COLLECT_EMAILS:
                for m in e.get("members", []):
                    m["email"] = None
        entries.append(e)
        if e.get("error") and s["id"] in new_by_id:
            week_items.append({"type": "needs_review", "entry_id": e["id"], "entry_name": e["name"],
                               "text": f"Refresh failed, showing last known data ({e['error'][:120]})"})

    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    DIRECTORY.write_text(json.dumps({"generated_at": now, "model": MODEL, "effort": EFFORT, "entries": entries},
                                    ensure_ascii=False, indent=1))
    if not args.skeleton and (targets or discover_names):
        week = TODAY.isoformat()
        same = next((w for w in changes.get("weeks", []) if w["week"] == week), None)
        if same:
            seen = {(i["entry_id"], i["type"], i["text"]) for i in week_items}
            week_items += [i for i in same["items"] if (i["entry_id"], i["type"], i["text"]) not in seen]
        changes["weeks"] = [w for w in changes.get("weeks", []) if w["week"] != week]
        changes["weeks"].insert(0, {"week": week, "generated_at": now, "checked": len(targets), "items": week_items})
        changes["weeks"] = changes["weeks"][:26]
    CHANGES.write_text(json.dumps(changes, ensure_ascii=False, indent=1))
    failed = [e["id"] for e in new_by_id.values() if e.get("error")]
    print(f"Wrote {len(entries)} entries; refreshed {len(new_by_id) - len(failed)}, failed {len(failed)}.")
    if targets and len(failed) == len(targets):
        sys.exit("Every entry failed - check the Claude token.")


if __name__ == "__main__":
    main()
