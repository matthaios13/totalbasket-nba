#!/usr/bin/env python3
"""Totalbasket Παναθηναϊκός bot: the basketball news of trifillakia.gr, rewritten for totalbasket.gr.

Every run:
  1. reads trifillakia.gr's basketball feed (every item is Παναθηναϊκός basketball)
  2. skips items older than MAX_AGE_HOURS, items already handled, and stories totalbasket already has
     (same story in the last ~24h: ≥4 shared title words and ≥80% overlap; a new development is not a duplicate)
  3. has Claude rewrite each in totalbasket's voice (title, excerpt, text) and checks the new title again
  4. publishes it with the trifillakia photo, tags top + 2ndtop (homepage hero: leads first, then the small cards)
     and the Παναθηναϊκός tag, in the category Claude picks (Euroleague / Stoiximan GBL / ΕΟΚ / Ελλάδα).

Env: as bot.py (WP_USER, WP_APP_PASSWORD, CLAUDE_CODE_OAUTH_TOKEN, WP_STATUS, DRY_RUN)
  PAO_MODEL (default sonnet), MAX_POSTS (default 4), MAX_AGE_HOURS (default 6)
  PREVIEW=N   with DRY_RUN=1: ignore age/state and write the N newest non-duplicate items to ./out
"""
import datetime as dt
import email.utils
import html
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
import xml.etree.ElementTree as ET

from PIL import Image

import bot
from bot import AuthError, UA, get_json, log, now, retry, strip_html, wp, _norm

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "pao_state.json")
FEED = "https://www.trifillakia.gr/taxonomy/term/2/feed"
WP_URL = bot.WP_URL
DRY_RUN = bot.DRY_RUN
WP_STATUS = bot.WP_STATUS
MODEL = os.environ.get("PAO_MODEL", "sonnet")
MAX_POSTS = int(os.environ.get("MAX_POSTS", "4"))
MAX_AGE_HOURS = float(os.environ.get("MAX_AGE_HOURS", "6"))
PREVIEW = int(os.environ.get("PREVIEW", "0"))

CATEGORIES = {"euroleague": 2, "gbl": 4, "eok": 1238, "greece": 9}
TAG_TOP, TAG_2ND_TOP, TAG_PAO = 112, 113, 137
DUP_HOURS = 48
STOP = set("και του της των τον την στο στη στον στην στα για από με που ότι είναι θα να δεν σε ως οι ο η το τα ένα μια".split())


# ---------------------------------------------------------------- state

def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {"items": {}}


def save_state(state):
    cutoff = (now() - dt.timedelta(days=14)).isoformat()
    state["items"] = {k: v for k, v in state["items"].items() if v.get("seen", "") >= cutoff}
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=1, sort_keys=True)


# ---------------------------------------------------------------- trifillakia

def fetch(url):
    def go():
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
            return r.read()
    return retry(go)


def feed_items():
    # «?nc=…» παρακάμπτει την cache του Drupal, που κρατά τη ροή έως 1 ώρα (ένα νέο άρθρο δεν φαινόταν, 22/10)
    root = ET.fromstring(fetch(f"{FEED}?nc={int(now().timestamp())}"))
    for it in root.iter("item"):
        link = (it.findtext("link") or "").strip()
        desc = html.unescape(it.findtext("description") or "")
        body = re.search(r'field-name-body.*?<div class="field-item[^"]*">(.*)', desc, re.S)
        yield {
            "url": link,
            "title": html.unescape(it.findtext("title") or "").strip(),
            "date": email.utils.parsedate_to_datetime(it.findtext("pubDate")),
            "body": strip_html(body.group(1) if body else desc)[:9000],
        }


def page_meta(url):
    """og:image and meta description of the article page."""
    page = fetch(url).decode("utf-8", "replace")
    def meta(prop):
        m = re.search(rf'<meta (?:property|name)="{prop}" content="([^"]*)"', page)
        return html.unescape(m.group(1)) if m else ""
    return {"image": meta("og:image"), "description": meta("og:description") or meta("description")}


# ---------------------------------------------------------------- duplicates

def words(title):
    return {w for w in re.findall(r"\w+", _norm(title)) if len(w) > 2 and w not in STOP}


def same_story(a, b):
    wa, wb = words(a), words(b)
    shared = len(wa & wb)
    return shared >= 4 and shared / max(1, min(len(wa), len(wb))) >= 0.8


def recent_site_posts():
    """Every post of the last DUP_HOURS on totalbasket (editors' and bots'): [title]."""
    after = (now() - dt.timedelta(hours=DUP_HOURS)).strftime("%Y-%m-%dT%H:%M:%S")
    res = get_json(f"{WP_URL}/wp-json/wp/v2/posts?per_page=100&after={after}&_fields=title")
    return [html.unescape(p["title"]["rendered"]) for p in res]


# ---------------------------------------------------------------- Claude

