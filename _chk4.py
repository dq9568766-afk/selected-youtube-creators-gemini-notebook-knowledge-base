import sqlite3,os
db=sqlite3.connect(os.environ['LOCALAPPDATA']+'/youtube-gemini-channel-summarizer/tasks.sqlite3')
db.row_factory=sqlite3.Row
print("channels:")
for r in db.execute("select c.id,c.name,count(v.id) n, sum(case when v.state='pending' then 1 else 0 end) pending from channels c left join videos v on v.channel_id=c.id group by c.id"):
    print(" ", r['id'], r['name'], 'total',r['n'],'pending',r['pending'])
