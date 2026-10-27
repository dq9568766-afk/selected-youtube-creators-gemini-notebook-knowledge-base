"""Task metadata only. Browser actions belong to the existing Codex UI worker.

No Gemini API, browser profile access, transcripts, summaries, or log files.
"""
from __future__ import annotations

import argparse
import contextlib
from datetime import datetime
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
from urllib.parse import urlsplit
import uuid

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS channels(
 id TEXT PRIMARY KEY, url TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
 notebook TEXT UNIQUE, initialized INTEGER NOT NULL DEFAULT 0,
 last_full TEXT, last_check TEXT, turn INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS scans(
 channel_id TEXT NOT NULL REFERENCES channels(id), tab TEXT NOT NULL,
 count INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL,
 checked TEXT NOT NULL, PRIMARY KEY(channel_id,tab));
CREATE TABLE IF NOT EXISTS runs(
 id TEXT PRIMARY KEY, mode TEXT NOT NULL, schedule_key TEXT UNIQUE,
 armed INTEGER NOT NULL DEFAULT 0, finished INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS lease(
 singleton INTEGER PRIMARY KEY CHECK(singleton=1), run_id TEXT NOT NULL,
 expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS videos(
 channel_id TEXT NOT NULL REFERENCES channels(id), id TEXT NOT NULL,
 title TEXT NOT NULL, published TEXT, history INTEGER NOT NULL,
 found INTEGER NOT NULL, seen TEXT NOT NULL, missing INTEGER NOT NULL DEFAULT 0,
 state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN
 ('pending','reserved','submitting','submitted','summarized','renamed','verified','uncertain','blocked','deferred')),
 chat TEXT UNIQUE, topic TEXT, reason TEXT, owner TEXT, metadata_checked REAL,
 PRIMARY KEY(channel_id,id));
CREATE INDEX IF NOT EXISTS videos_owner_state ON videos(owner,state,found);
CREATE INDEX IF NOT EXISTS videos_queue ON videos(history,state,found);
"""
METADATA_TTL = 3600
SCAN_WINDOW = 50
SCAN_OVERLAP = 10
REASONS = {'quota','login','captcha','ui_changed','network','unknown_submit',
           'source_unavailable','metadata_unavailable','live_not_ready','ambiguous_match','save_unverified'}

class Stop(Exception):
    pass

def now():
    # Deliberately use the Windows local wall clock, not a fixed UTC offset.
    return datetime.now().astimezone()

def default_db_path():
    """Keep private task state outside repositories and synced folders."""
    if os.name == 'nt':
        base = Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local')
    else:
        base = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local' / 'state')
    return base / 'youtube-gemini-channel-summarizer' / 'tasks.sqlite3'

def channel_url(value):
    p = urlsplit(value)
    if p.scheme != 'https' or p.hostname not in {'youtube.com','www.youtube.com'}:
        raise Stop('Expected an https YouTube channel URL')
    path = p.path.rstrip('/')
    if not re.fullmatch(r'/(@[\w.\-]+|channel/UC[\w-]{22})', path):
        raise Stop('Use a channel handle or /channel/UC... URL without a tab suffix')
    return 'https://www.youtube.com' + path

def gemini_url(value, kind):
    p = urlsplit(value)
    pattern = r'/notebook/[0-9a-f-]{36}' if kind == 'notebook' else r'/app/[0-9a-f]{16}'
    if p.scheme != 'https' or p.netloc != 'gemini.google.com' or not re.fullmatch(pattern,p.path) or p.query or p.fragment:
        raise Stop('Invalid Gemini ' + kind + ' URL')
    return value

def date_value(value):
    if not value or not re.fullmatch(r'\d{8}',value):
        raise Stop('A real publication date is required')
    try:
        datetime.strptime(value,'%Y%m%d')
    except ValueError as exc:
        raise Stop('Invalid publication date') from exc
    return value

def title_for(row, topic=None):
    topic = topic or row['topic']
    if not topic or not re.fullmatch(r'[\u3400-\u4dbf\u4e00-\u9fff]{1,5}', topic):
        raise Stop('Topic must contain 1–5 Han characters')
    return f"{date_value(row['published'])}-{topic}-{row['title']}"


def metadata_fresh(row):
    checked = row['metadata_checked']
    return checked is not None and 0 <= time.time()-checked < METADATA_TTL

class Store:
    def __init__(self,path):
        Path(path).parent.mkdir(parents=True,exist_ok=True)
        self.db = sqlite3.connect(path, timeout=10, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript(SCHEMA)
        # Older task databases are upgraded in place without resetting task state.
        columns = {r['name'] for r in self.db.execute('PRAGMA table_info(videos)')}
        if 'metadata_checked' not in columns:
            with self.tx():
                columns = {r['name'] for r in self.db.execute('PRAGMA table_info(videos)')}
                if 'metadata_checked' not in columns:
                    self.db.execute('ALTER TABLE videos ADD COLUMN metadata_checked REAL')

    @contextlib.contextmanager
    def tx(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def setting(self,key,default=''):
        row = self.db.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
        return row[0] if row else default

    def set(self,key,value):
        self.db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(key,str(value)))

    def channel(self,cid):
        r = self.db.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
        if not r:
            raise Stop('Unknown channel')
        return r

    def video(self,cid,vid):
        r = self.db.execute('SELECT * FROM videos WHERE channel_id=? AND id=?',(cid,vid)).fetchone()
        if not r:
            raise Stop('Unknown video')
        return r

    def insert(self,cid,entry,stamp,history):
        vid = entry.get('id')
        if not re.fullmatch(r'[A-Za-z0-9_-]{11}',vid or ''):
            raise Stop('Invalid video id in inventory')
        title = entry.get('title') or vid
        # Keep the title used by an existing chat stable even if YouTube edits it.
        self.db.execute('''INSERT INTO videos(channel_id,id,title,published,history,found,seen)
          VALUES(?,?,?,?,?,?,?) ON CONFLICT(channel_id,id) DO UPDATE SET
          seen=excluded.seen,missing=0,
          metadata_checked=CASE WHEN videos.state='pending' AND videos.title<>excluded.title THEN NULL ELSE videos.metadata_checked END,
          title=CASE WHEN videos.state='pending' THEN excluded.title ELSE videos.title END,
          state=CASE WHEN videos.state='deferred' AND videos.reason='live_not_ready' THEN 'pending' ELSE videos.state END,
          reason=CASE WHEN videos.state='deferred' AND videos.reason='live_not_ready' THEN NULL ELSE videos.reason END''',
          (cid,vid,title,entry.get('upload_date'),int(history),time.time_ns(),stamp))

    def check_lease(self,run):
        r = self.db.execute('SELECT * FROM lease WHERE singleton=1').fetchone()
        if not r or r['run_id'] != run or r['expires'] < time.time():
            raise Stop('Run has no live lease; reopen the same run before continuing')
        if self.setting('pause'):
            raise Stop('Paused: '+self.setting('pause'))
        self.db.execute('UPDATE lease SET expires=? WHERE singleton=1',(time.time()+900,))

    def begin(self,mode,scheduled=False):
        with self.tx():
            if mode not in {'history','incremental'}:
                raise Stop('Unknown run mode')
            if self.setting('pause'):
                raise Stop('Paused: '+self.setting('pause'))
            t = now()
            key = None
            if scheduled:
                if mode!='incremental':
                    raise Stop('Scheduled daily rounds must use incremental mode')
                if (t.hour,t.minute)<(5,0):
                    raise Stop('Not yet the daily local 05:00 due window')
                key = t.date().isoformat()
            old = self.db.execute('SELECT * FROM lease WHERE singleton=1').fetchone()
            if old and old['expires'] > time.time():
                raise Stop('Another worker owns the queue')
            if old:
                self.db.execute("UPDATE videos SET state='pending',owner=NULL WHERE state='reserved'")
                self.db.execute("UPDATE videos SET state='uncertain',reason='unknown_submit' WHERE state='submitting'")
            existing = self.db.execute('SELECT * FROM runs WHERE schedule_key=?',(key,)).fetchone() if key else None
            if existing and existing['finished']:
                raise Stop('This scheduled round is already finished')
            rid = existing['id'] if existing else uuid.uuid4().hex
            if not existing:
                self.db.execute('INSERT INTO runs(id,mode,schedule_key) VALUES(?,?,?)',(rid,mode,key))
            self.db.execute('INSERT OR REPLACE INTO lease VALUES(1,?,?)',(rid,time.time()+900))
            return dict(self.db.execute('SELECT * FROM runs WHERE id=?',(rid,)).fetchone())

    def reserve(self,run):
        with self.tx():
            self.check_lease(run)
            r = self.db.execute('SELECT * FROM runs WHERE id=?',(run,)).fetchone()
            history = int(r['mode']=='history')
            # Finish the active browser action first. Historical summaries can wait
            # for renaming while generation advances; uncertain submissions cannot.
            active = "('reserved','submitting','submitted','renamed','uncertain')" if history else "('reserved','submitting','submitted','summarized','renamed','uncertain')"
            row = self.db.execute(f"SELECT * FROM videos WHERE owner=? AND state IN {active} ORDER BY found LIMIT 1",(run,)).fetchone()
            if row:
                return self.payload(row)
            recover = "('submitting','submitted','renamed','uncertain')" if history else "('submitting','submitted','summarized','renamed','uncertain')"
            row = self.db.execute(f"SELECT * FROM videos WHERE history=? AND state IN {recover} ORDER BY found LIMIT 1",(history,)).fetchone()
            if not row:
                if not history and r['armed']>=20:
                    return {'stop':'round_cap','armed':r['armed']}
                row = self.db.execute('''SELECT v.* FROM videos v JOIN channels c ON c.id=v.channel_id
                  WHERE v.state='pending' AND v.history=? AND c.initialized=1 AND c.notebook IS NOT NULL AND v.missing=0
                  ORDER BY c.turn ASC,COALESCE(v.published,'') DESC,v.found ASC LIMIT 1''',(history,)).fetchone()
                if row:
                    self.db.execute("UPDATE videos SET state='reserved',owner=? WHERE channel_id=? AND id=?",(run,row['channel_id'],row['id']))
                elif history:
                    row = self.db.execute("SELECT * FROM videos WHERE history=1 AND state='summarized' ORDER BY found LIMIT 1").fetchone()
                if not row:
                    return {'stop':'empty'}
            self.db.execute('UPDATE videos SET owner=? WHERE channel_id=? AND id=?',(run,row['channel_id'],row['id']))
            self.db.execute('UPDATE channels SET turn=? WHERE id=?',(time.time_ns(),row['channel_id']))
            return self.payload(self.video(row['channel_id'],row['id']))

    def payload(self,row):
        d = dict(row)
        d['notebook'] = self.channel(row['channel_id'])['notebook']
        d['marker'] = f"YTG:{row['channel_id']}:{row['id']}"
        d['url'] = 'https://www.youtube.com/watch?v='+row['id']
        d['phase'] = 'finalize' if row['state'] in {'summarized','renamed','verified'} else 'generate'
        if row['topic'] and row['published']:
            d['expected_title'] = title_for(row)
        if row['state']=='reserved':
            d['prompt'] = d['url']
        return d

    def daily_due(self):
        t = now()
        if (t.hour,t.minute)<(5,0):
            return False
        r = self.db.execute('SELECT finished FROM runs WHERE schedule_key=?',
                            (t.date().isoformat(),)).fetchone()
        return not r or not r['finished']

    def park(self,run):
        """Release a historical worker at a safe boundary for the daily round."""
        with self.tx():
            self.check_lease(run)
            r = self.db.execute('SELECT mode FROM runs WHERE id=?',(run,)).fetchone()
            if r['mode']!='history':
                raise Stop('Only a historical round can be parked')
            if self.db.execute("SELECT 1 FROM videos WHERE history=1 AND state IN ('submitting','submitted','renamed','uncertain') LIMIT 1").fetchone():
                raise Stop('Resolve the active chat before parking')
            self.db.execute("UPDATE videos SET state='pending',owner=NULL WHERE owner=? AND state='reserved'",(run,))
            self.db.execute("UPDATE videos SET owner=NULL WHERE owner=? AND state='summarized'",(run,))
            self.db.execute('UPDATE runs SET finished=1 WHERE id=?',(run,))
            self.db.execute('DELETE FROM lease WHERE run_id=?',(run,))

    def transition(self,run,cid,vid,action,value=None,evidence=None):
        with self.tx():
            self.check_lease(run)
            v = self.video(cid,vid)
            if v['owner'] != run:
                raise Stop('Item is not owned by this run')
            args = (cid,vid)
            if action=='arm':
                if v['state']!='reserved' or not v['published'] or not metadata_fresh(v):
                    raise Stop('Only a reserved item with verified metadata can be armed')
                r = self.db.execute('SELECT * FROM runs WHERE id=?',(run,)).fetchone()
                if r['mode']=='incremental' and r['armed']>=20:
                    raise Stop('20 submissions already reserved in this round')
                self.db.execute('UPDATE runs SET armed=armed+1 WHERE id=?',(run,))
                self.db.execute("UPDATE videos SET state='submitting' WHERE channel_id=? AND id=?",args)
            elif action=='attach':
                if v['state'] not in {'submitting','uncertain','submitted'}:
                    raise Stop('Attach is only valid after a submission intent or during reconciliation')
                gemini_url(value,'chat')
                if v['chat'] and v['chat']!=value:
                    raise Stop('Do not replace a known chat with a different one')
                if evidence not in {'video-match', 'marker-and-video-match'}:
                    raise Stop('Inspect the original video URL in the chat first; also check the marker in older chats')
                self.db.execute("UPDATE videos SET state='submitted',chat=?,reason=NULL WHERE channel_id=? AND id=?",(value,*args))
            elif action=='summary':
                if v['state']!='submitted' or evidence!='youtube-content-complete':
                    raise Stop('Require a completed, content-backed response in the linked chat')
                title_for(v,value)
                self.db.execute("UPDATE videos SET state='summarized',topic=? WHERE channel_id=? AND id=?",(value,*args))
                self.set('source_failures',0)
            elif action=='renamed':
                if v['state']!='summarized' or value!=title_for(v):
                    raise Stop('Exact title mismatch or summary not completed')
                self.db.execute("UPDATE videos SET state='renamed' WHERE channel_id=? AND id=?",args)
            elif action=='verify':
                if v['state'] not in {'renamed','verified'} or value!=title_for(v) or evidence not in {'reopen-video-summary', 'reload-notebook-reopen-video-summary', 'reload-notebook-reopen-marker-summary'}:
                    raise Stop('Require reopened chat, notebook membership, exact title, original video URL, and summary')
                self.db.execute("UPDATE videos SET state='verified',owner=NULL,reason=NULL WHERE channel_id=? AND id=?",args)
            elif action=='uncertain':
                if v['state'] not in {'submitting','submitted','summarized','renamed','uncertain'}:
                    raise Stop('No submission to reconcile')
                self.db.execute("UPDATE videos SET state='uncertain',reason='unknown_submit' WHERE channel_id=? AND id=?",args)
                self.set('pause','unknown_submit')
            elif action=='defer':
                if value not in {'source_unavailable','metadata_unavailable','live_not_ready'}:
                    raise Stop('Unknown deferral reason')
                if v['state'] not in {'reserved','submitting','submitted','uncertain'}:
                    raise Stop('Cannot defer an already completed summary or repeat a deferral')
                self.db.execute("UPDATE videos SET state='deferred',reason=?,owner=NULL,metadata_checked=NULL WHERE channel_id=? AND id=?",(value,*args))
                if value=='source_unavailable':
                    failures=int(self.setting('source_failures','0'))+1
                    self.set('source_failures',failures)
                    if failures>=3:
                        self.set('pause','source_unavailable')
            elif action=='skip':
                if value not in {'content_mismatch','video_unreadable','summary_failed','other'}:
                    raise Stop('Choose a supported manual-review reason')
                if v['state'] not in {'reserved','submitting','submitted','uncertain'}:
                    raise Stop('Only an unfinished item can be skipped for manual review')
                self.db.execute("UPDATE videos SET state='deferred',reason=?,owner=NULL WHERE channel_id=? AND id=?",
                                ('review:'+value,*args))
            else:
                raise Stop('Unknown transition')
            return self.payload(self.video(cid,vid))

    def end(self,run):
        with self.tx():
            self.check_lease(run)
            unresolved = self.db.execute("SELECT count(*) FROM videos WHERE owner=? AND state IN ('submitting','submitted','summarized','renamed','uncertain')",(run,)).fetchone()[0]
            if unresolved:
                raise Stop('Finish or pause the outstanding chat before ending the round')
            self.db.execute("UPDATE videos SET state='pending',owner=NULL WHERE owner=? AND state='reserved'",(run,))
            self.db.execute('UPDATE runs SET finished=1 WHERE id=?',(run,))
            self.db.execute('DELETE FROM lease WHERE run_id=?',(run,))

    def status(self,verbose=True):
        if not verbose:
            lease = self.db.execute('SELECT run_id,expires FROM lease WHERE expires>?',(time.time(),)).fetchone()
            return {'pause':self.setting('pause'), 'busy':bool(lease),
                    'daily_due':self.daily_due(),
                    'states':[dict(x) for x in self.db.execute('SELECT history,state,count(*) AS count FROM videos GROUP BY history,state')]}
        return {'pause':self.setting('pause'), 'channels':[dict(x) for x in self.db.execute('SELECT * FROM channels')],
                'states':[dict(x) for x in self.db.execute('SELECT state,history,count(*) AS count FROM videos GROUP BY state,history')],
                'scans':[dict(x) for x in self.db.execute('SELECT * FROM scans')],
                'lease':[dict(x) for x in self.db.execute('SELECT * FROM lease')]}

    def report(self,mode='history'):
        if mode not in {'history','incremental'}:
            raise Stop('Unknown report mode')
        history = int(mode=='history')
        counts = {r['state']:r['count'] for r in self.db.execute(
            'SELECT state,count(*) AS count FROM videos WHERE history=? GROUP BY state',(history,))}
        remaining = sum(counts.get(state,0) for state in
                        ('pending','reserved','submitting','submitted','summarized','renamed','uncertain','blocked'))
        if history:
            remaining -= self.db.execute("SELECT count(*) FROM videos WHERE history=1 AND state='pending' AND missing=1").fetchone()[0]
        review = [dict(r) for r in self.db.execute(
            "SELECT channel_id,id,title,reason,chat,missing FROM videos WHERE history=? AND state='deferred' AND reason LIKE 'review:%' ORDER BY found",
            (history,))]
        for item in review:
            item['url'] = 'https://www.youtube.com/watch?v='+item['id']
        deferred = [dict(r) for r in self.db.execute(
            "SELECT reason,count(*) AS count FROM videos WHERE history=? AND state='deferred' GROUP BY reason ORDER BY reason",
            (history,))]
        return {'mode':mode,'complete':remaining==0,'remaining':remaining,
                'counts':counts,'deferred':deferred,'manual_review':review}

class Quiet:
    def debug(self,*a): pass
    def warning(self,*a): pass
    def error(self,*a): pass

def extract(url,flat=False,limit=None):
    from yt_dlp import YoutubeDL
    opts = {'skip_download':True,'extract_flat':flat,'lazy_playlist':True,
            'quiet':True,'no_warnings':True,'logger':Quiet(),
            'ignoreerrors':False,'noplaylist':not flat,'retries':2,'extractor_retries':2,
            'socket_timeout':30,'cachedir':False,'writesubtitles':False,
            'writeautomaticsub':False,'writethumbnail':False,'writeinfojson':False,
            'playlistend':limit}
    with YoutubeDL(opts) as ydl:
        data = ydl.extract_info(url,download=False)
        if not data:
            raise Stop('Empty metadata response')
        if flat:
            # Consume lazy entries while the extractor context remains alive.
            return {**data,'entries':list(data.get('entries',[]))}
        return data

def add_channel(s,url):
    url = channel_url(url)
    info = extract(url+'/videos',True,limit=1)
    cid = info.get('channel_id')
    if not re.fullmatch(r'UC[\w-]{22}',cid or ''):
        raise Stop('Extractor did not return a canonical channel id')
    with s.tx():
        s.db.execute('INSERT INTO channels(id,url,name) VALUES(?,?,?) ON CONFLICT(id) DO NOTHING',
                     (cid,url,info.get('channel') or cid))
    return dict(s.channel(cid))

def scan(s,cid,full=False,fetch=extract,run=None):
    c = s.channel(cid)
    if s.setting('pause'):
        raise Stop('Paused: '+s.setting('pause'))
    if run:
        with s.tx():
            s.check_lease(run)
            round_info = s.db.execute('SELECT schedule_key FROM runs WHERE id=?',(run,)).fetchone()
            day = round_info['schedule_key']
            if (day and (c['last_check'] or '')[:10]==day
                    and (not full or (c['last_full'] or '')[:10]==day)):
                return {'channel_id':cid,'complete':True,'skipped':'already_checked_today','tabs':[]}
    stamp = now().isoformat()
    complete = True
    results = []
    for tab in ('videos','shorts','streams'):
        try:
            windowed = bool(c['initialized'] and not full)
            url = c['url']+'/'+tab
            info = fetch(url,True,limit=SCAN_WINDOW) if windowed else fetch(url,True)
            entries = info.get('entries',[])
            if info.get('channel_id') not in {None,cid}:
                raise Stop('Channel identity changed')
            if windowed and len(entries)>=SCAN_WINDOW:
                # A recent-page overlap avoids re-reading thousands of old entries.
                # No stable overlap means a backlog: fall back to the full tab.
                overlap = all(e and s.db.execute('SELECT 1 FROM videos WHERE channel_id=? AND id=?',
                              (cid,e.get('id'))).fetchone() for e in entries[-SCAN_OVERLAP:])
                if not overlap:
                    info = fetch(url,True)
                    entries = info.get('entries',[])
                    if info.get('channel_id') not in {None,cid}:
                        raise Stop('Channel identity changed')
                    windowed = False
            count = 0
            with s.tx():
                if run: s.check_lease(run)
                for entry in entries:
                    if not entry:
                        raise Stop('Null entry makes the inventory incomplete')
                    s.insert(cid,entry,stamp,not c['initialized'])
                    count += 1
                s.db.execute('INSERT OR REPLACE INTO scans VALUES(?,?,?,?,?)',(cid,tab,count,'ok',stamp))
            results.append({'tab':tab,'status':'ok','count':count,
                            'scope':'recent' if windowed else 'full'})
        except Exception as exc:
            # Only the extractor's explicit absent-tab result is an empty list.
            # Network, auth, pagination, CAPTCHA, and generic errors remain errors.
            absent = bool(re.search(r'does not have a '+tab+r' tab',str(exc),re.I))
            state = 'absent' if absent else 'error'
            complete = complete and absent
            with s.tx():
                if run: s.check_lease(run)
                s.db.execute('INSERT OR REPLACE INTO scans VALUES(?,?,?,?,?)',(cid,tab,0,state,stamp))
            results.append({'tab':tab,'status':state,'count':0})
    with s.tx():
        if run: s.check_lease(run)
        if complete:
            s.db.execute('UPDATE channels SET initialized=1,last_check=? WHERE id=?',(stamp,cid))
            if full or not c['initialized']:
                s.db.execute('UPDATE channels SET last_full=? WHERE id=?',(stamp,cid))
                # Missing means absent from a complete public listing; never delete a task/chat.
                s.db.execute('UPDATE videos SET missing=1 WHERE channel_id=? AND seen<>?',(cid,stamp))
    return {'channel_id':cid,'complete':complete,'tabs':results}

def metadata(s,run,cid,vid,fetch=extract,force=False):
    # Check before doing any network call, then again under the mutation lock.
    with s.tx():
        s.check_lease(run)
        v = s.video(cid,vid)
        if v['owner']!=run or v['state']!='reserved':
            raise Stop('Metadata update only allowed for the current reserved item')
        if not force and metadata_fresh(v):
            return s.payload(v)
    info = fetch('https://www.youtube.com/watch?v='+vid,False)
    if info.get('id')!=vid or info.get('channel_id')!=cid:
        raise Stop('Video/channel identity mismatch')
    if info.get('live_status') in {'is_live','is_upcoming','post_live'}:
        return s.transition(run,cid,vid,'defer','live_not_ready')
    date = date_value(info.get('release_date') or info.get('upload_date'))
    if not isinstance(info.get('title'),str) or not info['title'].strip():
        raise Stop('Metadata did not return an original title')
    with s.tx():
        s.check_lease(run)
        result = s.db.execute('UPDATE videos SET title=?,published=?,metadata_checked=? WHERE channel_id=? AND id=? AND state=\'reserved\' AND owner=?',
                              (info['title'],date,time.time(),cid,vid,run))
        if result.rowcount!=1:
            raise Stop('Reservation changed during metadata lookup')
    return s.payload(s.video(cid,vid))


def prepare(s,run,fetch=extract):
    """Reserve and prepare one item, without ever performing a browser submission."""
    with s.tx():
        s.check_lease(run)
        r = s.db.execute('SELECT mode FROM runs WHERE id=?',(run,)).fetchone()
        active = s.db.execute("SELECT 1 FROM videos WHERE history=1 AND state IN ('submitting','submitted','renamed','uncertain') LIMIT 1").fetchone()
        if r['mode']=='history' and not active and s.daily_due():
            return {'stop':'daily_due','next_action':'park history, run scheduled incremental, then resume history'}
    item = s.reserve(run)
    if item.get('state')=='reserved':
        try:
            return metadata(s,run,item['channel_id'],item['id'],fetch)
        except (Stop,sqlite3.Error):
            raise
        except Exception as exc:
            raise Stop('Metadata lookup failed; item remains reserved; retry prepare without sending') from exc
    return item


def compact(item,ack=False):
    """Keep CLI output task-oriented; full database rows are an explicit opt-in."""
    if 'state' not in item or 'channel_id' not in item:
        return item
    keys = ['channel_id','id','state']
    if ack:
        keys += ['reason']
    else:
        keys += ['phase','notebook','url','chat','reason']
        if item['state']=='reserved':
            keys += ['title','published','prompt']
        if item['state'] in {'summarized','renamed'}:
            keys += ['expected_title']
    return {k:item[k] for k in keys if item.get(k) is not None}


def advance(s,run,cid,vid,action,value,evidence,fetch=extract):
    if action not in {'summary','verify','defer','skip'}:
        raise Stop('--next is only allowed after summary, verify, defer, or skip')
    completed = s.transition(run,cid,vid,action,value,evidence)
    # The previous state is already committed. A failed next-item lookup must not
    # hide that acknowledgement or encourage repeating the previous submission.
    try:
        following = prepare(s,run,fetch)
    except (Stop,sqlite3.Error) as exc:
        following = {'stopped':str(exc)}
    return {'completed':compact(completed,ack=True),'next':compact(following)}

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--db',default=str(default_db_path()))
    p.add_argument('--verbose',action='store_true',help='Return full records for troubleshooting')
    sub = p.add_subparsers(dest='cmd',required=True)
    sub.add_parser('status')
    a = sub.add_parser('add'); a.add_argument('url')
    a = sub.add_parser('notebook'); a.add_argument('channel'); a.add_argument('url')
    a = sub.add_parser('scan'); a.add_argument('--channel'); a.add_argument('--full',action='store_true'); a.add_argument('--run')
    a = sub.add_parser('begin'); a.add_argument('--mode',choices=['history','incremental'],required=True); a.add_argument('--scheduled',action='store_true')
    for name in ('next','prepare','park','end','renew'):
        a = sub.add_parser(name); a.add_argument('run')
    a = sub.add_parser('metadata'); a.add_argument('run'); a.add_argument('channel'); a.add_argument('video'); a.add_argument('--force',action='store_true')
    a = sub.add_parser('step'); a.add_argument('run'); a.add_argument('channel'); a.add_argument('video'); a.add_argument('action',choices=['arm','attach','summary','renamed','verify','uncertain','defer','skip']); a.add_argument('--value'); a.add_argument('--evidence'); a.add_argument('--next',dest='advance',action='store_true')
    a = sub.add_parser('report'); a.add_argument('--mode',choices=['history','incremental'],default='history')
    a = sub.add_parser('pause'); a.add_argument('reason',choices=sorted(REASONS))
    a = sub.add_parser('resume'); a.add_argument('--confirmed',action='store_true',required=True)
    args = p.parse_args(argv)
    s = None
    try:
        if not Path(args.db).is_file() and args.cmd!='add':
            raise Stop('Task database is missing; refusing to create an empty replacement')
        s = Store(args.db)
        if args.cmd=='status': result=s.status(verbose=args.verbose)
        elif args.cmd=='report': result=s.report(args.mode)
        elif args.cmd=='add': result=add_channel(s,args.url)
        elif args.cmd=='notebook':
            gemini_url(args.url,'notebook')
            with s.tx():
                c = s.channel(args.channel)
                if c['notebook'] and c['notebook']!=args.url: raise Stop('Existing notebook mapping cannot be overwritten')
                s.db.execute('UPDATE channels SET notebook=? WHERE id=?',(args.url,args.channel))
            result=dict(s.channel(args.channel))
        elif args.cmd=='scan':
            ids = [args.channel] if args.channel else [x[0] for x in s.db.execute('SELECT id FROM channels')]
            result=[scan(s,c,args.full or now().weekday()==0,run=args.run) for c in ids]
        elif args.cmd=='begin': result=s.begin(args.mode,args.scheduled)
        elif args.cmd=='next': result=s.reserve(args.run)
        elif args.cmd=='prepare': result=prepare(s,args.run)
        elif args.cmd=='park': s.park(args.run); result={'parked':True}
        elif args.cmd=='end': s.end(args.run); result={'ended':True}
        elif args.cmd=='renew':
            with s.tx(): s.check_lease(args.run)
            result={'renewed':True}
        elif args.cmd=='metadata': result=metadata(s,args.run,args.channel,args.video,force=args.force)
        elif args.cmd=='step':
            if args.advance:
                result=advance(s,args.run,args.channel,args.video,args.action,args.value,args.evidence)
            else:
                result=s.transition(args.run,args.channel,args.video,args.action,args.value,args.evidence)
        elif args.cmd=='pause':
            with s.tx(): s.set('pause',args.reason)
            result={'paused':args.reason}
        elif args.cmd=='resume':
            # Must only be called after human approval; never by a timer.
            with s.tx():
                s.set('pause','')
                s.db.execute('UPDATE lease SET expires=0')
            result={'resumed':True}
        if not args.verbose and isinstance(result,dict):
            result=compact(result,ack=args.cmd=='step')
        print(json.dumps(result,ensure_ascii=False,separators=(',',':')))
        return 0
    except (Stop,sqlite3.Error) as exc:
        print(json.dumps({'stopped':str(exc)},ensure_ascii=False))
        return 2
    except Exception:
        # Do not dump extractor exceptions, cookies, HTTP headers, or webpage bodies.
        print(json.dumps({'stopped':'Operation failed; inspect task state before retrying'}))
        return 3
    finally:
        if s is not None:
            s.db.close()

if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.exit(main())
