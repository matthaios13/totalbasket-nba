#!/usr/bin/env python3
"""Totalbasket news bot.

Every run:
  1. finds finished NBA games (ESPN) that we have not covered -> one article per game
  2. finds new NBA news (ESPN, RealGM) and news from the Italian, Spanish and French leagues
     (see sources.FEEDS)
  3. has Claude write an original Greek article for each, picks a featured image,
     and publishes it to totalbasket.gr in the league's category.

Env:
  WP_URL (default https://totalbasket.gr), WP_USER, WP_APP_PASSWORD
  WP_STATUS   publish | draft          (default publish)
  DRY_RUN=1   write articles to ./out instead of WordPress
  CLAUDE_MODEL (default sonnet) writes, TRIAGE_MODEL (default haiku) pre-screens news; MAX_POSTS (default 10)
  CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY  (used by the claude CLI)
"""
import base64
import datetime as dt
import html
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

import boxscore
import card
import euro
import photos
import sources
from sources import LEAGUES
from teams import TEAMS, colours, greek, logo_url

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "state.json")
ESPN = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
ESPN_CONTENT = "https://content.core.api.espn.com/v1/sports/news/"

WP_URL = os.environ.get("WP_URL", "https://totalbasket.gr").rstrip("/")
WP_STATUS = os.environ.get("WP_STATUS", "publish")
DRY_RUN = os.environ.get("DRY_RUN") == "1"
# the bot runs on the owner's Claude subscription and shares its weekly limit: Opus for every item
# used it up in two days (2026-10-23), so a cheap model screens news first and Sonnet writes
MODEL = os.environ.get("CLAUDE_MODEL", "sonnet")
TRIAGE_MODEL = os.environ.get("TRIAGE_MODEL", "haiku")
# plain `claude -p` sends Claude Code's whole system prompt + tool list (~32k tokens) with every call;
# these flags cut a call to ~1k tokens plus the prompt (measured 2026-10-24)
LEAN = ["--max-turns", "1", "--tools", "", "--strict-mcp-config", "--setting-sources", "",
        "--system-prompt", "Είσαι συντάκτης ελληνικού αθλητικού site. Απαντάς μόνο με ένα JSON αντικείμενο.",
        "--effort", "low"]


def claude_cmd(model):
    return ["claude", "-p", "--output-format", "json", "--model", model, *LEAN]
MAX_POSTS = int(os.environ.get("MAX_POSTS", "10"))
ONLY = set(filter(None, os.environ.get("ONLY_LEAGUES", "").split(",")))   # test runs: e.g. "lba,acb,lnb"
GAMES_ONLY = os.environ.get("GAMES_ONLY") == "1"   # the 10-minute game-night job: results only, no news feeds
NBA_CATEGORY = 6
NBA_TAG = 153
TAG_2ND_TOP = 113               # the run's most important article
TAG_3RD_TOP = 114               # every other NBA article; European news only when important
EUROPE_TOP_IMPORTANCE = 7       # European league news gets 3rdTOP from this importance up
TAG_TOP = 112                   # main headline: EuroLeague results of Panathinaikos / Olympiacos
GREEK_EL = re.compile(r"(?i)panathinaikos|olympiacos")
GREEK_EC = re.compile(r"(?i)\baris\b|\bpaok\b")


def euro_top_tag(league, teams=None):
    """User's rules (2026-10-27): EuroLeague result of Panathinaikos/Olympiacos -> TOP (both get it, in the
    order the games end); every other EuroLeague article -> 2ndTOP; EuroCup -> 3rdTOP, except Aris / PAOK
    results -> 2ndTOP."""
    names = " ".join(teams or [])
    if league == "el":
        return TAG_TOP if teams and GREEK_EL.search(names) else TAG_2ND_TOP
    return TAG_2ND_TOP if teams and GREEK_EC.search(names) else TAG_3RD_TOP
SKIP_SEASON_TYPES = {1, 4}       # preseason, all-star
RECAP_WAIT_HOURS = 3             # wait this long for the AP recap (it carries the quotes)
EURO_WAIT_HOURS = float(os.environ.get("EURO_WAIT_HOURS", "0"))   # user wants the result article right after the game
NEWS_MAX_AGE_HOURS = 30
MAX_ATTEMPTS = 3

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"}


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def now():
    return dt.datetime.now(dt.timezone.utc)


def retry(fn, tries=4):
    """GitHub's runners sometimes can't reach a host for a moment ("Network is unreachable")."""
    for i in range(tries):
        try:
            return fn()
        except urllib.error.HTTPError:
            raise
        except (urllib.error.URLError, OSError, TimeoutError):
            if i == tries - 1:
                raise
            time.sleep(5 * (i + 1))


def get_json(url):
    req = urllib.request.Request(url, headers=UA)

    def go():
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    return retry(go)


def strip_html(s):
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", s or "", flags=re.S)
    s = re.sub(r"<br\s*/?>|</p>", "\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n\n", s)).strip()


# ---------------------------------------------------------------- state

def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {"games": {}, "news": {}, "badges": {}}


def save_state(state):
    cutoff = (now() - dt.timedelta(days=14)).isoformat()
    for k in ("games", "news"):
        state[k] = {i: v for i, v in state[k].items() if v.get("seen", "") >= cutoff}
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=1, sort_keys=True)


