"""One-off (user, 19/11/2026): publish the Fasoulas press release (info@ email «ΠΡΩΤΑ ΤΟ ΜΠΑΣΚΕΤ») in ΕΟΚ,
2ndTOP, with the user's photo. Delete after use."""
import json
import tempfile

from PIL import Image

import bot
import card

d = json.load(open("oneoff/fasoulas.json"))
path = card.photo_card(tempfile.mktemp(suffix=".jpg"), Image.open("oneoff/fasoulas.jpg").convert("RGB"))
with open(path, "rb") as f:
    media = bot.wp("POST", "media", raw=f.read(), headers={
        "Content-Type": "image/jpeg", "Content-Disposition": 'attachment; filename="fasoulas-eok-apantisi.jpg"'})
bot.wp("POST", f"media/{media['id']}", {"alt_text": "Παναγιώτης Φασούλας"})
res = bot.wp("POST", "posts", {
    "title": d["title"], "content": d["content"], "excerpt": d["excerpt"], "status": "publish",
    "categories": [1238], "tags": [bot.TAG_2ND_TOP, 1239, 519, 1457, 370], "featured_media": media["id"],
    "tb_bot_meta": {"tagline": d["tagline"], "metadesc": d["metadesc"]}})
print("PUBLISHED", res["id"], res["link"])
