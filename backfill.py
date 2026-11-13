"""Resumable public metadata collection; no Gemini operations."""
import json
import math
import os
import re
import time
import urllib.parse
import urllib.request
import urllib.error
import uuid

METADATA_INTERVAL_ENV = 'YOUTUBE_METADATA_INTERVAL_SECONDS'
DEFAULT_METADATA_INTERVAL = 1.0
MAX_BATCH_SIZE = 50
MAX_API_RETRIES = 5
MAX_BACKOFF_SECONDS = 60

def _api_error_label(code, body=''):
    """Return a bounded, key-free API error label for task diagnostics."""
    reason = ''
    try:
        payload = json.loads(body or '{}')
        error = payload.get('error') or {}
        reasons = [str(item.get('reason', '')).strip() for item in error.get('errors', [])]
        reason = next((item for item in reasons if item), '')
        reason = reason or str(error.get('message', '')).strip()
    except (TypeError, ValueError, AttributeError):
        reason = ''
    reason = re.sub(r'[^A-Za-z0-9_.:-]+', '_', reason)[:120]
    return f'youtube_api_http_{int(code)}' + (f':{reason}' if reason else '')

SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata_jobs(id TEXT PRIMARY KEY,channel_id TEXT NOT NULL,
 created REAL NOT NULL,status TEXT NOT NULL DEFAULT 'ready');
CREATE TABLE IF NOT EXISTS metadata_items(job TEXT NOT NULL,video TEXT NOT NULL,
 state TEXT NOT NULL,reason TEXT,release_date TEXT,upload_date TEXT,date_source TEXT,
 checked REAL,attempts INTEGER NOT NULL DEFAULT 0,retry_after REAL NOT NULL DEFAULT 0,
 PRIMARY KEY(job,video));
CREATE TABLE IF NOT EXISTS metadata_access(id INTEGER PRIMARY KEY CHECK(id=1),
 next_at REAL NOT NULL DEFAULT 0,count INTEGER NOT NULL DEFAULT 0,blocked TEXT,
 interval REAL NOT NULL DEFAULT 1,consecutive_success INTEGER NOT NULL DEFAULT 0,
 blocked_until REAL NOT NULL DEFAULT 0);
