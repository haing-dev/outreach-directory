#!/usr/bin/env python3
"""
Weekly email: new internships / placements / fellowships found by the scraper.

Collects programs from companies (public/data/directory.json) and from universities /
institutes (public/data/institution_programs.json), drops closed ones and anything already
emailed (config/digest_sent.json), emails the rest, then records what was sent.
A program that was sent as "opens soon"/"rolling"/"dates not posted" is sent once more when it opens.

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


def select_new(programs: list[dict], sent: dict) -> list[dict]:
    new = []
    for p in programs:
        prev = sent.get(p["key"])
        if prev is None:
            p["why"] = "new"
            new.append(p)
        elif prev.get("status") != "open" and p.get("status") == "open":
            p["why"] = "now open"
            new.append(p)
    return new


def when(p) -> str:
    if p.get("deadline"):
        return "Deadline " + dt.date.fromisoformat(p["deadline"]).strftime("%-d %b %Y")
    return p.get("deadline_text") or {"rolling": "Rolling", "opens_soon": "Opens soon"}.get(p.get("status"), "Dates not posted")


def render(new: list[dict], site_url: str) -> tuple[str, str, str]:
    open_now = sorted([p for p in new if p.get("status") == "open"], key=lambda p: p.get("deadline") or "9999")
    soon = [p for p in new if p.get("status") in ("opens_soon", "rolling")]
    other = [p for p in new if p not in open_now and p not in soon]
    n_open = len(open_now)
    subject = f"{len(new)} new program{'s' if len(new) != 1 else ''} this week" + (f", {n_open} open now" if n_open else "")
    title = (f"{n_open} program{'s are' if n_open != 1 else ' is'} open now, and {len(new) - n_open} more are coming up"
             if n_open else f"{len(new)} new program{'s' if len(new) != 1 else ''} to watch")

    def section(name, items):
        if not items:
            return ""
        rows = []
        for p in items:
            name_html = (f'<a href="{html.escape(p["url"])}" style="color:#1F40E6;text-decoration:none;font-weight:bold">{html.escape(p["name"])}</a>'
                         if p.get("url") else f'<b>{html.escape(p["name"])}</b>')
            tag = ' <span style="color:#1F40E6">(now open)</span>' if p.get("why") == "now open" else ""
            details = " · ".join(x for x in [p.get("type"), p.get("location"), p.get("duration")] if x)
            req = f'<div style="margin-top:4px"><b>Requirements:</b> {html.escape(p["requirements"])}</div>' if p.get("requirements") else ""
            elig = f'<div style="margin-top:2px"><b>Eligibility:</b> {html.escape(p["eligibility"])}</div>' if p.get("eligibility") else ""
            rows.append(f'''<tr><td style="padding:14px 0;border-bottom:1px solid #D0D0D0;font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.5;color:#000">
              {name_html}{tag}<div style="color:#4D4D4D">{html.escape(p["org"])} · {html.escape(p["kind"])}{' · ' + html.escape(details) if details else ''}</div>
              <div style="margin-top:4px"><b>{html.escape(when(p))}</b></div>{req}{elig}</td></tr>''')
        return (f'<h2 style="font-family:Arial,Helvetica,sans-serif;font-size:16px;font-weight:bold;margin:28px 0 0;padding-bottom:8px;'
                f'border-bottom:1px solid #7F7F7F;color:#000">{name}</h2><table width="100%" cellpadding="0" cellspacing="0" '
                f'style="border-collapse:collapse">{"".join(rows)}</table>')

    body = f'''<!doctype html><html><head><meta charset="utf-8"></head><body style="margin:0;background:#fff">
<table width="100%" cellpadding="0" cellspacing="0"><tr><td align="center">
<table width="640" cellpadding="0" cellspacing="0" style="max-width:640px;width:100%;padding:28px 20px">
<tr><td style="font-family:Arial,Helvetica,sans-serif;font-size:12px;color:#4D4D4D">Outreach Directory · weekly programs · {TODAY.strftime("%-d %b %Y")}</td></tr>
<tr><td style="font-family:Georgia,'Times New Roman',serif;font-size:26px;font-weight:bold;line-height:1.25;color:#000;padding:10px 0 8px">{html.escape(title)}</td></tr>
<tr><td style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#4D4D4D;padding-bottom:14px;border-bottom:1px solid #000">
Only programs you haven't been sent before. Check each official page before applying, since dates can change.</td></tr>
<tr><td>{section("Open now", open_now)}{section("Rolling or opening soon", soon)}{section("Dates not posted yet", other)}</td></tr>
<tr><td style="font-family:Arial,Helvetica,sans-serif;font-size:12px;color:#4D4D4D;padding-top:24px">
{f'All programs: <a href="{html.escape(site_url)}/#programs" style="color:#1F40E6;text-decoration:none">{html.escape(site_url)}</a>' if site_url else ''}</td></tr>
</table></td></tr></table></body></html>'''
    text = title + "\n\n" + "\n\n".join(
        f"{p['name']} ({p['org']}) - {when(p)}\n{p.get('url') or ''}\nRequirements: {p.get('requirements') or '-'}" for p in open_now + soon + other)
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
