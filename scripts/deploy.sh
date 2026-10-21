#!/usr/bin/env bash
# One-shot Cloudflare setup + deploy. Safe to run every time:
# creates the D1 notes database and the Pages project if they don't exist yet,
# applies migrations, pushes site secrets, then deploys public/.
set -euo pipefail

PROJECT="${CF_PAGES_PROJECT:-outreach-directory}"
DB="outreach-notes"
W="npx --no-install wrangler"

: "${CLOUDFLARE_API_TOKEN:?Missing GitHub secret CLOUDFLARE_API_TOKEN}"
: "${CLOUDFLARE_ACCOUNT_ID:?Missing GitHub secret CLOUDFLARE_ACCOUNT_ID}"

db_id() {
  $W d1 list --json 2>/dev/null | python3 -c '
import sys, json
raw = sys.stdin.read(); raw = raw[raw.find("["):]
for d in json.loads(raw or "[]"):
    if d.get("name") == "'"$DB"'":
        print(d.get("uuid") or d.get("id") or ""); break'
}

echo "==> D1 database"
ID="$(db_id || true)"
if [ -z "$ID" ]; then
  $W d1 create "$DB"
  ID="$(db_id)"
fi
[ -n "$ID" ] || { echo "Could not find or create D1 database"; exit 1; }
sed -i "s/REPLACE_WITH_YOUR_D1_DATABASE_ID/$ID/" wrangler.toml
$W d1 migrations apply "$DB" --remote

echo "==> Pages project"
$W pages project create "$PROJECT" --production-branch=main >/dev/null 2>&1 || true

echo "==> Site settings"
put() { printf '%s' "$2" | $W pages secret put "$1" --project-name "$PROJECT" >/dev/null && echo "   set $1"; }
[ -n "${SITE_PASSWORD:-}" ]  && put SITE_PASSWORD "$SITE_PASSWORD"
[ -n "${ALLOWED_EMAILS:-}" ] && put ALLOWED_EMAILS "$ALLOWED_EMAILS"
[ -n "${GITHUB_REPOSITORY:-}" ] && put GITHUB_REPO "$GITHUB_REPOSITORY"
[ -n "${REFRESH_TOKEN:-}" ] && put GITHUB_TOKEN "$REFRESH_TOKEN"

echo "==> Deploy"
$W pages deploy --project-name="$PROJECT" --branch=main --commit-dirty=true
