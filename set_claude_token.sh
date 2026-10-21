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

TOKEN=$(python3 - "$LOG" <<'EOF'
import re, sys
raw = open(sys.argv[1], "rb").read().decode("utf-8", "ignore")
text = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07|\r", "", raw)
lines = text.split("\n")
for i, line in enumerate(lines):
    m = re.search(r"sk-ant-oat[A-Za-z0-9_-]+", line)
    if not m:
        continue
    tok = m.group(0)
    for nxt in lines[i + 1:]:
        s = nxt.strip()
        if re.fullmatch(r"[A-Za-z0-9_-]+", s):
            tok += s
        else:
            break
    print(tok)
    break
EOF
)

if [[ -z "$TOKEN" ]]; then
  echo "✗ Δεν βρέθηκε token. Ξανατρέξτε το script."; exit 1
fi
echo "Έλεγχος ότι το token δουλεύει..."
if ! CLAUDE_CODE_OAUTH_TOKEN="$TOKEN" $CLAUDE -p "Reply with OK only" >/dev/null 2>&1; then
  echo "✗ Το token δεν δουλεύει. Ξανατρέξτε το script."; exit 1
fi
print -n "$TOKEN" | $GH secret set CLAUDE_CODE_OAUTH_TOKEN -R $REPO
unset TOKEN
echo "OK - token saved (ελέγχθηκε και δουλεύει)"