# ---------------------------------------------------------------- ESPN: games

def finished_games():
    today = now()
    seen = set()
    for delta in (2, 1, 0):
        day = (today - dt.timedelta(days=delta)).strftime("%Y%m%d")
        for e in get_json(f"{ESPN}/scoreboard?dates={day}").get("events", []):
            if e["id"] in seen:
                continue
            seen.add(e["id"])
            comp = e["competitions"][0]
            if comp["status"]["type"].get("completed") and e["season"].get("type") not in SKIP_SEASON_TYPES:
                yield e


def player_lines(box_team):
    stats = box_team["statistics"][0]
    names = stats["names"]
    out = []
    for a in stats["athletes"]:
        if a.get("didNotPlay") or not a.get("stats"):
            continue
        s = dict(zip(names, a["stats"]))
        out.append({"player": a["athlete"]["displayName"], "starter": a.get("starter", False), **s})
    out.sort(key=lambda p: int(p.get("PTS") or 0), reverse=True)
    return out


def game_facts(event):
    s = get_json(f"{ESPN}/summary?event={event['id']}")
    comp = s["header"]["competitions"][0]
    teams = []
    for c in comp["competitors"]:
        t = c["team"]
        teams.append({
            "espn_id": t["id"],
            "name_en": t["displayName"],
            "name_gr": greek(t["id"], t["displayName"]),
            "city_gr": TEAMS.get(t["id"], ("", t["location"]))[1],
            "home_away": c["homeAway"],
            "score": c.get("score"),
            "winner": c.get("winner", False),
            "record": (c.get("record") or [{}])[0].get("summary") if c.get("record") else None,
            "quarters": [l.get("displayValue") for l in c.get("linescores", [])],
        })
    box = {}
    lines = {}
    for bt in s.get("boxscore", {}).get("players", []):
        name = greek(bt["team"]["id"], bt["team"]["displayName"])
        box[name] = player_lines(bt)[:8]
        lines[bt["team"]["id"]] = [{"name": p["player"], "pts": int(p.get("PTS") or 0),
                                    "threes": int(str(p.get("3PT") or "0").split("-")[0] or 0)}
                                   for p in player_lines(bt)]
    for t in teams:
        t["players"] = lines.get(t["espn_id"], [])
    team_stats = {}
    for bt in s.get("boxscore", {}).get("teams", []):
        team_stats[greek(bt["team"]["id"], bt["team"]["displayName"])] = {
            st["label"]: st["displayValue"] for st in bt.get("statistics", [])}
    art = s.get("article") or {}
    return {
        "type": "game",
        "competition": "NBA " + ({2: "κανονική περίοδος", 3: "playoffs", 5: "play-in"}
                                 .get(event["season"].get("type"), "")),
        "date_utc": event["date"],
        "venue": (s.get("gameInfo", {}).get("venue") or {}).get("fullName"),
        "status": comp["status"]["type"].get("detail"),
        "teams": teams,
        "box_score_top_players": box,
        "team_stats": team_stats,
        "recap_en": strip_html(art.get("story", ""))[:9000],
    }, bool(art.get("story"))


# ---------------------------------------------------------------- ESPN: news

def news_items():
    for a in get_json(f"{ESPN}/news?limit=40").get("articles", []):
        if a.get("type") not in ("HeadlineNews", "Story"):
            continue
        href = a.get("links", {}).get("api", {}).get("self", {}).get("href", "")
        if "/sports/news/" not in href:
            continue
        yield a


def news_facts(item):
    full = get_json(ESPN_CONTENT + str(item["id"]))["headlines"][0]
    team_ids = [str(c.get("teamId") or c.get("team", {}).get("id"))
                for c in full.get("categories", []) if c.get("type") == "team"]
    return {
        "type": "news",
        "published_utc": item.get("published"),
        "headline_en": full.get("headline"),
        "description_en": full.get("description"),
        "teams_gr": [greek(t) for t in team_ids if greek(t)],
        "story_en": strip_html(full.get("story", ""))[:9000],
    }, (team_ids[0] if team_ids else None)


# ---------------------------------------------------------------- Claude

