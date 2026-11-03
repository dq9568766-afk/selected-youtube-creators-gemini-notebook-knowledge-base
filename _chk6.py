import sqlite3,os,sys
sys.stdout.reconfigure(encoding="utf-8")
db=os.path.join(os.environ["LOCALAPPDATA"],"youtube-gemini-channel-summarizer","tasks.sqlite3")
c=sqlite3.connect(db)
for r in c.execute("select id,channel_id,title,state,topic,chat from videos where id='0-yPYKjdtNg'"): print(r)
print("--- 王剑 channel: chats recently set ---")
for r in c.execute("select id,state,topic,chat from videos where channel_id='UC8UCbiPrm2zN9nZHKdTevZA' and chat is not null order by rowid desc limit 10"): print(r)
