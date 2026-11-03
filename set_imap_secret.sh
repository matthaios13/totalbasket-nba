#!/bin/zsh
# Stores the password of info@totalbasket.gr as a GitHub secret for press.py (hidden input, nothing saved on disk).
GH=~/.local/bin/gh
REPO=matthaios13/totalbasket-nba
echo "Κωδικός του info@totalbasket.gr (Hostinger email) — επικόλληση, δεν φαίνεται:"
read -s P; echo
print -n "$P" | $GH secret set IMAP_PASSWORD -R $REPO
unset P
echo "Έτοιμο ✔"
