import sqlite3,os
db=sqlite3.connect(os.environ['LOCALAPPDATA']+'/youtube-gemini-channel-summarizer/tasks.sqlite3')
db.row_factory=sqlite3.Row
print([x[1] for x in db.execute("pragma table_info(videos)")])
print("---")
for r in db.execute("select * from videos where id in ('Dn9RKM_SaeQ','0-yPYKjdtNg')"):
    d=dict(r); print({k:d[k] for k in d if k in ('id','title','channel_id','state','chat_id','notebook_id','run_id','release_date','upload_date')})