STYLE = """Είσαι συντάκτης του ελληνικού site μπάσκετ totalbasket.gr και γράφεις για το NBA και τα ευρωπαϊκά πρωταθλήματα.

Κανόνες:
- Γράφεις ΠΡΩΤΟΤΥΠΟ άρθρο στα ελληνικά, με δικά σου λόγια. ΔΕΝ μεταφράζεις παράγραφο-παράγραφο το ξένο κείμενο (αγγλικό, ιταλικό, ισπανικό ή γαλλικό).
- Χρησιμοποιείς ΜΟΝΟ γεγονότα, αριθμούς και δηλώσεις που υπάρχουν στα δεδομένα. Δεν εφευρίσκεις τίποτα.
- Για στατιστικά παικτών, αν διαφέρει το recap από το box score, ισχύει το box score.
- Δηλώσεις προπονητών/παικτών: μεταφράζεις στα ελληνικά, μέσα σε «εισαγωγικά», με σαφή απόδοση (ποιος το είπε). Διάλεξε τις 1-3 πιο ενδιαφέρουσες.
- Ονόματα παικτών/προπονητών με ελληνικούς χαρακτήρες όπως τα γράφουν τα ελληνικά αθλητικά site (π.χ. Λεμπρόν Τζέιμς, Νίκολα Γιόκιτς, Βίκτορ Ουεμπανιάμα, Σάι Γκίλτζιους-Αλεξάντερ, Γιάννης Αντετοκούνμπο).
- NBA ομάδες με το ελληνικό τους παρατσούκλι όπως δίνεται στα δεδομένα (Λέικερς, Σέλτικς, Νικς ...). Εναλλακτικά για ποικιλία η πόλη (π.χ. «το Λος Άντζελες», «η Βοστώνη»).
- Ευρωπαϊκές ομάδες όπως τις γράφει ο ελληνικός αθλητικός τύπος (π.χ. Ρεάλ Μαδρίτης, Μπαρτσελόνα, Μπασκόνια, Βαλένθια, Μάλαγα, Βίρτους Μπολόνια, Αρμάνι Μιλάνο, Νάπολι, Μάξιμα Ρόμα, Παρί, Βιλερμπάν, Μονακό, Λε Μαν).
- Αν συμμετέχει Έλληνας παίκτης ή προπονητής, τον αναδεικνύεις.
- Ύφος ζωντανό, δημοσιογραφικό, όπως ένα ελληνικό αθλητικό site. Όροι όπως triple-double, double-double, rookie, clutch μένουν στα αγγλικά.
- Μορφή κειμένου: 4-6 παράγραφοι, HTML μόνο με <p class="wp-block-paragraph">, <strong> (λίγο, για τα πιο σημαντικά) και <blockquote class="wp-block-quote"><p>...</p></blockquote> αν θες για μία δήλωση. Τίποτα άλλο.
- Τίτλος: σύντομος, πιασάρικος, ελληνικός, χωρίς τελεία, χωρίς emoji. Μπορεί να έχει δήλωση σε «» ή θαυμαστικό όπως ο ελληνικός αθλητικός τύπος.
- Μην αναφέρεις ESPN, AP ή άλλο μέσο ως πηγή του κειμένου.
- Αν το κείμενο της πηγής είναι πολύ σύντομο, γράψε σύντομη είδηση (2-3 παράγραφοι) μόνο με όσα υπάρχουν· μην «φουσκώνεις».
- Αν ένα όνομα υπάρχει ήδη γραμμένο στους τίτλους του "already_on_site", χρησιμοποίησε ΑΚΡΙΒΩΣ την ίδια ελληνική γραφή.
- Τίτλος και κείμενο ΜΟΝΟ με ελληνικούς χαρακτήρες στα ονόματα (ποτέ μισό ελληνικά μισό λατινικά, π.χ. «Εμπίντ» και όχι «Εμπίid»).
- photo_person: αν το άρθρο αφορά κυρίως ΕΝΑ πρόσωπο (παίκτη/προπονητή/στέλεχος), το πλήρες όνομά του στα ΑΓΓΛΙΚΑ (π.χ. "Joel Embiid"), αλλιώς "".
- main_team: η ομάδα που αφορά κυρίως το άρθρο, με το επίσημο λατινικό της όνομα (π.χ. "Real Madrid Baloncesto", "Olimpia Milano", "Paris Basketball"), αλλιώς "".
- importance: 1-10 πόσο σημαντικό/ελκυστικό είναι για Έλληνα αναγνώστη (σούπερ σταρ, Έλληνες παίκτες, μεγάλες μεταγραφές = υψηλά).

Απαντάς ΜΟΝΟ με ένα JSON αντικείμενο, χωρίς τίποτα άλλο:
{"publish": true, "reason": "", "title": "...", "excerpt": "1-2 προτάσεις", "content": "<p ...>...</p>", "people": ["ελληνικά ονόματα των 1-4 κύριων προσώπων"], "photo_person": "", "main_team": "", "importance": 5}"""

STATS_NOTE = """
Τα δεκάλεπτα και οι γραμμές στατιστικών των ομάδων προστίθενται ΑΥΤΟΜΑΤΑ στο τέλος· μην τα γράψεις εσύ ως λίστα.
Δώσε επιπλέον στο JSON:
"names_gr": {"<όνομα όπως στα δεδομένα>": "<ελληνικό ΕΠΩΝΥΜΟ>"} για ΟΛΟΥΣ τους παίκτες που αγωνίστηκαν και τους προπονητές,
"teams_gr": {"<όνομα ομάδας όπως στα δεδομένα>": "<ελληνικό όνομα ομάδας όπως το γράφει ο ελληνικός τύπος>"}."""

GAME_TASK = """Γράψε το άρθρο για τον αγώνα. Στην αρχή το τελικό σκορ και ο πρωταγωνιστής, μετά η εξέλιξη (περίοδοι, κρίσιμο σημείο),
οι κορυφαίοι και των δύο ομάδων με αριθμούς, οι δηλώσεις αν υπάρχουν στο recap, και τα ρεκόρ των ομάδων. Το publish είναι πάντα true.""" + STATS_NOTE

