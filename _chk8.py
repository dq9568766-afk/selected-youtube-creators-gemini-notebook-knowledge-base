import sqlite3,os,sys,re
sys.stdout.reconfigure(encoding="utf-8")
db=os.path.join(os.environ["LOCALAPPDATA"],"youtube-gemini-channel-summarizer","tasks.sqlite3")
c=sqlite3.connect(db)
def is_en(t): return bool(t) and not any('一'<=ch<='鿿' for ch in t)
for st in ("verified","deferred","pending","summarized","renamed","submitting"):
    rows=c.execute("select id,title,state,topic,chat from videos where channel_id='UCBKDRq35-L8xev4O7ZqBeLg' and state=?",(st,)).fetchall()
    en=[r for r in rows if is_en(r[1])]
    print(st, "total",len(rows),"english-title",len(en))
    if st=="verified":
        for r in en: print("   ",r[0],r[3],"|",r[1][:80],"|",r[4])
