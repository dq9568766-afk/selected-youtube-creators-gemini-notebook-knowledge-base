import sqlite3,os,sys
sys.stdout.reconfigure(encoding="utf-8")
db=os.path.join(os.environ["LOCALAPPDATA"],"youtube-gemini-channel-summarizer","tasks.sqlite3")
c=sqlite3.connect(db)
print("== 王剑 channel title samples ==")
for r in c.execute("select id,title,state from videos where channel_id='UC8UCbiPrm2zN9nZHKdTevZA' limit 8"):
    print(r)
print("== does 'Police Backend' appear under 王剑 rows? ==")
for r in c.execute("select id,channel_id,title from videos where title like '%Police Backend%' or title like '%Wenchuan Earthquake%' or title like '%Cognitive Decline%'"):
    print(r)
print("== 王志安 rows with Chinese titles count ==")
tot=zh=0
for (t,) in c.execute("select title from videos where channel_id='UCBKDRq35-L8xev4O7ZqBeLg'"):
    tot+=1
    if t and any('一'<=ch<='鿿' for ch in t): zh+=1
print("total",tot,"with-chinese",zh)
