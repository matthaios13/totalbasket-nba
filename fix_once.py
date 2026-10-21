"""One-off: trash the duplicate Rizzetta interview; fix the name spelling in the kept one. Delete after use."""
import bot

p = bot.wp("GET", "posts/65315?context=edit&_fields=id,title")
assert "Ριτσέτα" in p["title"]["raw"], p["title"]["raw"]
print(65315, bot.wp("DELETE", "posts/65315")["status"])        # no force -> Trash

p = bot.wp("GET", "posts/65313?context=edit&_fields=id,title,content,excerpt")
upd = {k: p[k]["raw"].replace("Ρινζέτα", "Ριτσέτα").replace("Ριτζέτα", "Ριτσέτα") for k in ("title", "content", "excerpt")}
r = bot.wp("POST", "posts/65313", upd)
print(65313, r["title"]["rendered"], upd["content"].count("Ριτσέτα"), "mentions")