STYLE = """Είσαι συντάκτης του ελληνικού site μπάσκετ totalbasket.gr. Σου δίνεται μια είδηση μπάσκετ (Παναθηναϊκός, ΕΟΚ, ελληνικό μπάσκετ)
από άλλο site και τη γράφεις ξανά για το totalbasket.

Κανόνες:
- Γράφεις το άρθρο ΞΑΝΑ με δικά σου λόγια και δική σου δομή. ΔΕΝ αντιγράφεις προτάσεις του πρωτότυπου.
- Χρησιμοποιείς ΜΟΝΟ γεγονότα, αριθμούς, ονόματα και δηλώσεις που υπάρχουν στα δεδομένα. Δεν εφευρίσκεις τίποτα.
- Οι δηλώσεις (μέσα σε «») μένουν ίδιες στο νόημα, με σαφή απόδοση (ποιος το είπε). Κράτα τις πιο ενδιαφέρουσες.
- Αν το πρωτότυπο αναφέρει άλλο μέσο ως πηγή (π.χ. «σύμφωνα με το SDNA»), κράτα την αναφορά. Μην αναφέρεις το trifillakia.
- Ύφος ζωντανό, δημοσιογραφικό, ουδέτερο, όπως ένα ελληνικό αθλητικό site (όχι οπαδικό).
- Μορφή κειμένου: 3-6 παράγραφοι, HTML μόνο με <p class="wp-block-paragraph">, <strong> (λίγο) και
  <blockquote class="wp-block-quote"><p>...</p></blockquote> για μία δήλωση αν θες. Τίποτα άλλο.
- Τίτλος: σύντομος, πιασάρικος, ελληνικός, διαφορετικός από τον αρχικό, χωρίς τελεία, χωρίς emoji.
- excerpt: 1-2 προτάσεις (υπότιτλος).
- category: "euroleague" για EuroLeague, "gbl" για Stoiximan GBL / Κύπελλο, "eok" για θέματα της ΕΟΚ (ομοσπονδία, πρόεδρος/Λιόλιος,
  διαιτησία, δικαστικά/έρευνες γύρω από την ΕΟΚ), "greece" για άλλα θέματα ελληνικού μπάσκετ.
- people: τα ελληνικά ονόματα των 1-4 κύριων προσώπων ή ομάδων.
- ΣΗΜΑΝΤΙΚΟ: στο "already_on_site" είναι οι τίτλοι του totalbasket του τελευταίου 24ώρου. Βάλε publish=false (με reason) αν
  η είδηση είναι ΗΔΗ εκεί (ίδιο θέμα, ίδια γεγονότα). Μια ΝΕΑ εξέλιξη του ίδιου θέματος (π.χ. ένταλμα → σύλληψη,
  «κοντά σε συμφωνία» → «υπέγραψε», νέες δηλώσεις) ΔΕΝ είναι διπλότυπο: publish=true.

Απαντάς ΜΟΝΟ με ένα JSON αντικείμενο, χωρίς τίποτα άλλο:
{"publish": true, "reason": "", "title": "...", "excerpt": "...", "content": "<p ...>...</p>", "category": "euroleague", "people": []}"""


def rewrite(item, meta, site_titles):
    facts = {
        "title": item["title"],
        "subtitle": meta["description"],
        "text": item["body"],
        "already_on_site": site_titles[:60],
    }
    prompt = f"{STYLE}\n\nΔΕΔΟΜΕΝΑ (JSON):\n{json.dumps(facts, ensure_ascii=False, indent=1)}"
    r = subprocess.run(
        bot.claude_cmd(MODEL),
        input=prompt, capture_output=True, text=True, timeout=600)
    if '"api_error_status":401' in r.stdout or "Failed to authenticate" in r.stdout:
        raise AuthError("Claude token rejected - set CLAUDE_CODE_OAUTH_TOKEN again")
    if r.returncode != 0:
        raise RuntimeError(f"claude failed: {r.stderr[-800:] or r.stdout[-800:]}")
    text = json.loads(r.stdout).get("result", "")
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise RuntimeError(f"no JSON in answer: {text[:300]}")
    art = json.loads(m.group(0))
    if art.get("publish") and not (art.get("title") and art.get("content")):
        raise RuntimeError("answer missing title/content")
    if art.get("publish") and bot.MIXED_SCRIPT.search(art["title"]):
        raise RuntimeError(f"mixed Greek/Latin title: {art['title']}")
    return art


# ---------------------------------------------------------------- WordPress

def image_jpeg(url):
    """The trifillakia photo as a JPEG file (never WebP: Instagram rejects it)."""
    img = Image.open(io.BytesIO(fetch(url))).convert("RGB")
    # μεγάλες: έως 1600px· μικρές (trifillakia δίνει συχνά ~770px): έως 1200px, ώστε να γεμίζουν το πλαίσιο
    # του άρθρου και να βγαίνουν σωστά στο Facebook (θέλει πλάτος 1200px)
    width = 1600 if img.width > 1600 else (1200 if img.width < 1200 else img.width)
    if width != img.width:
        img = img.resize((width, round(img.height * width / img.width)), Image.LANCZOS)
    path = tempfile.mktemp(suffix=".jpg")
    img.save(path, "JPEG", quality=88)
    return path


