#!/bin/zsh
# Stores the password of info@totalbasket.gr as a GitHub secret for press.py (hidden input, nothing saved on disk).
GH=~/.local/bin/gh
REPO=matthaios13/totalbasket-nba
echo "Κωδικός του info@totalbasket.gr (Hostinger email) — επικόλληση, δεν φαίνεται:"
read -s P; echo
for i in 1 2 3 4 5; do
  if print -n "$P" | $GH secret set IMAP_PASSWORD -R $REPO; then unset P; echo "Έτοιμο ✔"; exit 0; fi
  echo "Το GitHub δεν απάντησε, ξαναδοκιμάζω σε 15\""; sleep 15
done
unset P
echo "ΑΠΕΤΥΧΕ ✘ (το GitHub δεν απαντά) — τρέξτε το ξανά σε λίγο"; exit 1
