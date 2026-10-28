"""One-off: replace the text-card images (user, 2026-10-28: always photos, never text cards). Delete after use."""
import re
import tempfile

import bot
import card

POSTS = {66427: [], 66249: [], 65256: ["Giannis Antetokounmpo", "Victor Wembanyama", "Bam Adebayo"]}
for pid, names in POSTS.items():
    try:
        p = bot.wp("GET", f"posts/{pid}?context=edit&_fields=id,title,content,featured_media")
        media = bot.wp("GET", f"media/{p['featured_media']}?context=edit&_fields=source_url")
        stem = re.sub(r"(-v\d)?\.jpg$", "", media["source_url"].rsplit("/", 1)[1])
        art = {"title": p["title"]["raw"], "content": p["content"]["raw"]}
        path = bot.person_image(art, names) if names else None
        path = path or card.news_card(tempfile.mktemp(suffix=".jpg"), logo=bot.NBA_LOGO)
        with open(path, "rb") as f:
            new = bot.wp("POST", "media", raw=f.read(), headers={
                "Content-Type": "image/jpeg", "Content-Disposition": f'attachment; filename="{stem}-v2.jpg"'})
        bot.wp("POST", f"media/{new['id']}", {"alt_text": art["title"], "caption": art.get("_caption", "")})
        bot.wp("POST", f"posts/{pid}", {"featured_media": new["id"], "content": art["content"]})
        print(pid, "->", new["source_url"], art.get("_caption", ""))
    except Exception as ex:
        print(pid, "ERROR", ex)