def pao_story(art):
    """Article about Παναθηναϊκός (gets the team tag); ΕΟΚ stories without the team don't."""
    return bool(re.search(r"παναθηνα[ιϊ]κ|(?<!\w)παο(?!\w)", _norm(art["title"] + " " + art["content"])))


def publish(art, image_path, slug_hint, source):
    cat = CATEGORIES.get(art.get("category"), CATEGORIES["euroleague"])
    if DRY_RUN:
        os.makedirs(os.path.join(HERE, "out"), exist_ok=True)
        base = os.path.join(HERE, "out", "pao-" + slug_hint)
        if image_path:
            os.replace(image_path, base + ".jpg")
        with open(base + ".html", "w") as f:
            f.write(f"<p><small>Πηγή: <a href='{html.escape(source['url'])}'>{html.escape(source['title'])}</a> · "
                    f"κατηγορία {art.get('category')} · ετικέτες top, 2ndtop{', Παναθηναϊκός' if pao_story(art) else ''}</small></p>"
                    f"<h1>{html.escape(art['title'])}</h1>"
                    + (f"<p><img src='{os.path.basename(base)}.jpg' width=640></p>" if image_path else "")
                    + f"<p><em>{html.escape(art.get('excerpt', ''))}</em></p>{art['content']}")
        return {"id": "dry-run", "link": base + ".html"}
    media_id = 0
    if image_path:
        with open(image_path, "rb") as f:
            media = wp("POST", "media", raw=f.read(), headers={
                "Content-Type": "image/jpeg",
                "Content-Disposition": f'attachment; filename="pao-{slug_hint}.jpg"'})
        wp("POST", f"media/{media['id']}", {"alt_text": art["title"]})
        media_id = media["id"]
    tags = [t for t in bot.find_tags(art.get("people", [])) if t != bot.NBA_TAG]
    tags = list(dict.fromkeys(tags + ([TAG_PAO] if pao_story(art) else []) + [TAG_TOP, TAG_2ND_TOP]))
    data = {"title": art["title"], "content": art["content"], "excerpt": art.get("excerpt", ""),
            "status": WP_STATUS, "categories": [cat], "tags": tags}
    if media_id:
        data["featured_media"] = media_id
    return wp("POST", "posts", data)


# ---------------------------------------------------------------- main

def run():
    state = load_state()
    try:
        _run(state)
    finally:
        if not PREVIEW:
            save_state(state)


def _run(state):
    site_titles = recent_site_posts()
    posted = 0
    for item in sorted(feed_items(), key=lambda i: i["date"], reverse=bool(PREVIEW)):
        key = item["url"]
        if not PREVIEW:
            if key in state["items"]:
                continue
            if item["date"] < now() - dt.timedelta(hours=MAX_AGE_HOURS):
                state["items"][key] = {"seen": now().isoformat(), "skip": "old"}
                continue
        limit = PREVIEW or MAX_POSTS
        if posted >= limit:
            break
        dup = next((t for t in site_titles if same_story(item["title"], t)), None)
        if dup:
            log("already on site:", item["title"], "=", dup)
            state["items"][key] = {"seen": now().isoformat(), "skip": "duplicate"}
            continue
        try:
            meta = page_meta(item["url"])
            art = rewrite(item, meta, site_titles)
            if not art.get("publish"):
                log("skipped by Claude:", item["title"], "-", art.get("reason"))
                state["items"][key] = {"seen": now().isoformat(), "skip": art.get("reason", "")[:200]}
                continue
            dup = next((t for t in site_titles if same_story(art["title"], t)), None)
            if dup:
                log("rewritten title already on site:", art["title"], "=", dup)
                state["items"][key] = {"seen": now().isoformat(), "skip": "duplicate (rewritten)"}
                continue
            image = image_jpeg(meta["image"]) if meta["image"] else None
            slug = re.sub(r"\D", "", key.split("/article/")[-1].split("/")[0]) or str(int(now().timestamp()))
            res = publish(art, image, slug, item)
            state["items"][key] = {"seen": now().isoformat(), "post": res.get("id"), "title": art["title"]}
            site_titles.insert(0, art["title"])
            posted += 1
            log("published:", art["title"], res.get("link"))
        except AuthError:
            raise
        except Exception as ex:
            log("  ERROR", item["title"], ex)
    log(f"done, {posted} article(s)")


if __name__ == "__main__":
    if not DRY_RUN and not (os.environ.get("WP_USER") and os.environ.get("WP_APP_PASSWORD")):
        sys.exit("WP_USER / WP_APP_PASSWORD missing")
    try:
        run()
    except AuthError as ex:
        sys.exit(f"STOPPED: {ex}")