EURO_GAME_TASK = """Γράψε το άρθρο για τον αγώνα ({comp}). Τίτλος στο ύφος των ελληνικών site, π.χ. «Γηπεδούχος – Φιλοξενούμενη 85-80: ...»
(πρώτα η γηπεδούχος). Στην αρχή το τελικό σκορ και ο πρωταγωνιστής, μετά η εξέλιξη με βάση τα δεκάλεπτα (ποια ομάδα προηγήθηκε,
πού κρίθηκε), οι κορυφαίοι και των δύο ομάδων με αριθμούς (πόντοι, ριμπάουντ, ασίστ, PIR). Δεν υπάρχουν δηλώσεις στα δεδομένα:
ΜΗΝ γράψεις δηλώσεις. 3-5 παράγραφοι. Το publish είναι πάντα true.""" + STATS_NOTE


def stats_block(facts, art, team_name=lambda t: t["name_gr"]):
    """Quarter-by-quarter score and each team's points line, numbers straight from the data."""
    names = art.get("names_gr") or {}
    side = {}
    for t in facts["teams"]:
        side[t["home_away"]] = {
            "team": team_name(t),
            "coach": boxscore.greek(names, t["coach"]) if t.get("coach") else None,
            "partials": t["quarters"],
            "players": [{**p, "name": boxscore.greek(names, p["name"])} for p in t.get("players", [])],
        }
    if not side["home"]["players"] or not side["away"]["players"]:
        return ""
    return boxscore.block(side["home"], side["away"])

LEAGUE_TASK = """Η είδηση αφορά το {about}. Αξιολόγησέ την. Βάλε publish=false (με reason) αν: δεν αφορά την πρώτη κατηγορία
αυτού του πρωταθλήματος (π.χ. Serie A2, LEB Oro, Élite 2, γυναικείο, εφηβικό), αν είναι live/ενδιάμεσο σκορ, διαφήμιση, τηλεοπτικό πρόγραμμα,
γνώμη/ανάλυση χωρίς είδηση, ή πολύ ασήμαντο. Βάλε publish=true για αποτελέσματα με ουσία, δηλώσεις, μεταγραφές, τραυματισμούς,
απολύσεις/προσλήψεις προπονητών, τιμωρίες, οικονομικά θέματα συλλόγων και ειδήσεις για Έλληνες παίκτες/προπονητές.
ΣΗΜΑΝΤΙΚΟ: στο "already_on_site" είναι οι τίτλοι που υπάρχουν ήδη στο site. Βάλε publish=false αν η είδηση καλύπτει το ίδιο θέμα
(π.χ. το ίδιο αποτέλεσμα αγώνα, την ίδια μεταγραφή). Αν υπάρχει ήδη άρθρο με δηλώσεις του ίδιου προσώπου από τις τελευταίες
ημέρες (π.χ. ίδια συνέντευξη με άλλη γωνία), βάλε επίσης publish=false· μία συνέντευξη = ένα άρθρο."""

NEWS_TASK = """Αξιολόγησε την είδηση. Βάλε publish=false (με reason) αν είναι: fantasy/στοιχηματική ανάλυση, λίστα/ranking/γνώμη αρθρογράφου,
βίντεο χωρίς ουσία, θέμα εκτός NBA, ή κάτι πολύ ασήμαντο. Βάλε publish=true για δηλώσεις προπονητών/παικτών, τραυματισμούς, μεταγραφές/ανταλλαγές/
συμβόλαια/απολύσεις, ρεκόρ, τιμωρίες και κάθε είδηση με ειδησεογραφική αξία, και γράψε το άρθρο.
ΣΗΜΑΝΤΙΚΟ: στο "already_on_site" είναι οι τίτλοι που υπάρχουν ήδη στο site. Βάλε publish=false αν η είδηση καλύπτει το ίδιο θέμα
(π.χ. είναι ουσιαστικά αποτέλεσμα/περίληψη αγώνα που ήδη υπάρχει, ή roundup αποτελεσμάτων). Δηλώσεις μετά από αγώνα με δικό τους θέμα επιτρέπονται."""


class AuthError(Exception):
    """Claude or WordPress rejected our key: stop the run, don't burn the item's attempts."""


class QuotaError(AuthError):
    """Claude usage limit reached: stop quietly, the next hourly run tries again."""


MIXED_SCRIPT = re.compile(r"[\u0370-\u03ff\u1f00-\u1fff][A-Za-z]|[A-Za-z][\u0370-\u03ff\u1f00-\u1fff]")


def ask_claude(task, facts):
    for attempt in range(2):
        art = _ask_claude(task, facts, retry_note=attempt > 0)
        if not art.get("publish") or not MIXED_SCRIPT.search(art["title"] + art.get("excerpt", "")):
            return art
        log("  mixed Greek/Latin title, rewriting:", art["title"])
    raise RuntimeError("title keeps mixing Greek and Latin letters")


