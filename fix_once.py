"""One-off: check the tb-bot-meta mu-plugin on post 70223 (tagline + meta description from its excerpt). Delete after use."""
import html
import re

import bot

pid = 70223
p = bot.wp("GET", f"posts/{pid}?context=edit&_fields=id,excerpt")
ex = re.sub(r"<[^>]+>", "", html.unescape(p["excerpt"]["raw"] or p["excerpt"]["rendered"])).strip()
first = re.split(r"(?<=[.!;])\s", ex)[0]
bot.wp("POST", f"posts/{pid}", {"tb_bot_meta": {"tagline": first[:120], "metadesc": ex[:155]}})
print(pid, bot.wp("GET", f"posts/{pid}?context=edit&_fields=tb_bot_meta"))
