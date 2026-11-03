import sqlite3,os,sys
sys.stdout.reconfigure(encoding="utf-8")
db=os.path.join(os.environ["LOCALAPPDATA"],"youtube-gemini-channel-summarizer","tasks.sqlite3")
c=sqlite3.connect(db)
print("defer reasons:", [r for r in c.execute("select reason,count(*) from videos group by reason")])
print("state:", [r for r in c.execute("select state,count(*) from videos group by state")])
