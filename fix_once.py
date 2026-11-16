"""One-off: post 70076 got the site-logo placeholder from the library; give it a photo of Yabusele. Delete after use."""
import tempfile

import bot
import card

pid = 70076
p = bot.wp("GET", f"posts/{pid}?context=edit&_fields=id,title,content")
art = {"title": p["title"]["raw"], "content": p["content"]["raw"]}
path = bot.person_image(art, ["Guerschon Yabusele"]) or card.news_card(tempfile.mktemp(suffix=".jpg"),
                                                                         logo=bot.sources.badge("gbl", "Panathinaikos"))
with open(path, "rb") as f:
    new = bot.wp("POST", "media", raw=f.read(), headers={
        "Content-Type": "image/jpeg", "Content-Disposition": 'attachment; filename="gbl-news-yabusele-70076.jpg"'})
bot.wp("POST", f"media/{new['id']}", {"alt_text": art["title"], "caption": art.get("_caption", "")})
bot.wp("POST", f"posts/{pid}", {"featured_media": new["id"], "content": art["content"]})
print(pid, "->", new["source_url"], art.get("_caption", ""))
