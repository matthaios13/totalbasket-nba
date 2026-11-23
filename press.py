#!/usr/bin/env python3
"""Totalbasket press-release bot: the press releases that arrive at info@totalbasket.gr become articles.

Every run:
  1. reads the mailbox over IMAP, read-only (BODY.PEEK: nothing is marked as read, moved or deleted)
  2. keeps only mails from the SENDERS below that are newer than MAX_AGE_HOURS and not handled yet
  3. takes the text from the mail body, else from an attached .doc/.docx/.pdf, else from the article the mail links to
  4. Claude keeps the press release AS IS (only greetings, signatures, contact details are removed); English
     releases (EuroLeague / EuroCup) are translated faithfully into Greek
  5. publishes it at once in the team's category, with the photos of the mail (attachments, photos in the mail,
     or the linked article's photo): the first is the featured image, the rest go into the text.

User (2026-10-30 / 2026-11-03): text as is, publish immediately, in the category of the team's league, with the
photos the senders put in the mail.

Env: as bot.py (WP_USER, WP_APP_PASSWORD, CLAUDE_CODE_OAUTH_TOKEN, WP_STATUS, DRY_RUN)
  IMAP_USER (default info@totalbasket.gr), IMAP_PASSWORD, IMAP_HOST (default imap.hostinger.com)
  PRESS_MODEL (default sonnet), MAX_POSTS (default 8), MAX_AGE_HOURS (default 24)
  PREVIEW=N   with DRY_RUN=1: ignore age/state and write the N newest press releases to ./out
"""
import datetime as dt
import email
import email.header
import email.policy
import email.utils
import hashlib
import html
import imaplib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import zipfile

from PIL import Image

import bot
from bot import AuthError, UA, get_json, log, now, retry, strip_html, wp
from pao import recent_site_posts, same_story

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "press_state.json")
WP_URL = bot.WP_URL
DRY_RUN = bot.DRY_RUN
WP_STATUS = bot.WP_STATUS
MODEL = os.environ.get("PRESS_MODEL", "sonnet")
MAX_POSTS = int(os.environ.get("MAX_POSTS", "8"))
MAX_AGE_HOURS = float(os.environ.get("MAX_AGE_HOURS", "24"))
PREVIEW = int(os.environ.get("PREVIEW", "0"))
IMAP_HOST = os.environ.get("IMAP_HOST", "imap.hostinger.com")
IMAP_USER = os.environ.get("IMAP_USER", "info@totalbasket.gr")

# site categories
EUROLEAGUE, EUROCUP, NEXT_GEN = 2, 12, 470
GBL, ELITE, NL2, GREECE = 4, 10, 1501, 9
TAG_EL, TAG_EC = 105, 1233

# sender address (or domain) -> team, site category, tags. Categories = the league the team plays in (2026-27).
# The clubs' own addresses are not written here (the repo is public since 23/11/2026): they come from the
# GitHub secret PRESS_SENDER_ADDRS, a JSON map {"karditsa": "<address>", ...}.
CLUBS = {
    "karditsa": {"name": "Α.Σ. Καρδίτσας", "categories": [GBL], "people": ["Καρδίτσα"]},
    "danaos":   {"name": "Δαναός Τρικάλων", "categories": [NL2], "people": ["Δαναός Τρικάλων"]},
    "sofades":  {"name": "Γ.Σ. Σοφάδων", "categories": [ELITE], "people": ["Σοφάδες"]},
    "titanes":  {"name": "Γ.Σ. Τιτάνες Παλάμα", "categories": [GREECE], "people": ["Τιτάνες Παλάμα"]},
}
SENDERS = {"comms.euroleague.net": {"name": "EuroLeague Basketball", "lang": "en"}}   # category from the subject
SENDERS.update({addr.lower(): CLUBS[key] for key, addr in json.loads(os.environ.get("PRESS_SENDER_ADDRS") or "{}").items()
                if key in CLUBS and addr})

MIN_PHOTO = 500          # px, shorter side of a photo worth publishing (logos, icons, banners are smaller)
MAX_PHOTOS = 10
SOCIAL = re.compile(r"facebook|instagram|twitter|x\.com|tiktok|youtube|linkedin|threads|mailto:|google\.com/maps|"
                    r"unsubscribe|list-manage|mailchimp|euroleague\.net/?$")


# ---------------------------------------------------------------- state

def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {"items": {}}


def save_state(state):
    cutoff = (now() - dt.timedelta(days=30)).isoformat()
    state["items"] = {k: v for k, v in state["items"].items() if v.get("seen", "") >= cutoff}
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=1, sort_keys=True)


