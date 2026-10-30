#!/usr/bin/env python3
"""
Weekly email: new internships / placements / fellowships found by the scraper.

Collects programs from companies (public/data/directory.json) and from universities /
institutes (public/data/institution_programs.json), drops closed ones and anything already
emailed (config/digest_sent.json), emails the rest, then records what was sent.
Only programs accepting applications (open or rolling) are sent; anything not open yet is held
until a later scan finds it open.

Usage:
  python scripts/digest.py --preview        # write public/data/last-digest.html, don't send
  python scripts/digest.py --send           # send and update config/digest_sent.json

Env (GitHub secrets): GMAIL_USER, GMAIL_APP_PASSWORD   (or SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD)
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import smtplib
import sys
from email.message import EmailMessage
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DIRECTORY = ROOT / "public" / "data" / "directory.json"
INST = ROOT / "public" / "data" / "institution_programs.json"
SENT = ROOT / "config" / "digest_sent.json"
CONFIG = ROOT / "config" / "digest.yaml"
PREVIEW = ROOT / "public" / "data" / "last-digest.html"
TODAY = dt.date.today()
GROUP = {"bigtech": "Big tech", "biotech": "Biotech and pharma", "startup": "Startup",
         "academic": "University", "institute": "Institute"}


def load(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def collect() -> list[dict]:
    out = []
    for e in load(DIRECTORY, {"entries": []})["entries"]:
        for p in e.get("programs") or []:
            out.append({**p, "org": e["name"], "kind": GROUP.get(e["category"], ""), "entry_id": e["id"]})
    for i in load(INST, {"institutions": []})["institutions"]:
        for p in i.get("programs") or []:
            out.append({**p, "org": i["name"], "kind": GROUP.get(i.get("category"), ""), "entry_id": ""})
    keep = []
    for p in out:
        if p.get("status") == "closed":
            continue
        if p.get("deadline") and p["deadline"] < TODAY.isoformat():
            continue
        p["key"] = f"{p['org']}|{p['name']}".lower().strip()
        keep.append(p)
    return keep


OPEN = {"open", "rolling"}


def select_new(programs: list[dict], sent: dict) -> list[dict]:
    """Only programs accepting applications now, never sent before as open."""
    return [p for p in programs if p.get("status") in OPEN and (sent.get(p["key"]) or {}).get("status") not in OPEN]


def short_eligibility(programs: list[dict]) -> None:
    """One short line per program (<= 10 words) via Claude; falls back to the first clause."""
    def fallback(p):
        e = (p.get("eligibility") or p.get("requirements") or "").split(".")[0]
        words = e.split()
        return " ".join(words[:12]) + ("…" if len(words) > 12 else "")
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        import refresh  # noqa: E402
        if not refresh.TOKEN:
            raise RuntimeError("no token")
        items = [{"i": i, "program": p["name"], "org": p["org"], "eligibility": p.get("eligibility"),
                  "requirements": p.get("requirements")} for i, p in enumerate(programs)]
        prompt = ("For each program below, write who can apply in at most 10 words: plain, concrete, no repetition of "
                  "the program or organization name, no filler (e.g. 'PhD students, any country, ML/comp-bio background'). "
                  "Use only the given text. Return ```json {\"items\": [{\"i\": 0, \"who\": \"...\"}]}```\n\n"
                  + json.dumps(items, ensure_ascii=False))
        text, _ = refresh.call_claude("You condense text precisely. Do not use tools.", prompt, max_searches=0)
        got = {int(x["i"]): str(x["who"]).strip() for x in refresh.extract_json(text).get("items", [])}
        for i, p in enumerate(programs):
            p["who"] = got.get(i) or fallback(p)
    except Exception as e:  # noqa: BLE001
        print(f"Short eligibility via Claude failed ({e}); using first clause", file=sys.stderr)
        for p in programs:
            p["who"] = fallback(p)


def render(new: list[dict], site_url: str) -> tuple[str, str, str]:
    new = sorted(new, key=lambda p: (p.get("deadline") or "9999", p["org"]))
    n = len(new)
    subject = f"{n} program{'s' if n != 1 else ''} open now"
    td = "padding:9px 12px 9px 0;border-bottom:1px solid #D0D0D0;vertical-align:top;font-family:Arial,Helvetica,sans-serif;font-size:13px;color:#000"
    th = "padding:0 12px 8px 0;border-bottom:1px solid #7F7F7F;text-align:left;font-family:Arial,Helvetica,sans-serif;font-size:13px;font-weight:bold"
    rows = "".join(
        f'<tr><td style="{td}"><b>{html.escape(p["name"])}</b><br><span style="color:#4D4D4D">{html.escape(p["org"])}'
        f'{" · due " + dt.date.fromisoformat(p["deadline"]).strftime("%-d %b") if p.get("deadline") else ""}</span></td>'
        f'<td style="{td}">{html.escape(p.get("who") or "")}</td>'
        f'<td style="{td};white-space:nowrap">{f"""<a href="{html.escape(p["url"])}" style="color:#1F40E6;text-decoration:none">Apply</a>""" if p.get("url") else "–"}</td></tr>'
        for p in new)
    body = f'''<!doctype html><html><head><meta charset="utf-8"></head><body style="margin:0;background:#fff">
<table width="100%" cellpadding="0" cellspacing="0"><tr><td align="center">
<table width="680" cellpadding="0" cellspacing="0" style="max-width:680px;width:100%;padding:24px 16px">
<tr><td style="font-family:Georgia,'Times New Roman',serif;font-size:22px;font-weight:bold;color:#000;padding-bottom:14px">{html.escape(subject)}</td></tr>
<tr><td><table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse">
<tr><th style="{th}">Program</th><th style="{th}">Eligibility</th><th style="{th}">Link</th></tr>{rows}</table></td></tr>
<tr><td style="font-family:Arial,Helvetica,sans-serif;font-size:12px;color:#4D4D4D;padding-top:16px">{f'<a href="{html.escape(site_url)}" style="color:#1F40E6;text-decoration:none">All programs</a>' if site_url else ""}</td></tr>
</table></td></tr></table></body></html>'''
    text = subject + "\n\n" + "\n".join(f"- {p['name']} ({p['org']}): {p.get('who') or ''} {p.get('url') or ''}" for p in new)
    return subject, body, text


def send(subject, body, text, to):
    user = os.environ.get("SMTP_USER") or os.environ.get("GMAIL_USER")
    pw = os.environ.get("SMTP_PASSWORD") or os.environ.get("GMAIL_APP_PASSWORD")
    host = os.environ.get("SMTP_HOST") or "smtp.gmail.com"
    port = int(os.environ.get("SMTP_PORT") or 465)
    if not user or not pw:
        sys.exit("Email not sent: add GitHub secrets GMAIL_USER and GMAIL_APP_PASSWORD.")
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, f"Outreach Directory <{user}>", ", ".join(to)
    msg.set_content(text)
    msg.add_alternative(body, subtype="html")
    with smtplib.SMTP_SSL(host, port, timeout=60) as s:
        s.login(user, pw)
        s.send_message(msg)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--preview", action="store_true")
    args = ap.parse_args()
    cfg = yaml.safe_load(CONFIG.read_text()) if CONFIG.exists() else {}
    to = cfg.get("recipients") or []
    sent = load(SENT, {})
    new = select_new(collect(), sent)
    print(f"{len(new)} programs to send")
    if not new:
        print("Nothing new this week; no email sent.")
        return
    short_eligibility(new)
    subject, body, text = render(new, cfg.get("site_url", ""))
    PREVIEW.write_text(body)
    if args.send:
        if not to:
            sys.exit("No recipients in config/digest.yaml")
        send(subject, body, text, to)
        for p in new:
            sent[p["key"]] = {"status": p.get("status"), "sent": TODAY.isoformat(), "url": p.get("url")}
        SENT.write_text(json.dumps(sent, indent=1, ensure_ascii=False, sort_keys=True))
        print(f"Sent '{subject}' to {', '.join(to)}")


if __name__ == "__main__":
    main()