def _claude(prompt, model):
    r = subprocess.run(
        claude_cmd(model),
        input=prompt, capture_output=True, text=True, timeout=600)
    if '"api_error_status":429' in r.stdout or "hit your" in r.stdout.lower():
        msg = re.search(r'"result":"([^"]*)"', r.stdout)
        raise QuotaError(f"Claude usage limit: {msg.group(1) if msg else 'rate limited'}")
    if '"api_error_status":401' in r.stdout or "Failed to authenticate" in r.stdout:
        raise AuthError("Claude token rejected - set CLAUDE_CODE_OAUTH_TOKEN again")
    if r.returncode != 0:
        raise RuntimeError(f"claude failed: {r.stderr[-800:] or r.stdout[-800:]}")
    text = json.loads(r.stdout).get("result", "")
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise RuntimeError(f"no JSON in answer: {text[:300]}")
    return json.loads(m.group(0))


TRIAGE = """Είσαι αρχισυντάκτης του ελληνικού site μπάσκετ totalbasket.gr. Θέμα της ενότητας: {about}. {note}
Αποφάσισε ΜΟΝΟ από τίτλο και περίληψη αν αξίζει άρθρο. ΟΧΙ για: fantasy/στοίχημα, λίστες/rankings, γνώμη χωρίς είδηση,
live/ενδιάμεσα σκορ, τηλεοπτικό πρόγραμμα, κατώτερες κατηγορίες/γυναικείο/εφηβικό, πολύ ασήμαντα, ή θέμα που ήδη υπάρχει
στους τίτλους "already_on_site" (ίδιο αποτέλεσμα, ίδια μεταγραφή, ίδια συνέντευξη). ΝΑΙ για δηλώσεις, τραυματισμούς,
μεταγραφές/συμβόλαια/απολύσεις, ρεκόρ, τιμωρίες, ειδήσεις για Έλληνες.
Απαντάς ΜΟΝΟ JSON: {{"publish": true/false, "reason": "σύντομα"}}

ΔΕΔΟΜΕΝΑ:
{data}"""


def triage(about, headline, summary, on_site, note=""):
    """Cheap first pass on headline + summary, so the expensive writer only sees likely articles."""
    data = json.dumps({"headline": headline, "summary": (summary or "")[:700],
                       "already_on_site": on_site[:120]}, ensure_ascii=False, indent=1)
    return _claude(TRIAGE.format(about=about, note=note, data=data), TRIAGE_MODEL)


def _ask_claude(task, facts, retry_note=False):
    prompt = f"{STYLE}\n\n{task}\n\nΔΕΔΟΜΕΝΑ (JSON):\n{json.dumps(facts, ensure_ascii=False, indent=1)}"
    if retry_note:
        prompt += "\n\nΠΡΟΣΟΧΗ: η προηγούμενη απάντηση είχε λέξη με ανακατεμένα ελληνικά και λατινικά γράμματα. Γράψ' τα όλα σωστά."
    art = _claude(prompt, MODEL)
    if art.get("publish") and not (art.get("title") and art.get("content")):
        raise RuntimeError("answer missing title/content")
    return art


# ---------------------------------------------------------------- WordPress

def wp(method, path, data=None, headers=None, raw=None):
    auth = base64.b64encode(f'{os.environ["WP_USER"]}:{os.environ["WP_APP_PASSWORD"]}'.encode()).decode()
    h = {"Authorization": "Basic " + auth, **UA, **(headers or {})}
    body = raw
    if data is not None:
        body = json.dumps(data).encode()
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(f"{WP_URL}/wp-json/wp/v2/{path}", data=body, headers=h, method=method)
    def go():
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    try:
        return retry(go)
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise AuthError(f"WordPress rejected WP_USER / WP_APP_PASSWORD ({method} {path})")
        raise RuntimeError(f"WordPress {method} {path}: {e.code} {e.read()[:400]!r}")


_tag_cache = {}


def find_tags(names, base_tag=NBA_TAG):
    """Only reuse tags that already exist on the site (exact name match) — never create new ones."""
    ids = [base_tag]
    for n in names:
        n = (n or "").strip()
        if not n:
            continue
        if n not in _tag_cache:
            res = get_json(f"{WP_URL}/wp-json/wp/v2/tags?search={urllib.parse.quote(n)}&_fields=id,name")
            _tag_cache[n] = next((t["id"] for t in res if html.unescape(t["name"]).lower() == n.lower()), None)
        if _tag_cache[n] and _tag_cache[n] not in ids:
            ids.append(_tag_cache[n])
    return ids


def publish(art, image_path, slug_hint, league="nba"):
    if DRY_RUN:
        os.makedirs(os.path.join(HERE, "out"), exist_ok=True)
        base = os.path.join(HERE, "out", slug_hint)
        with open(base + ".html", "w") as f:
            f.write(f"<h1>{html.escape(art['title'])}</h1><p><img src='{os.path.basename(image_path)}' width=600></p>"
                    f"<p><em>{html.escape(art.get('excerpt', ''))}</em></p>{art['content']}"
                    f"<p>tags: {html.escape(', '.join(art.get('people', [])))}</p>")
        os.replace(image_path, base + ".jpg")
        return {"id": "dry-run", "link": base + ".html"}
    with open(image_path, "rb") as f:
        media = wp("POST", "media", raw=f.read(), headers={
            "Content-Type": "image/jpeg",
            "Content-Disposition": f'attachment; filename="{slug_hint}.jpg"'})
    wp("POST", f"media/{media['id']}", {"alt_text": art["title"], "caption": art.get("_caption", "")})
    lg = LEAGUES[league]
    tags = find_tags(art.get("people", []) + art.get("_teams", []), lg["tag"])
    if league in ("el", "ec"):
        tags.append(euro_top_tag(league, art.get("_result_teams")))
    elif league == "nba" or int(art.get("importance") or 0) >= EUROPE_TOP_IMPORTANCE:
        tags.append(TAG_3RD_TOP)
    categories = lg["categories"]
    return wp("POST", "posts", {
        "title": art["title"], "content": art["content"], "excerpt": art.get("excerpt", ""),
        "status": WP_STATUS, "categories": categories, "tags": tags,
        "featured_media": media["id"]})


