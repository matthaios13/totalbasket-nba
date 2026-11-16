"""One-off: post 70076 (Yabusele on Panathinaikos): no free photo of him, so the Panathinaikos crest wallpaper. Delete after use."""
import tempfile

import bot
import card
import sources

pid = 70076
p = bot.wp("GET", f"posts/{pid}?context=edit&_fields=id,title")
path = card.news_card(tempfile.mktemp(suffix=".jpg"), logo=sources.badge("gbl", "Panathinaikos"))
with open(path, "rb") as f:
    new = bot.wp("POST", "media", raw=f.read(), headers={
        "Content-Type": "image/jpeg", "Content-Disposition": 'attachment; filename="gbl-news-70076-v2.jpg"'})
bot.wp("POST", f"media/{new['id']}", {"alt_text": p["title"]["raw"]})
bot.wp("POST", f"posts/{pid}", {"featured_media": new["id"]})
print(pid, "->", new["source_url"])
