#!/usr/bin/env bash
# One-shot Cloudflare setup + deploy. Safe to run every time:
# creates the D1 notes database and the Pages project if they don't exist yet,
# applies migrations, pushes site secrets, then deploys public/.
set -euo pipefail
set -o pipefail

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

SUMMARY="${GITHUB_STEP_SUMMARY:-/dev/null}"
echo "==> Cloudflare account"
$W whoami 2>&1 | grep -v -i "token\|email" | tee /tmp/whoami.txt || true
ACCT_NAME="$(grep -oE "│ [^│]+ │ ${CLOUDFLARE_ACCOUNT_ID} │" /tmp/whoami.txt | head -1 | cut -d'│' -f2 | xargs || true)"
echo "### Cloudflare deploy" >> "$SUMMARY"
echo "- Account: **${ACCT_NAME:-unknown}** (ID ends in \`${CLOUDFLARE_ACCOUNT_ID: -6}\`)" >> "$SUMMARY"

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
if $W pages project list 2>/dev/null | grep -q " $PROJECT "; then
  echo "   project exists"
else
  $W pages project create "$PROJECT" --production-branch=main
fi

echo "==> Site settings"
put() { printf '%s' "$2" | $W pages secret put "$1" --project-name "$PROJECT" >/dev/null && echo "   set $1"; }
[ -n "${SITE_PASSWORD:-}" ]  && put SITE_PASSWORD "$SITE_PASSWORD"
[ -n "${ALLOWED_EMAILS:-}" ] && put ALLOWED_EMAILS "$ALLOWED_EMAILS"
[ -n "${GITHUB_REPOSITORY:-}" ] && put GITHUB_REPO "$GITHUB_REPOSITORY"
[ -n "${REFRESH_TOKEN:-}" ] && put GITHUB_TOKEN "$REFRESH_TOKEN"

echo "==> Deploy"
$W pages deploy --project-name="$PROJECT" --branch=main --commit-dirty=true 2>&1 | tee /tmp/deploy.txt
URL="$($W pages project list 2>/dev/null | grep " $PROJECT " | grep -oE "[a-z0-9.-]+\.pages\.dev" | head -1 || true)"
echo "- D1 database: \`$DB\` (${ID})" >> "$SUMMARY"
echo "- Pages project: \`$PROJECT\`" >> "$SUMMARY"
echo "- Site: https://${URL:-$PROJECT.pages.dev}" >> "$SUMMARY"
grep -q "Deployment complete" /tmp/deploy.txt || { echo "- **Deploy did not report success**" >> "$SUMMARY"; exit 1; }