INSERT OR IGNORE INTO metadata_access(id) VALUES(1);
"""

def migrate(s):
    columns={x['name'] for x in s.db.execute('PRAGMA table_info(metadata_items)')}
    with s.tx():
        if 'attempts' not in columns:
            s.db.execute('ALTER TABLE metadata_items ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0')
        if 'retry_after' not in columns:
            s.db.execute('ALTER TABLE metadata_items ADD COLUMN retry_after REAL NOT NULL DEFAULT 0')
        access_columns={x['name'] for x in s.db.execute('PRAGMA table_info(metadata_access)')}
        for name, declaration in [('interval','REAL NOT NULL DEFAULT 1'),
                                  ('consecutive_success','INTEGER NOT NULL DEFAULT 0'),
                                  ('blocked_until','REAL NOT NULL DEFAULT 0')]:
            if name not in access_columns:
                s.db.execute(f'ALTER TABLE metadata_access ADD COLUMN {name} {declaration}')

def configured_interval():
    try:
        return max(0.0, float(os.environ.get(METADATA_INTERVAL_ENV, DEFAULT_METADATA_INTERVAL)))
    except ValueError:
        return DEFAULT_METADATA_INTERVAL

def gate(s):
    from ytg import Stop
    with s.tx():
        row=s.db.execute('SELECT * FROM metadata_access WHERE id=1').fetchone()
        now=time.time()
        if row['blocked'] and row['blocked_until'] > now:
            raise Stop('YouTube metadata paused: '+row['blocked'])
        if row['blocked']:
            s.db.execute('UPDATE metadata_access SET blocked=NULL,blocked_until=0 WHERE id=1')
            row=s.db.execute('SELECT * FROM metadata_access WHERE id=1').fetchone()
        wait=math.ceil(row['next_at']-time.time())
        if wait>0:
            raise Stop('metadata_wait:'+str(wait))
        count=row['count']+1
        interval=max(configured_interval(), float(row['interval'] or 0))
        s.db.execute('UPDATE metadata_access SET count=?,next_at=? WHERE id=1',
                     (count,time.time()+interval))

def record_api_success(s):
    with s.tx():
        row=s.db.execute('SELECT interval,consecutive_success FROM metadata_access WHERE id=1').fetchone()
        base=configured_interval()
        successes=int(row['consecutive_success'] or 0)+1
        # Recover gradually after a stable response stream; never go below config.
        interval=max(base, float(row['interval'] or base) * (0.8 if successes >= 3 else 1.0))
        s.db.execute('UPDATE metadata_access SET interval=?,consecutive_success=? WHERE id=1',
                     (interval,successes))

def record_rate_limit(s, retry_after=None):
    with s.tx():
        row=s.db.execute('SELECT interval,consecutive_success FROM metadata_access WHERE id=1').fetchone()
        interval=max(configured_interval(), float(row['interval'] or configured_interval()))
        interval=min(MAX_BACKOFF_SECONDS, max(configured_interval(), interval * 2))
        delay=max(interval, float(retry_after or 0))
        s.db.execute('''UPDATE metadata_access
                        SET interval=?,consecutive_success=0,next_at=?,blocked='temporary_rate_limit',blocked_until=?''',
                     (interval,time.time()+delay,time.time()+delay))
    return delay

def quota_reset_timestamp():
    from datetime import datetime, timedelta, timezone
    from zoneinfo import ZoneInfo
    pacific=ZoneInfo('America/Los_Angeles')
    now=datetime.now(timezone.utc).astimezone(pacific)
    tomorrow=(now+timedelta(days=1)).date()
    return datetime.combine(tomorrow, datetime.min.time(), tzinfo=pacific).timestamp()

def record_quota_exhausted(s):
    reset=quota_reset_timestamp()
    with s.tx():
        s.db.execute("UPDATE metadata_access SET blocked='quota_exhausted',blocked_until=?,next_at=? WHERE id=1",
                     (reset,reset))
    return reset

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
        from ytg import youtube_data_api_key
        key, _ = youtube_data_api_key()
        if key and not flat and extractor is None:
            parsed = urllib.parse.urlparse(url)
            video = urllib.parse.parse_qs(parsed.query).get('v', [None])[0]
            if not video:
                raise ValueError('YouTube URL has no video ID')
            item = api_batch(s, [video]).get(video)
            if not item:
                raise ValueError('YouTube Data API returned no item')
            snippet = item.get('snippet') or {}
            published = snippet.get('publishedAt') or ''
            return {
                'id': item.get('id'),
                'channel_id': snippet.get('channelId'),
                'title': snippet.get('title') or '',
                'release_date': published[:10].replace('-', '') or None,
                'upload_date': published[:10].replace('-', '') or None,
                'live_status': 'not_live',
            }
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
        latest=s.db.execute("SELECT id FROM metadata_jobs WHERE channel_id=? ORDER BY created DESC LIMIT 1",(cid,)).fetchone()
        current_count=s.db.execute("SELECT count(*) FROM videos WHERE channel_id=? AND history=1",(cid,)).fetchone()[0]
        old = latest if latest and s.db.execute(
            "SELECT count(*) FROM metadata_items WHERE job=?",(latest['id'],)
        ).fetchone()[0] == current_count else None
        # A completed job is a snapshot. Rebuild it when a history
        # reclassification or a later full inventory adds videos.
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
    complete=unresolved==0
    channel=s.channel(r['channel_id'])
    next_action=(
        '日期补齐已结束。请向用户报告补齐数量、覆盖率和 unavailable/review 项；'
        '日期标题格式下可开始历史视频总结。用户确认开始后，先创建本频道 Codex Goal，'
        '再核对 Gemini Notebook、账号、频道状态和租约，然后启动 history run。'
        if complete else '继续 metadata-backfill run，直到 complete=true。'
    )
    return dict(r,total=total,counts=counts,retry_wait=waiting,complete=complete,
                channel_name=channel['name'],title_format=channel['title_format'],
                next_action=next_action,
                reused=s.db.execute("SELECT count(*) FROM metadata_items WHERE job=? AND reason='cache'",(job,)).fetchone()[0],
                exceptions=[dict(x) for x in s.db.execute("SELECT video,state,reason FROM metadata_items WHERE job=? AND (state IN ('review','unavailable') OR reason IS NOT NULL AND reason<>'cache')",(job,))],
                all_dates_complete=counts.get('complete',0)==total,
                date_coverage=counts.get('complete',0)/total if total else 1,
                access=dict(s.db.execute('SELECT * FROM metadata_access WHERE id=1').fetchone()))

def api_batch(s, ids):
    """Fetch up to 50 videos with one official YouTube Data API request."""
    from ytg import Stop, MetadataFailure
    from ytg import youtube_data_api_key
    key, _ = youtube_data_api_key()
    if not key or not ids:
        raise Stop('YOUTUBE_DATA_API_KEY is required for API batch mode')
    ids=list(dict.fromkeys(ids))
    if len(ids)>MAX_BATCH_SIZE:
        raise ValueError('YouTube Data API batch is limited to 50 video IDs')
    api_url = 'https://www.googleapis.com/youtube/v3/videos?' + urllib.parse.urlencode({
        'part': 'snippet', 'id': ','.join(ids), 'key': key,
    })
    for attempt in range(MAX_API_RETRIES):
        while True:
            try:
                gate(s)
                break
            except Stop as exc:
                if not str(exc).startswith('metadata_wait:'):
                    raise
                time.sleep(min(30, int(str(exc).split(':')[1])))
        try:
            with urllib.request.urlopen(api_url, timeout=30) as response:
                payload = json.loads(response.read().decode('utf-8'))
            error=payload.get('error') or {}
            if error:
                reasons=' '.join(str(e.get('reason','')) for e in error.get('errors',[])).lower()
                message=(error.get('message') or '').lower()
                if any(x in reasons+' '+message for x in ('quotaexceeded','dailylimitexceeded','dailylimit')):
                    reset=record_quota_exhausted(s)
                    raise MetadataFailure('youtube_api_quota_exhausted:'+str(int(reset)))
                if int(error.get('code',500)) in {429,503}:
                    raise urllib.error.HTTPError(api_url, int(error.get('code',500)),
                                                 'YouTube API error', None, None)
                raise MetadataFailure(_api_error_label(error.get('code',500), json.dumps(payload)))
            record_api_success(s)
            return {item.get('id'): item for item in payload.get('items', [])}
        except urllib.error.HTTPError as exc:
            body=''
            try:
                body=exc.read().decode('utf-8','ignore').lower()
            except Exception:
                pass
            if exc.code==403 and any(x in body for x in ('quotaexceeded','dailylimitexceeded','dailylimit')):
                reset=record_quota_exhausted(s)
                raise MetadataFailure('youtube_api_quota_exhausted:'+str(int(reset))) from exc
            if exc.code==429 or exc.code==503:
                retry_after=None
                try: retry_after=float(exc.headers.get('Retry-After')) if exc.headers else None
                except (TypeError,ValueError): pass
                delay=record_rate_limit(s,retry_after)
                if attempt+1<MAX_API_RETRIES:
                    time.sleep(delay)
                    continue
                raise MetadataFailure('youtube_rate_limited') from exc
            if exc.code in {401,403}:
                with s.tx():
                    s.db.execute('UPDATE metadata_access SET blocked=? WHERE id=1',
                                 (_api_error_label(exc.code, body),))
            raise MetadataFailure(_api_error_label(exc.code, body)) from exc

def batch_step(s,job):
    """Process a maximum API page of 50 pending IDs in one transaction."""
    from ytg import Stop, date_value
    r=report(s,job); cid=r['channel_id']; now=time.time()
    with s.tx():
        if r['status']=='review': raise Stop('Metadata job requires review')
        if s.pause_for(cid): raise Stop('Paused: '+s.pause_for(cid))
        if any(x['expires']>now for x in s.conflicting_leases(cid)):
            raise Stop('Another worker owns the queue scope')
        s.db.execute('UPDATE metadata_items SET state=\'pending\' WHERE job=? AND state=\'fetching\'',(job,))
        rows=s.db.execute('''SELECT v.id FROM metadata_items m JOIN videos v
                             ON v.id=m.video AND v.channel_id=?
                             WHERE m.job=? AND m.state='pending' AND m.retry_after<=?
                             ORDER BY m.attempts,v.found LIMIT 50''',(cid,job,now)).fetchall()
        if not rows:
            s.db.execute("UPDATE metadata_jobs SET status='complete' WHERE id=? AND NOT EXISTS (SELECT 1 FROM metadata_items WHERE job=? AND state IN ('pending','fetching','review'))",(job,job))
            return report(s,job)
        ids=[row['id'] for row in rows]
        s.db.execute('INSERT OR REPLACE INTO lease(scope,run_id,expires,last_seen) VALUES(?,?,?,?)',(cid,job,now+900,now))
        s.db.executemany("UPDATE metadata_items SET state='fetching' WHERE job=? AND video=?",[(job,vid) for vid in ids])
    try:
        items=api_batch(s,ids)
        checked=time.time()
        with s.tx():
            s.check_lease(job)
            for vid in ids:
                item=items.get(vid); snippet=(item or {}).get('snippet') or {}
                if not item:
                    state,reason='unavailable','not_found'
                    date=None
                elif snippet.get('channelId')!=cid:
                    state,reason='review','identity_mismatch'; date=None
                else:
                    raw=snippet.get('publishedAt') or ''
                    try: date=date_value(raw[:10].replace('-',''))
                    except (Stop,TypeError): state,reason='unavailable','date_not_public'; date=None
                    else: state,reason='complete',None
                s.db.execute('''UPDATE metadata_items SET state=?,reason=?,release_date=?,upload_date=?,
                                date_source=?,checked=? WHERE job=? AND video=?''',
                             (state,reason,date,date,'publishedAt' if date else None,checked,job,vid))
                if state=='complete':
                    s.db.execute('''UPDATE videos SET published=?,metadata_checked=?,
                                    title=CASE WHEN state='pending' THEN ? ELSE title END,
                                    title_source=CASE WHEN state='pending' THEN 'api' ELSE title_source END
                                    WHERE channel_id=? AND id=? AND (chat IS NULL OR published IS NULL)''',
                                 (date,checked,snippet.get('title') or '',cid,vid))
        return {'job':job,'state':'batch_complete','count':len(ids),'matched':len(items)}
    except Exception as exc:
        category = str(exc)
        if category in {'youtube_rate_limited', 'youtube_auth_required', 'metadata_network_error'} or category.startswith('youtube_api_quota_exhausted'):
            reason = 'quota_exhausted' if category.startswith('youtube_api_quota_exhausted') else category
            with s.tx():
                for vid in ids:
                    s.db.execute("UPDATE metadata_items SET state='pending',reason=? WHERE job=? AND video=?",
                                 (reason,job,vid))
            raise
        diagnostic = category if category.startswith('youtube_api_http_') else 'metadata_error'
        with s.tx():
            for vid in ids:
                attempts=s.db.execute('SELECT attempts FROM metadata_items WHERE job=? AND video=?',(job,vid)).fetchone()[0]+1
                backoff=(60,300,900)[min(attempts-1,2)]
                s.db.execute("UPDATE metadata_items SET state='pending',reason=?,attempts=?,retry_after=?,checked=? WHERE job=? AND video=?",(diagnostic,attempts,time.time()+backoff,time.time(),job,vid))
        raise
    finally:
        with s.tx(): s.db.execute('DELETE FROM lease WHERE run_id=?',(job,))

def step(s,job,extractor=None):
    from ytg import youtube_data_api_key
    if youtube_data_api_key()[0] and extractor is None:
        return batch_step(s,job)
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
