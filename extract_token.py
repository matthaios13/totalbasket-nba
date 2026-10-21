"""Pull the full Claude OAuth token out of a `script` capture of `claude setup-token`.

The CLI draws the token over two screen lines with cursor-movement codes in between,
so plain copy/paste or line-based parsing only gets the first part.
"""
import re
import sys

raw = open(sys.argv[1], "rb").read().decode("utf-8", "ignore")
text = re.sub(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)", "", raw)          # hyperlinks / titles
text = re.sub(r"\x1b\[[0-9;?<>=]*[ -/]*[@-~]", "\n", text)            # colours, cursor moves
text = re.sub(r"\x1b[@-_]", "", text).replace("\r", "\n")

i = text.rfind("sk-ant-oat")
if i < 0:
    sys.exit()
tok = ""
for line in text[i:].split("\n"):
    s = line.strip()
    if not s:
        if len(tok) >= 100:
            break             # full token (~108 chars) already collected
        continue              # cursor codes leave empty fragments between the two halves
    elif re.fullmatch(r"[A-Za-z0-9_-]+", s):
        tok += s
        continue
    if tok and s:
        break                 # reached the next sentence ("Store this token ...")
print(tok)