# ---------------------------------------------------------------- mailbox

def sender_of(msg):
    addr = email.utils.parseaddr(str(msg.get("From", "")))[1].lower()
    for key, info in SENDERS.items():
        if addr == key or addr.endswith("@" + key) or addr.endswith("." + key):
            return addr, info
    return addr, None


def mails(since):
    """(uid, message) of every mail from SENDERS since the given date; read-only, nothing is marked as read."""
    box = imaplib.IMAP4_SSL(IMAP_HOST, 993)
    try:
        box.login(IMAP_USER, os.environ["IMAP_PASSWORD"])
    except imaplib.IMAP4.error as ex:
        raise AuthError(f"IMAP login failed for {IMAP_USER}: {ex}")
    try:
        box.select("INBOX", readonly=True)
        uids = set()
        for key in SENDERS:
            _, data = box.uid("search", None, "SINCE", since.strftime("%d-%b-%Y"), "FROM", f'"{key}"')
            uids.update(data[0].split())
        for uid in sorted(uids, key=int):
            _, data = box.uid("fetch", uid, "(BODY.PEEK[])")
            raw = next((d[1] for d in data if isinstance(d, tuple)), None)
            if raw:
                yield uid.decode(), email.message_from_bytes(raw, policy=email.policy.default)
    finally:
        try:
            box.logout()
        except Exception:
            pass


def subject_of(msg):
    return re.sub(r"\s+", " ", str(msg.get("Subject", ""))).strip()


def date_of(msg):
    try:
        d = email.utils.parsedate_to_datetime(str(msg.get("Date")))
        return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
    except Exception:
        return now()


def parts(msg):
    """body text, body html, attachments [(filename, content_type, bytes)] (inline images included)."""
    text, htm, files = "", "", []
    for part in msg.walk():
        if part.is_multipart():
            continue
        ctype = part.get_content_type()
        name = part.get_filename() or ""
        try:
            payload = part.get_payload(decode=True) or b""
        except Exception:
            continue
        if ctype == "text/plain" and not name and not text:
            text = payload.decode(part.get_content_charset() or "utf-8", "replace")
        elif ctype == "text/html" and not name and not htm:
            htm = payload.decode(part.get_content_charset() or "utf-8", "replace")
        elif payload:
            files.append((name, ctype, payload))
    return text, htm, files


def doc_text(name, data):
    """Text of an attached Word / PDF document ('' if it can't be read)."""
    ext = os.path.splitext(name.lower())[1]
    try:
        if ext == ".docx":
            xml = zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml").decode("utf-8")
            xml = re.sub(r"</w:p>", "\n", xml)
            return html.unescape(re.sub(r"<[^>]+>", "", xml)).strip()
        if ext not in (".doc", ".rtf", ".odt", ".pdf"):
            return ""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "in" + ext)
            with open(path, "wb") as f:
                f.write(data)
            if ext == ".pdf":
                cmd = ["pdftotext", "-layout", path, "-"]
            elif shutil.which("textutil"):                       # macOS (local tests)
                cmd = ["textutil", "-convert", "txt", "-stdout", path]
            elif ext == ".doc":
                cmd = ["catdoc", "-d", "utf-8", "-w", path]     # GitHub runner: apt install catdoc
            else:
                return ""
            r = subprocess.run(cmd, capture_output=True, timeout=60)
            return r.stdout.decode("utf-8", "replace").strip()
    except Exception as ex:
        log("  can't read", name, ex)
        return ""


def body_links(htm, text):
    links = re.findall(r'href=["\'](https?://[^"\']+)', htm or "") or re.findall(r"https?://\S+", text or "")
    out = []
    for u in links:
        u = html.unescape(u).rstrip(").,>")
        if not SOCIAL.search(u) and u not in out:
            out.append(u)
    return out


def fetch(url):
    def go():
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
            return r.geturl(), r.read()
    return retry(go)


def linked_article(url):
    """{'url', 'text', 'image'} of the article the mail links to (WordPress-style pages)."""
    final, raw = fetch(url)
    page = raw.decode("utf-8", "replace")
    m = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', page)
    image = html.unescape(m.group(1)) if m else ""
    m = re.search(r'class="[^"]*(?:entry-content|post-content|article-content|td-post-content)|<article', page)
    body = page[m.start():] if m else page
    end = re.search(r"<footer|class=\"[^\"]*(?:jeg_post_tags|post-tags|related|post-navigation|jeg_prevnext|comments)", body)
    body = body[:end.start()] if end else body[:60000]
    paras = re.findall(r"<p[^>]*>(.*?)</p>", body, re.S)
    text = "\n\n".join(t for t in (strip_html(p) for p in paras) if t) or strip_html(body)
    return {"url": final, "text": text[:12000], "image": image}


