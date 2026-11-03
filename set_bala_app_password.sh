#!/bin/bash
# Stores bala24's WordPress application password as GitHub secrets for the "App images" job.
# The password is typed here (hidden) and goes straight to GitHub; it is never shown or saved on disk.
set -e
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
REPO=matthaios13/totalbasket-nba
read -r -p "bala24 WordPress username (e.g. Bala24 Team's login name): " U
read -r -s -p "Application password (paste, hidden): " P; echo
[ -n "$U" ] && [ -n "$P" ] || { echo "Nothing entered."; exit 1; }
printf '%s' "$U" | gh secret set BALA_WP_USER -R "$REPO"
printf '%s' "$P" | gh secret set BALA_WP_APP_PASSWORD -R "$REPO"
unset P
echo "Done: BALA_WP_USER and BALA_WP_APP_PASSWORD are set."
