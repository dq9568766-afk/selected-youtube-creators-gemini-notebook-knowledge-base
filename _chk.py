import sqlite3,os,sys
sys.stdout.reconfigure(encoding="utf-8")
db=os.path.join(os.environ["LOCALAPPDATA"],"youtube-gemini-channel-summarizer","tasks.sqlite3")
c=sqlite3.connect(db)
tabs=[r[0] for r in c.execute("select name from sqlite_master where type='table'")]
print(tabs)
cols=[r[1] for r in c.execute("PRAGMA table_info(channels)")]
print(cols)
for r in c.execute("select * from channels"):
    print(r)
