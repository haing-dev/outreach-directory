# Outreach Directory

A private website listing labs, institutes and companies for cancer-genomics networking. Every Monday, Claude (Sonnet, medium effort, with web search) re-checks each entry and updates:

- key people (PI / team lead / founders)
- a 2–3 line summary and topics, plus a "fit" score against your interests
- programs (internships, fellowships, visiting schemes…) with deadlines, eligibility and links
- suggested PhD-student / postdoc contacts (labs and institutes only), each with a conversation opener
- sources for every entry

You can sort, filter and search everything, and keep notes, a status, a priority, a follow-up date and a contact log next to each entry. The weekly refresh never touches your notes.

## Setup

1. **GitHub repo → Settings → Secrets and variables → Actions → Secrets.** Add:

   | Secret | Value |
   |---|---|
   | `CLAUDE_TOKEN` | Either a subscription token (run `claude setup-token` in a terminal with Claude Code installed; it starts with `sk-ant-oat`) or an API key. The job detects which one you gave it. |
   | `CLOUDFLARE_API_TOKEN` | Cloudflare → My Profile → API Tokens → Create Token → Custom, with **Account › Cloudflare Pages › Edit** and **Account › D1 › Edit** |
   | `CLOUDFLARE_ACCOUNT_ID` | Cloudflare dashboard → Workers & Pages → right sidebar |
   | `SITE_PASSWORD` | Any password. The site asks for it; any username works. |

   Variables (optional): set `CLAUDE_BASE_URL` if your internal token goes through a gateway URL, and `CLAUDE_MODEL` if the gateway uses a different model name.
2. **Settings → Actions → General → Workflow permissions →** select **Read and write**.
3. **Actions → Deploy → Run workflow.** This creates the Cloudflare database and site and deploys it to `https://outreach-directory.pages.dev`.
4. **Actions → Weekly refresh → Run workflow.** Type `stanford-curtis` under "only" to test one lab first, then run it again with nothing filled in for everything. After that it runs by itself every Monday.

Optional: to enable the "Refresh now" button, add a `REFRESH_TOKEN` secret. Make it a fine-grained GitHub token for this repo with Actions: Read and write.

## How it fits together

```
GitHub Actions (Mon 13:00 UTC)
  └─ scripts/refresh.py ── Claude API (Sonnet + web search) ──> public/data/*.json
       └─ commit ──> scripts/deploy.sh ──> Cloudflare Pages (site) + D1 (your notes)
```

| Path | What it is |
|---|---|
| `config/seeds.yaml` | **Which** labs/companies are listed. Edit this to add or remove entries. |
| `config/profile.yaml` | Your interests. Used for fit scores and openers. |
| `scripts/refresh.py` | The weekly research job. |
| `scripts/deploy.sh` | Creates the Cloudflare resources if missing, then deploys. |
| `public/` | The website (plain HTML/JS, no build step). |
| `functions/` | Site password and notes API. |

## Everyday use

- **Add or remove an entry:** edit `config/seeds.yaml`, then commit. The next refresh fills it in. To refresh it right away, run the workflow with its id under "only". Never change an existing `id`, because your notes are keyed on it.
- **Find more labs:** run the workflow with `discover = true`. Suggestions appear under *What's new → Suggested additions*. Copy the ones you want into `seeds.yaml`.
- **Include Imperial:** set `enabled: true` on the Imperial entry in `seeds.yaml` and add labs there.
- **Change the schedule:** edit the `cron` line in `.github/workflows/refresh.yml`.

## Local development
```bash
npm install
npx wrangler d1 migrations apply outreach-notes --local
npx wrangler pages dev           # http://localhost:8788
CLAUDE_TOKEN=... python3 scripts/refresh.py --only stanford-curtis
```

## Data quality notes

- Everything comes from web search at refresh time. Every entry links its sources, so check a program page before you apply.
- If an entry fails to refresh, the site keeps last week's data and marks the entry under *What's new → needs review*. Broken (404) program links are flagged the same way.
- Suggested contacts come only from public lab and member pages. The script never collects emails. Find contact details on the institution's directory yourself.
- The seed lab list reflects public information as of mid-2026. The first refresh confirms each PI's current affiliation and flags anyone who has moved.

## Cost control
Each entry is one Claude request with up to 8 web searches (`MAX_SEARCHES`). To cut cost, lower `MAX_SEARCHES`, refresh one category at a time (the `category` input), or change the schedule to every two weeks.