# ---------------------------------------------------------------- main

def person_image(art):
    """Free-licence photo of the article's main person; adds the required credit line to the text."""
    name = (art.get("photo_person") or "").strip()
    if not name:
        return None
    try:
        found = photos.find(name)
    except Exception as ex:
        log("  photo lookup failed:", name, ex)
        return None
    if not found:
        log("  no free photo for", name)
        return None
    img, credit = found
    credit = re.sub(r"\s+", " ", credit)[:200]
    art["content"] = re.sub(r'\s*<p class="wp-block-paragraph"><em>Φωτογραφία:.*?</em></p>', "", art["content"])
    art["content"] += f'\n\n<p class="wp-block-paragraph"><em>Φωτογραφία: {html.escape(credit)}</em></p>'
    art["_caption"] = credit
    return card.photo_card(tempfile.mktemp(suffix=".jpg"), img)


_espn_teams = None


def team_image(art, league):
    """White wallpaper with the club's logo; the league's name when no logo is found."""
    team = (art.get("main_team") or "").strip()
    url = None
    if league == "nba":
        global _espn_teams
        if _espn_teams is None:
            try:
                data = get_json(f"{ESPN}/teams")["sports"][0]["leagues"][0]["teams"]
                _espn_teams = {_norm(n): t["team"]["id"] for t in data
                               for n in (t["team"]["displayName"], t["team"]["name"], t["team"]["shortDisplayName"])}
            except Exception:
                _espn_teams = {}
        tid = _espn_teams.get(_norm(team)) or next(
            (i for n, i in _espn_teams.items() if team and (_norm(team) in n or n in _norm(team))), None)
        url = logo_url(tid) if tid else None
    else:
        url = sources.badge(league, team)
    path = tempfile.mktemp(suffix=".jpg")
    if url:
        return card.news_card(path, art["title"], logo=url)
    return card.league_card(path, LEAGUES[league]["label"])


def recent_site_posts(category=NBA_CATEGORY):
    """Latest posts of a category (None = whole site), ours and the editors': [{title, date}] newest first."""
    try:
        cat = f"categories={category}&" if category else ""
        res = get_json(f"{WP_URL}/wp-json/wp/v2/posts?{cat}per_page=60&_fields=title,date_gmt")
        return [{"title": html.unescape(p["title"]["rendered"]),
                 "date": dt.datetime.fromisoformat(p["date_gmt"]).replace(tzinfo=dt.timezone.utc)} for p in res]
    except Exception as ex:
        log("could not read site posts:", ex)
        return []