# ---------------------------------------------------------------- photos

def jpeg(data):
    """A photo as a JPEG file, or None if it is too small to be a photo (logo, icon, signature banner)."""
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception:
        return None
    if min(img.size) < MIN_PHOTO or not 0.3 <= img.height / img.width <= 2.2:
        return None
    img = img.convert("RGB")
    if img.width > 1600:
        img = img.resize((1600, round(img.height * 1600 / img.width)), Image.LANCZOS)
    path = tempfile.mktemp(suffix=".jpg")
    img.save(path, "JPEG", quality=88)
    return path


def photos(files, htm, page_image):
    """The mail's photos, in order: attachments, photos placed in the mail, the linked article's photo."""
    out, seen = [], set()

    def add(data):
        h = hashlib.md5(data).hexdigest()
        if h in seen or len(out) >= MAX_PHOTOS:
            return
        seen.add(h)
        path = jpeg(data)
        if path:
            out.append(path)

    for name, ctype, data in files:
        if ctype.startswith("image/") or re.search(r"\.(jpe?g|png|webp|gif|heic)$", name.lower()):
            add(data)
    for src in re.findall(r'<img[^>]+src=["\'](https?://[^"\']+)', htm or ""):
        src = html.unescape(src)
        if re.search(r"logo|icon|spacer|pixel|track|open\.|/o/|facebook|twitter|instagram|youtube|tiktok", src, re.I):
            continue
        try:
            add(fetch(src)[1])
        except Exception:
            pass
    if page_image:
        try:
            add(fetch(page_image)[1])
        except Exception:
            pass
    return out


# ---------------------------------------------------------------- Claude

PROMPT = """Είσαι συντάκτης του ελληνικού site μπάσκετ totalbasket.gr. Σου δίνεται ένα email που ήρθε στο site από
τον αποστολέα «{sender}»: δελτίο τύπου ή ανακοίνωση. Το ανεβάζεις ως άρθρο.

Κανόνες:
- Το κείμενο του δελτίου μένει ΑΥΤΟΥΣΙΟ. Αν είναι στα ελληνικά, ΔΕΝ αλλάζεις, δεν προσθέτεις, δεν συντομεύεις,
  δεν ξαναγράφεις καμία λέξη του. Διορθώνεις μόνο σπασμένες γραμμές/κενά.
- Αν είναι σε άλλη γλώσσα (π.χ. αγγλικά), το μεταφράζεις ΠΙΣΤΑ και ολόκληρο στα ελληνικά, σε φυσικό δημοσιογραφικό ύφος
  ελληνικού αθλητικού site. Ονόματα παικτών/ομάδων με ελληνικά γράμματα όπως τα γράφει ο ελληνικός αθλητικός Τύπος
  (π.χ. Real Madrid → Ρεάλ Μαδρίτης, Valencia Basket → Βαλένθια). Οι διοργανώσεις μένουν EuroLeague / EuroCup.
- Αφαιρείς ΜΟΝΟ ό,τι δεν είναι μέρος του δελτίου: χαιρετισμούς («Καλησπέρα σας», «σας αποστέλλουμε…»,
  «παρακαλούμε για τη δημοσίευση», «ευχαριστούμε»), υπογραφές, τηλέφωνα, διευθύνσεις, emails, social links,
  «Preview in browser», «unsubscribe», νομικές σημειώσεις, επικεφαλίδες τύπου «ΔΕΛΤΙΟ ΤΥΠΟΥ» / «PRESS RELEASE» / ημερομηνία,
  και το τυποποιημένο κείμενο παρουσίασης του αποστολέα στο τέλος (π.χ. «About Euroleague Basketball» / «Euroleague
  Basketball (EB) is a global leader…»).
- Το κείμενο του δελτίου μπορεί να είναι στο σώμα του email, στο συνημμένο έγγραφο ή στο άρθρο του συνδέσμου
  ("linked_article"): πάρε το από εκεί που είναι ολόκληρο. Αν ίδιο κείμενο υπάρχει σε δύο σημεία, το βάζεις μία φορά.
- Αν το email έχει ΔΥΟ ή περισσότερα ξεχωριστά δελτία, βγάζεις ένα άρθρο για το καθένα.
- title: ο τίτλος του ίδιου του δελτίου (ελληνικά, χωρίς «Δελτίο Τύπου», «EB Press Release:», τελεία ή emoji).
  Κανονική ορθογραφία πεζών/κεφαλαίων (π.χ. «Τι Τζέι Σορτς», ποτέ «ΤΖέι»).
  Αν το δελτίο δεν έχει δικό του τίτλο, φτιάξε έναν σύντομο, ενημερωτικό από το περιεχόμενο (όχι το γενικό θέμα του email).
- excerpt: 1-2 προτάσεις από το ίδιο το κείμενο (υπότιτλος).
- paragraphs: οι παράγραφοι του κειμένου, με τη σειρά, απλό κείμενο (χωρίς HTML). Λίστες/αποτελέσματα/συνθέσεις
  μένουν σε δική τους παράγραφο η καθεμία.
- competition: μόνο για EuroLeague Basketball: "euroleague", "eurocup" ή "nextgen" (Adidas Next Generation Tournament).
- publish=false (με reason) ΜΟΝΟ αν το email δεν είναι δελτίο τύπου/ανακοίνωση (π.χ. διαφήμιση, newsletter χωρίς είδηση,
  προσωπικό μήνυμα, άδειο email χωρίς κείμενο πουθενά, ή email που είναι μόνο σύνδεσμος για κατέβασμα υλικού
  — π.χ. «Download the game kits here» — χωρίς είδηση να γραφτεί).
- publish=false επίσης αν η ΙΔΙΑ είδηση υπάρχει ήδη στους τίτλους "already_on_site" (άρθρα του totalbasket των τελευταίων
  48 ωρών, π.χ. ο ίδιος MVP της ίδιας αγωνιστικής/μήνα, η ίδια μεταγραφή). Άλλη εξέλιξη του ίδιου θέματος δεν είναι διπλότυπο.

Απαντάς ΜΟΝΟ με ένα JSON αντικείμενο, χωρίς τίποτα άλλο:
{{"publish": true, "reason": "", "articles": [{{"title": "...", "excerpt": "...", "paragraphs": ["..."], "competition": ""}}]}}

EMAIL (JSON):
{data}"""


