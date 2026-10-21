#!/bin/zsh
# Creates a Claude token, captures it from the screen output (both lines), checks it works,
# and stores it as the GitHub secret. The token is never printed or kept on disk.
CLAUDE=~/.local/bin/claude
GH=~/.local/bin/gh
REPO=matthaios13/totalbasket-nba
LOG=$(mktemp)
trap 'rm -f "$LOG"' EXIT

echo "Εγκρίνετε στον browser που θα ανοίξει (Authorize)..."
script -q "$LOG" $CLAUDE setup-token; clear

TOKEN=$(python3 "${0:A:h}/extract_token.py" "$LOG")

if [[ -z "$TOKEN" ]]; then
  echo "✗ Δεν βρέθηκε token. Ξανατρέξτε το script."; exit 1
fi
echo "Έλεγχος ότι το token δουλεύει..."
echo "  (μήκος token: ${#TOKEN}, αρχή: ${TOKEN[1,14]})"
OUT=$(cd /tmp && env -u ANTHROPIC_API_KEY CLAUDE_CODE_OAUTH_TOKEN="$TOKEN" $CLAUDE -p "Reply with OK only" --output-format json 2>&1)
if [[ "$OUT" != *'"is_error":false'* ]]; then
  echo "✗ Το token δεν δουλεύει. Μήνυμα:"
  print -r -- "$OUT" | python3 -c 'import sys,json,re
t=sys.stdin.read()
try: d=json.loads(t); print(d.get("result"), d.get("api_error_status"))
except Exception: print(re.sub(r"sk-ant-[A-Za-z0-9_-]+","[token]",t)[-600:])'
  exit 1
fi
print -n "$TOKEN" | $GH secret set CLAUDE_CODE_OAUTH_TOKEN -R $REPO
unset TOKEN
echo "OK - token saved (ελέγχθηκε και δουλεύει)"
