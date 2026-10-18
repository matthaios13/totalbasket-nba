#!/bin/zsh
# Asks for the three keys (hidden input) and stores them as GitHub secrets. Nothing is saved on disk.
GH=~/.local/bin/gh
REPO=matthaios13/totalbasket-nba
echo "1/3  Όνομα χρήστη WordPress (αυτό με το οποίο μπαίνετε στο totalbasket.gr/wp-admin):"
read WPU
print -n "$WPU" | $GH secret set WP_USER -R $REPO
echo "2/3  Application Password του WordPress (επικόλληση, δεν φαίνεται):"
read -s WPP; echo
print -n "$WPP" | $GH secret set WP_APP_PASSWORD -R $REPO
echo "3/3  Claude token από την εντολή 'claude setup-token' (επικόλληση, δεν φαίνεται):"
read -s CT; echo
print -n "$CT" | $GH secret set CLAUDE_CODE_OAUTH_TOKEN -R $REPO
unset WPP CT
echo "Έτοιμο ✔"
