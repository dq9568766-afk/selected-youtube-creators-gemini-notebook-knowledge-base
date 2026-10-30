"""Resumable public metadata collection; no Gemini operations."""
import json
import math
import time
import uuid

SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata_jobs(id TEXT PRIMARY KEY,channel_id TEXT NOT NULL,
 created REAL NOT NULL,status TEXT NOT NULL DEFAULT 'ready');
CREATE TABLE IF NOT EXISTS metadata_items(job TEXT NOT NULL,video TEXT NOT NULL,
 state TEXT NOT NULL,reason TEXT,release_date TEXT,upload_date TEXT,date_source TEXT,
 checked REAL,attempts INTEGER NOT NULL DEFAULT 0,retry_after REAL NOT NULL DEFAULT 0,
 PRIMARY KEY(job,video));
CREATE TABLE IF NOT EXISTS metadata_access(id INTEGER PRIMARY KEY CHECK(id=1),
 next_at REAL NOT NULL DEFAULT 0,count INTEGER NOT NULL DEFAULT 0,blocked TEXT);
INSERT OR IGNORE INTO metadata_access(id) VALUES(1);
"""

def migrate(s):
    columns={x['name'] for x in s.db.execute('PRAGMA table_info(metadata_items)')}
    with s.tx():
        if 'attempts' not in columns:
            s.db.execute('ALTER TABLE metadata_items ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0')
        if 'retry_after' not in columns:
            s.db.execute('ALTER TABLE metadata_items ADD COLUMN retry_after REAL NOT NULL DEFAULT 0')

def gate(s):
    from ytg import Stop
    with s.tx():
        row=s.db.execute('SELECT * FROM metadata_access WHERE id=1').fetchone()
        if row['blocked']:
            raise Stop('YouTube metadata paused: '+row['blocked'])
        wait=math.ceil(row['next_at']-time.time())
        if wait>0:
            raise Stop('metadata_wait:'+str(wait))
        count=row['count']+1
        s.db.execute('UPDATE metadata_access SET count=?,next_at=? WHERE id=1',
                     (count,time.time()+(300 if count%50==0 else 10)))

def fetch(s,url,flat=False,limit=None,extractor=None):
    from ytg import extract,metadata_failure,MetadataFailure,Stop
    while True:
        try:
            gate(s)
            break
        except Stop as exc:
            if not str(exc).startswith('metadata_wait:'): raise
            time.sleep(min(30,int(str(exc).split(':')[1])))
    try:
        return (extractor or extract)(url,flat,limit) if flat else (extractor or extract)(url,False)
    except Exception as exc:
        category=metadata_failure(exc)
        if isinstance(exc,MetadataFailure): category=str(exc)
        if category in {'youtube_rate_limited','youtube_auth_required','metadata_network_error'}:
            with s.tx():
                s.db.execute('UPDATE metadata_access SET blocked=? WHERE id=1',(category,))
        raise

def start(s,cid):
    from ytg import Stop,date_value
    def cached_date(row):
        try:
            date_value(row['published'])
            return True
        except (Stop, TypeError):
            return False
    def repair_cached_dates(job):
        repaired=0
        with s.tx():
            rows=s.db.execute('''SELECT m.video,m.state,m.reason,v.published
                                 FROM metadata_items m JOIN videos v ON v.channel_id=? AND v.id=m.video
                                 WHERE m.job=? AND m.state='complete' AND m.reason='cache' ''',(cid,job)).fetchall()
            for row in rows:
                if not cached_date(row):
                    s.db.execute('''UPDATE metadata_items
                                    SET state='pending',reason=NULL,release_date=NULL,upload_date=NULL,
                                        date_source=NULL,checked=NULL
                                    WHERE job=? AND video=?''',(job,row['video']))
                    repaired+=1
            if repaired:
                s.db.execute("UPDATE metadata_jobs SET status='ready' WHERE id=?",(job,))
        return repaired
    old_id=None
    with s.tx():
        channel=s.channel(cid)
        if not channel['initialized']: raise Stop('Complete initial inventory first')
        if s.pause_for(cid): raise Stop('Paused: '+s.pause_for(cid))
        if any(x['expires']>time.time() for x in s.conflicting_leases(cid)):
            raise Stop('Another worker owns the queue scope')
        old=s.db.execute("SELECT id FROM metadata_jobs WHERE channel_id=? AND status<>'complete' ORDER BY created DESC LIMIT 1",(cid,)).fetchone()
        if old:
            old_id=old['id']
        else:
            job=uuid.uuid4().hex
            s.db.execute('INSERT INTO metadata_jobs(id,channel_id,created) VALUES(?,?,?)',(job,cid,time.time()))
            s.db.execute("INSERT INTO runs(id,mode,channel_id) VALUES(?,'metadata',?)",(job,cid))
            for v in s.db.execute('SELECT * FROM videos WHERE channel_id=? AND history=1',(cid,)).fetchall():
                has_date=cached_date(v)
                s.db.execute('INSERT INTO metadata_items(job,video,state,reason) VALUES(?,?,?,?)',
                             (job,v['id'],'complete' if has_date else 'pending','cache' if has_date else None))
    if old_id:
        repair_cached_dates(old_id)
        return report(s,old_id)
    return report(s,job)

def report(s,job):
    from ytg import Stop
    r=s.db.execute('SELECT * FROM metadata_jobs WHERE id=?',(job,)).fetchone()
    if not r: raise Stop('Unknown metadata job')
    counts={x[0]:x[1] for x in s.db.execute('SELECT state,count(*) FROM metadata_items WHERE job=? GROUP BY state',(job,))}
    total=sum(counts.values())
    unresolved=counts.get('pending',0)+counts.get('fetching',0)+counts.get('review',0)
    waiting=s.db.execute("SELECT count(*) FROM metadata_items WHERE job=? AND state='pending' AND retry_after>?",(job,time.time())).fetchone()[0]
    return dict(r,total=total,counts=counts,retry_wait=waiting,complete=unresolved==0,
                reused=s.db.execute("SELECT count(*) FROM metadata_items WHERE job=? AND reason='cache'",(job,)).fetchone()[0],
                exceptions=[dict(x) for x in s.db.execute("SELECT video,state,reason FROM metadata_items WHERE job=? AND (state IN ('review','unavailable') OR reason IS NOT NULL AND reason<>'cache')",(job,))],
                all_dates_complete=counts.get('complete',0)==total,
                date_coverage=counts.get('complete',0)/total if total else 1,
                access=dict(s.db.execute('SELECT * FROM metadata_access WHERE id=1').fetchone()))

def step(s,job,extractor=None):
    from ytg import Stop,date_value,metadata_failure,MetadataFailure
    r=report(s,job); cid=r['channel_id']
    with s.tx():
        if r['status']=='review': raise Stop('Metadata job requires review')
        if s.pause_for(cid): raise Stop('Paused: '+s.pause_for(cid))
        conflicts=s.conflicting_leases(cid)
        if any(x['expires']>time.time() for x in conflicts):
            raise Stop('Another worker owns the queue scope')
        for x in conflicts:
            if x['run_id']!=job: s.db.execute('DELETE FROM lease WHERE run_id=?',(x['run_id'],))
        s.db.execute('INSERT OR REPLACE INTO lease(scope,run_id,expires,last_seen) VALUES(?,?,?,?)',(cid,job,time.time()+900,time.time()))
        # A previous process may have died during extraction; no submission exists.
        s.db.execute("UPDATE metadata_items SET state='pending' WHERE job=? AND state='fetching'",(job,))
        v=s.db.execute("SELECT v.* FROM metadata_items m JOIN videos v ON v.id=m.video AND v.channel_id=? WHERE m.job=? AND m.state='pending' AND m.retry_after<=? ORDER BY m.attempts,v.found LIMIT 1",(cid,job,time.time())).fetchone()
        if not v:
            waiting=s.db.execute("SELECT min(retry_after) FROM metadata_items WHERE job=? AND state='pending'",(job,)).fetchone()[0]
            if waiting:
                s.db.execute('DELETE FROM lease WHERE run_id=?',(job,))
                return {'job':job,'state':'retry_wait','wait_seconds':max(0,math.ceil(waiting-time.time()))}
            s.db.execute("UPDATE metadata_jobs SET status=? WHERE id=?",('complete' if r['complete'] else 'review',job))
            s.db.execute('DELETE FROM lease WHERE run_id=?',(job,))
            return report(s,job)
        s.db.execute("UPDATE metadata_items SET state='fetching' WHERE job=? AND video=?",(job,v['id']))
    try:
        info=fetch(s,'https://www.youtube.com/watch?v='+v['id'],extractor=extractor)
        state,reason='complete',None
        if info.get('id')!=v['id'] or info.get('channel_id')!=cid:
            state,reason='review','identity_mismatch'
        elif info.get('live_status') in {'is_live','is_upcoming','post_live'}:
            state,reason='unavailable','live_not_ready'
        else:
            try: date=date_value(info.get('release_date') or info.get('upload_date'))
            except (Stop,TypeError): state,reason='unavailable','date_not_public'
            if not isinstance(info.get('title'),str) or not info['title'].strip():
                state,reason='review','title_invalid'
        with s.tx():
            s.check_lease(job)
            s.db.execute('UPDATE metadata_items SET state=?,reason=?,release_date=?,upload_date=?,date_source=?,checked=? WHERE job=? AND video=?',
                         (state,reason,info.get('release_date'),info.get('upload_date'),'release_date' if info.get('release_date') else 'upload_date',time.time(),job,v['id']))
            if state=='complete':
                # Preserve names already used by archived chats.
                s.db.execute("UPDATE videos SET published=?,metadata_checked=?,title=CASE WHEN state='pending' THEN ? ELSE title END WHERE channel_id=? AND id=? AND (chat IS NULL OR published IS NULL)",(date,time.time(),info['title'],cid,v['id']))
            if state=='review': s.db.execute("UPDATE metadata_jobs SET status='review' WHERE id=?",(job,))
        return {'job':job,'video':v['id'],'state':state,'reason':reason}
    except Exception as exc:
        category=metadata_failure(exc)
        restricted=category=='metadata_error' and any(x in str(exc).lower() for x in ('members-only','private video','video has been removed','video unavailable','video is unavailable'))
        with s.tx():
            if restricted:
                s.db.execute("UPDATE metadata_items SET state='unavailable',reason='restricted',checked=? WHERE job=? AND video=?",(time.time(),job,v['id']))
            elif category=='metadata_error':
                attempts=s.db.execute('SELECT attempts FROM metadata_items WHERE job=? AND video=?',(job,v['id'])).fetchone()[0]+1
                if attempts<3:
                    backoff=(60,300,900)[attempts-1]
                    s.db.execute("UPDATE metadata_items SET state='pending',reason='metadata_error',attempts=?,retry_after=?,checked=? WHERE job=? AND video=?",(attempts,time.time()+backoff,time.time(),job,v['id']))
                    return {'job':job,'video':v['id'],'state':'retry_scheduled','attempts':attempts,'retry_after':backoff}
                s.db.execute("UPDATE metadata_items SET state='review',reason='metadata_error',attempts=?,checked=? WHERE job=? AND video=?",(attempts,time.time(),job,v['id']))
                s.db.execute("UPDATE metadata_jobs SET status='review' WHERE id=?",(job,))
            else:
                s.db.execute('UPDATE metadata_items SET state=?,reason=? WHERE job=? AND video=?',('pending',category,job,v['id']))
        if restricted: return {'job':job,'video':v['id'],'state':'unavailable','reason':'restricted'}
        if isinstance(exc,Stop): raise
        raise MetadataFailure(category) from exc
    finally:
        with s.tx(): s.db.execute('DELETE FROM lease WHERE run_id=?',(job,))
