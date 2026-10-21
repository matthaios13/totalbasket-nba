"""One-off: move duplicate bot posts and the two test drafts to the WordPress trash (restorable)."""
import bot

for pid in (65189, 65179, 65173, 65175):
    p = bot.wp("DELETE", f"posts/{pid}")          # no force=true -> goes to Trash
    print(pid, p.get("status"), bot.html.unescape(p["title"]["rendered"])[:70])