def ask_claude(sender, facts):
    art = bot._claude(PROMPT.format(sender=sender, data=json.dumps(facts, ensure_ascii=False, indent=1)), MODEL)
    if art.get("publish"):
        arts = [a for a in art.get("articles") or [] if a.get("title") and a.get("paragraphs")]
        if not arts:
            raise RuntimeError("answer missing title/paragraphs")
        for a in arts:
            if bot.MIXED_SCRIPT.search(a["title"]):
                raise RuntimeError(f"mixed Greek/Latin title: {a['title']}")
        art["articles"] = arts
    return art


# ---------------------------------------------------------------- WordPress

def categories_and_tags(info, article):
    if info.get("categories"):
        return info["categories"], bot.find_tags(info.get("people", []), None)[1:]
    comp = article.get("competition")
    if comp == "eurocup":
        return [EUROCUP], [TAG_EC]
    if comp == "nextgen":
        return [NEXT_GEN], []
    return [EUROLEAGUE], [TAG_EL]


def content_html(article, photo_ids):
    """Paragraphs of the release; the photos after the featured one go in between the paragraphs."""
    paras = [f'<p class="wp-block-paragraph">{html.escape(p.strip())}</p>' for p in article["paragraphs"] if p.strip()]
    imgs = [f'<figure class="wp-block-image size-large"><img src="{src}" alt="{html.escape(article["title"])}" '
            f'class="wp-image-{mid}"/></figure>' for mid, src in photo_ids[1:]]
    if not imgs:
        return "\n".join(paras)
    out, step = [], max(1, len(paras) // (len(imgs) + 1))
    for i, p in enumerate(paras):
        out.append(p)
        if imgs and (i + 1) % step == 0 and i + 1 < len(paras):
            out.append(imgs.pop(0))
    return "\n".join(out + imgs)


def publish(article, info, images, slug_hint, source):
    cats, tags = categories_and_tags(info, article)
    if DRY_RUN:
        os.makedirs(os.path.join(HERE, "out"), exist_ok=True)
        base = os.path.join(HERE, "out", "press-" + slug_hint)
        names = []
        for i, path in enumerate(images):
            names.append(f"{os.path.basename(base)}-{i}.jpg")
            shutil.move(path, os.path.join(HERE, "out", names[-1]))
        body = content_html(article, [(0, n) for n in names])
        with open(base + ".html", "w") as f:
            f.write(f"<p><small>Από: {html.escape(source)} · κατηγορίες {cats} · ετικέτες {tags}</small></p>"
                    f"<h1>{html.escape(article['title'])}</h1>"
                    + (f"<p><img src='{names[0]}' width=640></p>" if names else "<p>(χωρίς φωτογραφία)</p>")
                    + f"<p><em>{html.escape(article.get('excerpt', ''))}</em></p>{body}")
        return {"id": "dry-run", "link": base + ".html"}
    uploaded = []
    for i, path in enumerate(images):
        with open(path, "rb") as f:
            media = wp("POST", "media", raw=f.read(), headers={
                "Content-Type": "image/jpeg",
                "Content-Disposition": f'attachment; filename="press-{slug_hint}-{i + 1}.jpg"'})
        wp("POST", f"media/{media['id']}", {"alt_text": article["title"]})
        uploaded.append((media["id"], media.get("source_url", "")))
    data = {"title": article["title"], "content": content_html(article, uploaded),
            "excerpt": article.get("excerpt", ""), "status": WP_STATUS, "categories": cats, "tags": tags}
    if uploaded:
        data["featured_media"] = uploaded[0][0]
    return wp("POST", "posts", data)


# ---------------------------------------------------------------- main

def handle(uid, msg, site_titles):
    """Publishes the press release(s) of one mail; returns the state entry."""
    addr, info = sender_of(msg)
    subject = subject_of(msg)
    text, htm, files = parts(msg)
    body = strip_html(htm) if htm else text
    docs = [{"file": n, "text": t[:15000]} for n, c, d in files for t in [doc_text(n, d)] if t]
    page = None
    links = body_links(htm, text)
    # the club only sends a link (Δαναός, sometimes Καρδίτσα): the release is the linked article
    if links and not docs and len(re.sub(r"https?://\S+", "", body)) < 900:
        try:
            page = linked_article(links[0])
        except Exception as ex:
            log("  can't read link", links[0], ex)
    facts = {"subject": subject, "body": body[:15000], "attachments": docs,
             "linked_article": page["text"] if page else "", "already_on_site": site_titles[:80]}
    art = ask_claude(info["name"], facts)
    if not art.get("publish"):
        log("skipped by Claude:", subject, "-", art.get("reason"))
        return {"skip": (art.get("reason") or "")[:200]}
    images = photos(files, htm, page["image"] if page else "")
    posts = []
    for n, article in enumerate(art["articles"]):
        dup = next((t for t in site_titles if same_story(article["title"], t)), None)
        if dup:
            log("already on site:", article["title"], "=", dup)
            continue
        # one mail with several releases: every article gets its own copy of the mail's photos
        imgs = [shutil.copy(p, tempfile.mktemp(suffix=".jpg")) for p in images]
        res = publish(article, info, imgs, f"{uid}-{n + 1}", f"{info['name']} <{addr}> · {subject}")
        site_titles.insert(0, article["title"])
        posts.append(res.get("id"))
        log("published:", article["title"], f"({len(imgs)} photo(s))", res.get("link"))
    return {"posts": posts, "title": subject}


def run():
    state = load_state()
    try:
        _run(state)
    finally:
        if not PREVIEW:
            save_state(state)


def _run(state):
    site_titles = recent_site_posts()
    since = now() - dt.timedelta(hours=MAX_AGE_HOURS if not PREVIEW else 24 * 14)
    found = sorted(mails(since - dt.timedelta(days=1)), key=lambda m: date_of(m[1]), reverse=bool(PREVIEW))
    posted = 0
    for uid, msg in found:
        key = str(msg.get("Message-ID") or uid).strip()
        if not PREVIEW:
            if key in state["items"]:
                continue
            if date_of(msg) < since:
                state["items"][key] = {"seen": now().isoformat(), "skip": "old"}
                continue
        if posted >= (PREVIEW or MAX_POSTS):
            break
        log("mail:", sender_of(msg)[0], "·", subject_of(msg))
        try:
            entry = handle(uid, msg, site_titles)
            state["items"][key] = {"seen": now().isoformat(), **entry}
            posted += len(entry.get("posts", []))
        except AuthError:
            raise
        except Exception as ex:
            log("  ERROR", subject_of(msg), ex)
    log(f"done, {posted} article(s)")


if __name__ == "__main__":
    if not os.environ.get("IMAP_PASSWORD"):
        sys.exit("IMAP_PASSWORD missing")
    if not DRY_RUN and not (os.environ.get("WP_USER") and os.environ.get("WP_APP_PASSWORD")):
        sys.exit("WP_USER / WP_APP_PASSWORD missing")
    try:
        run()
    except AuthError as ex:
        sys.exit(f"STOPPED: {ex}")
