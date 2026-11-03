import re
s=open("ytg.py",encoding="utf-8").read()
for pat in ["def insert(", "def metadata(", "def metadata_fresh", "def api_batch", "def title_for", "def cmd_metadata_backfill"]:
    for m in re.finditer(re.escape(pat), s):
        i=s.rfind("\n",0,m.start())
        print(pat, "->", s[:m.start()].count("\n")+1)