def _norm(s):
    s = unicodedata.normalize("NFD", s.lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def game_on_site(facts, posts):
    """True if the site already has an article about this game's result (same score, or both
    teams in a title published after tip-off)."""
    start = dt.datetime.fromisoformat(facts["date_utc"].replace("Z", "+00:00"))
    a, b = facts["teams"][0], facts["teams"][1]
    score = re.compile(rf"\b({a['score']}\s*[-–—:]\s*{b['score']}|{b['score']}\s*[-–—:]\s*{a['score']})\b")
    names = [_norm(a["name_gr"]), _norm(b["name_gr"])]
    for p in posts:
        if p["date"] < start:
            continue                       # previews written before the game don't count
        t = _norm(p["title"])
        if score.search(t) or all(n in t for n in names):
            log("  already on site:", p["title"])
            return True
    return False


def promote_top(published):
    """The most important article of this run goes to 2ndTOP, the rest stay 3rdTOP."""
    if DRY_RUN or not published:
        return
    best = max(published, key=lambda p: p["importance"])
    tags = [t for t in best["tags"] if t != TAG_3RD_TOP] + [TAG_2ND_TOP]
    wp("POST", f"posts/{best['id']}", {"tags": tags})
    log("2ndTOP:", best["title"])


def run():
    state = load_state()
    try:
        _run(state)
    finally:
        save_state(state)       # also on errors, so published items are never posted twice


def _run(state):
    posted = 0
    published = []
    stamp = now().isoformat()
    site = {lg: recent_site_posts(LEAGUES[lg]["category"]) for lg in LEAGUES}
    site_all = recent_site_posts(None)      # editors often file a story under another category

    def on_site(league):
        """Titles to check for duplicates: this league's latest posts plus the whole site's latest."""
        return list(dict.fromkeys([p["title"] for p in site[league][:60]] + [p["title"] for p in site_all[:60]]))
    site_posts = site["nba"]
    sources._badges.update(state.setdefault("badges", {}))

    def record(res, art, league="nba"):
        if league == "nba":                # 2ndTOP is picked among NBA articles only
            published.append({"id": res["id"], "tags": res.get("tags", []), "title": art["title"],
                              "importance": int(art.get("importance") or 0)})
        site[league].insert(0, {"title": art["title"], "date": now()})
        site_all.insert(0, {"title": art["title"], "date": now()})

    def budget():
        return posted < MAX_POSTS

    # 1. games
    for e in (finished_games() if not ONLY or "nba" in ONLY else []):
        g = state["games"].setdefault(e["id"], {"seen": stamp, "attempts": 0})
        if g.get("done") or g["attempts"] >= MAX_ATTEMPTS or not budget():
            continue
        try:
            facts, has_recap = game_facts(e)
            if game_on_site(facts, site_posts):
                g["done"] = "already on site"
                continue
            first = dt.datetime.fromisoformat(g["seen"])
            if not has_recap and now() - first < dt.timedelta(hours=RECAP_WAIT_HOURS):
                log("waiting for recap:", e["name"])
                continue
            g["attempts"] += 1
            log("writing game:", e["name"])
            art = ask_claude(GAME_TASK, facts)
            away = next(t for t in facts["teams"] if t["home_away"] == "away")
            home = next(t for t in facts["teams"] if t["home_away"] == "home")
            art["_teams"] = [away["name_gr"], home["name_gr"]]
            art["content"] += stats_block(facts, art)
            img = card.game_card(tempfile.mktemp(suffix=".jpg"),
                                 {"name": away["name_gr"], "score": away["score"], "winner": away["winner"],
                                  "colour": colours(away["espn_id"])[0], "logo": logo_url(away["espn_id"])},
                                 {"name": home["name_gr"], "score": home["score"], "winner": home["winner"],
                                  "colour": colours(home["espn_id"])[0], "logo": logo_url(home["espn_id"])})
            res = publish(art, img, f"nba-game-{e['id']}")
            g.update(done=True, post=res["id"])
            record(res, art)
            posted += 1
            log("  ->", res.get("link"))
        except AuthError:
            g["attempts"] -= 1
            raise
        except Exception as ex:  # keep going with the other items
            log("  ERROR", e["name"], ex)

    # 1b. EuroLeague / EuroCup games (official API), published as soon as the game is final; skipped if an
    # editor already posted the same result.
    for league in ("el", "ec"):
        if ONLY and league not in ONLY:
            continue
        try:
            games = euro.finished(league)
        except Exception as ex:
            log("euro games failed:", league, ex)
            continue
        for eg in games:
            gid = f"{euro.season_code(euro.COMPS[league])}-{eg['gameCode']}"
            g = state["games"].setdefault(gid, {"seen": stamp, "attempts": 0})
            if g.get("done") or g["attempts"] >= MAX_ATTEMPTS or not budget():
                continue
            if now() - dt.datetime.fromisoformat(g["seen"]) < dt.timedelta(hours=EURO_WAIT_HOURS):
                continue
            label = f"{eg['local']['club']['name']} - {eg['road']['club']['name']}"
            try:
                facts = euro.facts(league, eg)
                for t in facts["teams"]:
                    t["name_gr"] = t["name"]               # until the writer gives the Greek name
                if game_on_site(facts, site[league] + site_all):
                    g["done"] = "already on site"
                    continue
                g["attempts"] += 1
                log(f"writing {league} game:", label)
                art = ask_claude(EURO_GAME_TASK.format(comp=facts["competition"]), facts)
                teams_gr = art.get("teams_gr") or {}
                for t in facts["teams"]:
                    t["name_gr"] = teams_gr.get(t["name"]) or t["name"]
                if game_on_site(facts, site[league] + site_all):   # now also by Greek team names
                    g["done"] = "already on site"
                    continue
                home = next(t for t in facts["teams"] if t["home_away"] == "home")
                away = next(t for t in facts["teams"] if t["home_away"] == "away")
                art["_teams"] = [home["name_gr"], away["name_gr"]]
                art["_result_teams"] = [home["name"], away["name"]]     # Latin API names, for the TOP rules
                art["content"] += stats_block(facts, art)
                img = card.game_card(tempfile.mktemp(suffix=".jpg"),
                                     {"name": away["name_gr"], "score": away["score"], "winner": away["winner"],
                                      "colour": card.crest_colour(away["crest"]), "logo": away["crest"]},
                                     {"name": home["name_gr"], "score": home["score"], "winner": home["winner"],
                                      "colour": card.crest_colour(home["crest"]), "logo": home["crest"]})
                res = publish(art, img, f"{league}-game-{gid}", league)
                g.update(done=True, post=res["id"])
                record(res, art, league)
                posted += 1
                log("  ->", res.get("link"))
            except AuthError:
                g["attempts"] -= 1
                raise
            except Exception as ex:  # keep going with the other items
                log("  ERROR", label, ex)

    # 2. news
    for item in (news_items() if (not ONLY or "nba" in ONLY) and not GAMES_ONLY else []):
        nid = str(item["id"])
        if nid in state["news"]:
            n = state["news"][nid]
        else:
            n = state["news"][nid] = {"seen": stamp, "attempts": 0}
        pub = dt.datetime.fromisoformat(item["published"].replace("Z", "+00:00"))
        if not n.get("done") and now() - pub > dt.timedelta(hours=NEWS_MAX_AGE_HOURS):
            n["done"] = "too-old"
        if n.get("done") or n["attempts"] >= MAX_ATTEMPTS or not budget():
            continue
        try:
            n["attempts"] += 1
            log("judging news:", item["headline"])
            pre = triage("NBA", item["headline"], item.get("description"), [p["title"] for p in site_posts])
            if not pre.get("publish"):
                n["done"] = "skipped: " + pre.get("reason", "")[:120]
                log("  skipped (triage):", pre.get("reason"))
                continue
            facts, team_id = news_facts(item)
            facts["already_on_site"] = [p["title"] for p in site_posts[:40]]
            art = ask_claude(NEWS_TASK, facts)
            if not art.get("publish"):
                n["done"] = "skipped: " + art.get("reason", "")[:120]
                log("  skipped:", art.get("reason"))
                continue
            art["_teams"] = facts["teams_gr"]
            img = person_image(art) or card.news_card(
                tempfile.mktemp(suffix=".jpg"), art["title"],
                colours(team_id)[0] if team_id else "1d1d1d", logo_url(team_id))
            res = publish(art, img, f"nba-news-{nid}")
            n.update(done=True, post=res["id"])
            record(res, art)
            posted += 1
            log("  ->", res.get("link"))
        except AuthError:
            n["attempts"] -= 1
            raise
        except Exception as ex:  # keep going with the other items
            log("  ERROR", item["headline"], ex)

    # 3. other feeds: RealGM (NBA) and the Italian / Spanish / French leagues
    for feed in ([] if GAMES_ONLY else sources.FEEDS):
        league = feed["league"]
        if ONLY and league not in ONLY:
            continue
        try:
            feed_items = list(sources.items(feed))
        except Exception as ex:
            log("feed failed:", feed["key"], ex)
            continue
        judged = 0
        for item in feed_items:
            nid = item["id"]
            if nid in state["news"]:
                n = state["news"][nid]
            else:
                n = state["news"][nid] = {"seen": stamp, "attempts": 0}
            if not n.get("done") and (not item["published"]
                                      or now() - item["published"] > dt.timedelta(hours=NEWS_MAX_AGE_HOURS)):
                n["done"] = "too-old"
            if n.get("done") or n["attempts"] >= MAX_ATTEMPTS or not budget() or judged >= feed["max_judge"]:
                continue
            judged += 1
            try:
                n["attempts"] += 1
                log(f"judging {feed['key']}:", item["title"])
                pre = triage(LEAGUES[league]["about"], item["title"], item["summary"] or item["story"][:700],
                             on_site(league), LEAGUES[league].get("note", ""))
                if not pre.get("publish"):
                    n["done"] = "skipped: " + pre.get("reason", "")[:120]
                    log("  skipped (triage):", pre.get("reason"))
                    continue
                facts = {
                    "type": "news", "league": LEAGUES[league]["about"], "source_language": feed["lang"],
                    "published_utc": item["published"].isoformat(), "headline": item["title"],
                    "summary": item["summary"], "story": sources.full_story(feed, item),
                    "source_categories": item["cats"],
                    "already_on_site": on_site(league),
                }
                task = NEWS_TASK if league == "nba" else LEAGUE_TASK.format(about=LEAGUES[league]["about"])
                if LEAGUES[league].get("note"):
                    task += "\n" + LEAGUES[league]["note"]
                art = ask_claude(task, facts)
                if not art.get("publish"):
                    n["done"] = "skipped: " + art.get("reason", "")[:120]
                    log("  skipped:", art.get("reason"))
                    continue
                img = person_image(art) or team_image(art, league)
                res = publish(art, img, f"{league}-news-{nid.split(':')[1]}", league)
                n.update(done=True, post=res["id"])
                record(res, art, league)
                posted += 1
                log("  ->", res.get("link"))
            except AuthError:
                n["attempts"] -= 1
                raise
            except Exception as ex:  # keep going with the other items
                log("  ERROR", item["title"], ex)

    state["badges"] = dict(sources._badges)
    promote_top(published)
    log(f"done, {posted} article(s)")


def pending_euro_games():
    """How many finished EuroLeague/EuroCup games have no article yet (the game-night job's quick check)."""
    state = load_state()
    n = 0
    for league in ("el", "ec"):
        for eg in euro.finished(league):
            gid = f"{euro.season_code(euro.COMPS[league])}-{eg['gameCode']}"
            g = state["games"].get(gid, {})
            if not g.get("done") and g.get("attempts", 0) < MAX_ATTEMPTS:
                n += 1
    return n


if __name__ == "__main__":
    if sys.argv[1:] == ["--pending-euro"]:
        print(pending_euro_games())
        sys.exit(0)
    if not DRY_RUN and not (os.environ.get("WP_USER") and os.environ.get("WP_APP_PASSWORD")):
        sys.exit("WP_USER / WP_APP_PASSWORD missing")
    try:
        run()
    except QuotaError as ex:
        log(f"PAUSED until the limit resets: {ex}")     # not a failure: next hourly run retries
    except AuthError as ex:
        sys.exit(f"STOPPED: {ex}")
