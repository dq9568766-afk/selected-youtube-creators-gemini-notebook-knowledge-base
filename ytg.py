"""Task metadata only. Browser actions belong to the existing Codex UI worker.

No Gemini API, browser profile access, transcripts, summaries, or log files.
"""
from __future__ import annotations

import argparse
import contextlib
from datetime import datetime
import json
import math
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
CREATE TABLE IF NOT EXISTS pacing(scope TEXT PRIMARY KEY, processed INTEGER NOT NULL DEFAULT 0,
 break_started REAL);
CREATE TABLE IF NOT EXISTS pauses(scope TEXT PRIMARY KEY, reason TEXT NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS channels(
 id TEXT PRIMARY KEY, url TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
 notebook TEXT UNIQUE, initialized INTEGER NOT NULL DEFAULT 0,
 last_full TEXT, last_check TEXT, turn INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS scans(
 channel_id TEXT NOT NULL REFERENCES channels(id), tab TEXT NOT NULL,
 count INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL,
 checked TEXT NOT NULL, PRIMARY KEY(channel_id,tab));
CREATE TABLE IF NOT EXISTS runs(
 id TEXT PRIMARY KEY, mode TEXT NOT NULL, channel_id TEXT, schedule_key TEXT UNIQUE,
 policy TEXT NOT NULL DEFAULT 'strict',
 armed INTEGER NOT NULL DEFAULT 0, finished INTEGER NOT NULL DEFAULT 0,
 batch_count INTEGER NOT NULL DEFAULT 0, batch_started REAL, phase TEXT NOT NULL DEFAULT 'generate');
CREATE TABLE IF NOT EXISTS lease(
 singleton INTEGER PRIMARY KEY CHECK(singleton=1), run_id TEXT NOT NULL,
 expires REAL NOT NULL, last_seen REAL NOT NULL DEFAULT 0);
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
GLOBAL_PAUSES = {'quota','login','captcha','ui_changed','network'}
BREAK_EVERY = 20
BREAK_SECONDS = 120
REOPEN_REASONS = {'operation_error','page_interrupted','title_not_updated','membership_unclear'}
REOPEN_EVIDENCE = {'reopen-video-summary','reload-notebook-reopen-video-summary','reload-notebook-reopen-marker-summary'}
CURRENT_PAGE_EVIDENCE = 'current-page-video-summary'
COLLECT_REASONS = {'generation_timeout','submission_unconfirmed','chat_unidentified','content_failed','archive_unverified','source_unavailable','metadata_unavailable','live_not_ready'}

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
    return checked is not None and bool(row['published']) and (bool(row['history']) or 0 <= time.time()-checked < METADATA_TTL)

def generation_decision(elapsed, substantive=False, growing=False, complete=False, blocker=None):
    if blocker:
        return 'stop'
    if complete and substantive:
        return 'summary'
    if elapsed >= 300 or (elapsed >= 120 and not (substantive and growing)):
        return 'defer'
    return 'wait'

class Store:
    def __init__(self,path):
        Path(path).parent.mkdir(parents=True,exist_ok=True)
        self.db = sqlite3.connect(path, timeout=10, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        had_pacing = bool(self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pacing'").fetchone())
        self.db.executescript(SCHEMA)
        # Older task databases are upgraded in place without resetting task state.
        columns = {r['name'] for r in self.db.execute('PRAGMA table_info(videos)')}
        if 'metadata_checked' not in columns:
            with self.tx():
                columns = {r['name'] for r in self.db.execute('PRAGMA table_info(videos)')}
                if 'metadata_checked' not in columns:
                    self.db.execute('ALTER TABLE videos ADD COLUMN metadata_checked REAL')
        run_columns = {r['name'] for r in self.db.execute('PRAGMA table_info(runs)')}
        with self.tx():
            if 'channel_id' not in run_columns:
                self.db.execute('ALTER TABLE runs ADD COLUMN channel_id TEXT')
            if 'policy' not in run_columns:
                self.db.execute("ALTER TABLE runs ADD COLUMN policy TEXT NOT NULL DEFAULT 'strict'")
            if 'batch_count' not in run_columns:
                self.db.execute('ALTER TABLE runs ADD COLUMN batch_count INTEGER NOT NULL DEFAULT 0')
            if 'batch_started' not in run_columns:
                self.db.execute('ALTER TABLE runs ADD COLUMN batch_started REAL')
            if 'phase' not in run_columns:
                self.db.execute("ALTER TABLE runs ADD COLUMN phase TEXT NOT NULL DEFAULT 'generate'")
        with self.tx():
            for name, declaration in [('failure_streak','INTEGER NOT NULL DEFAULT 0'), ('health_after','REAL'), ('health_probe','INTEGER NOT NULL DEFAULT 0')]:
                if name not in run_columns:
                    self.db.execute(f'ALTER TABLE runs ADD COLUMN {name} {declaration}')
            if 'deferral_policy' not in columns:
                self.db.execute("ALTER TABLE videos ADD COLUMN deferral_policy TEXT NOT NULL DEFAULT 'strict'")
            if 'retry_count' not in columns:
                self.db.execute('ALTER TABLE videos ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0')
        with self.tx():
            if 'processed_counted' not in columns:
                self.db.execute('ALTER TABLE videos ADD COLUMN processed_counted INTEGER NOT NULL DEFAULT 0')
                # Older collect-first counted at summary time; do not count those
                # items again when their outstanding archive is verified.
                self.db.execute("UPDATE videos SET processed_counted=1 WHERE state IN ('verified','deferred') OR (state IN ('summarized','renamed') AND (owner IN (SELECT id FROM runs WHERE policy='collect-first') OR (owner IS NULL AND channel_id IN (SELECT channel_id FROM runs WHERE policy='collect-first'))))")
            if 'reopen_required' not in columns:
                self.db.execute('ALTER TABLE videos ADD COLUMN reopen_required TEXT')
                self.db.execute("UPDATE videos SET reopen_required=COALESCE(reopen_required,'page_interrupted') WHERE state IN ('summarized','renamed')")
            if not had_pacing or self.setting('pacing_version')!='2':
                seen=set()
                for r in self.db.execute('SELECT * FROM runs ORDER BY rowid DESC').fetchall():
                    key=r['channel_id'] or '*'
                    if key in seen:
                        continue
                    seen.add(key)
                    reliable='batch_count' in run_columns and r['channel_id'] and r['policy']=='collect-first'
                    count=r['batch_count'] if reliable else BREAK_EVERY
                    started=(r['batch_started'] if reliable and count>=BREAK_EVERY else None)
                    if count>=BREAK_EVERY and started is None:
                        started=time.time()
                    self.db.execute('INSERT OR IGNORE INTO pacing VALUES(?,?,?)',(key,count,started))
                self.set('pacing_version','2')
        self._migrate_pause()

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
        if key=='pause':
            row=self.db.execute("SELECT reason FROM pauses WHERE scope='*'").fetchone()
            return row[0] if row else default
        row = self.db.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
        return row[0] if row else default

    def set(self,key,value):
        if key=='pause':
            with self.tx():
                if value:
                    self.db.execute("INSERT OR REPLACE INTO pauses VALUES('*',?,?)",(value,time.time()))
                else:
                    self.db.execute('DELETE FROM pauses')
            return
        self.db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(key,str(value)))

    def _migrate_pause(self):
        row=self.db.execute("SELECT value FROM settings WHERE key='pause'").fetchone()
        if not row or not row[0]: return
        reason=row[0]
        with self.tx():
            if reason in GLOBAL_PAUSES:
                self.db.execute("INSERT OR REPLACE INTO pauses VALUES('*',?,?)",(reason,time.time()))
            else:
                ids=[x[0] for x in self.db.execute('SELECT DISTINCT channel_id FROM videos WHERE reason=?',(reason,))]
                for cid in ids:
                    self.db.execute("INSERT OR REPLACE INTO pauses VALUES(?,?,?)",(cid,reason,time.time()))
                if not ids:
                    self.db.execute("INSERT OR REPLACE INTO pauses VALUES('*',?,?)",(reason,time.time()))
            self.db.execute("DELETE FROM settings WHERE key='pause'")

    def pause_for(self,cid=None):
        row=self.db.execute("SELECT reason FROM pauses WHERE scope='*'").fetchone()
        if row: return row[0]
        if cid:
            row=self.db.execute('SELECT reason FROM pauses WHERE scope=?',(cid,)).fetchone()
            return row[0] if row else ''
        row=self.db.execute('SELECT reason FROM pauses ORDER BY updated DESC LIMIT 1').fetchone()
        return row[0] if row else ''

    def set_pause(self,reason,cid=None):
        scope='*' if reason in GLOBAL_PAUSES else (cid or '*')
        self.db.execute('INSERT OR REPLACE INTO pauses VALUES(?,?,?)',(scope,reason,time.time()))

    def clear_pause(self,cid=None):
        self.db.execute('DELETE FROM pauses WHERE scope=?',(cid or '*',))

    def pause_status(self):
        return [dict(x) for x in self.db.execute("SELECT NULLIF(scope,'*') AS channel_id,reason,updated FROM pauses ORDER BY updated DESC")]

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
          processed_counted=CASE WHEN videos.state='deferred' AND videos.reason='live_not_ready' AND videos.deferral_policy='strict' THEN 0 ELSE videos.processed_counted END,
          state=CASE WHEN videos.state='deferred' AND videos.reason='live_not_ready' AND videos.deferral_policy='strict' THEN 'pending' ELSE videos.state END,
          reason=CASE WHEN videos.state='deferred' AND videos.reason='live_not_ready' AND videos.deferral_policy='strict' THEN NULL ELSE videos.reason END''',
          (cid,vid,title,entry.get('upload_date'),int(history),time.time_ns(),stamp))
        if history and entry.get('upload_date'):
            try:
                date_value(entry['upload_date'])
            except Stop:
                return
            self.db.execute("UPDATE videos SET metadata_checked=? WHERE channel_id=? AND id=? AND state='pending' AND title=? AND published=? AND metadata_checked IS NULL", (time.time(),cid,vid,title,entry['upload_date']))


    def check_lease(self,run):
        r = self.db.execute('SELECT * FROM lease WHERE singleton=1').fetchone()
        if not r or r['run_id'] != run or r['expires'] < time.time():
            raise Stop('Run has no live lease; reopen the same run before continuing')
        run_row=self.db.execute('SELECT channel_id FROM runs WHERE id=?',(run,)).fetchone()
        reason=self.pause_for(run_row['channel_id'] if run_row else None)
        if reason:
            raise Stop('Paused: '+reason)
        self.db.execute('UPDATE lease SET expires=?,last_seen=? WHERE singleton=1',(time.time()+900,time.time()))

    def begin(self,mode,scheduled=False,channel_id=None,policy='strict',run_id=None):
        with self.tx():
            if mode not in {'history','incremental'}:
                raise Stop('Unknown run mode')
            if policy not in {'strict','collect-first'} or (policy=='collect-first' and (mode!='history' or not channel_id)):
                raise Stop('collect-first requires a channel-scoped historical run')
            if run_id:
                saved=self.db.execute('SELECT * FROM runs WHERE id=?',(run_id,)).fetchone()
                if not saved or saved['finished'] or saved['mode']!=mode or saved['channel_id']!=channel_id:
                    raise Stop('Cannot reopen a finished, missing, or differently scoped run')
                policy=saved['policy']
            reason=self.pause_for(channel_id)
            if reason:
                raise Stop('Paused: '+reason)
            if channel_id: self.channel(channel_id)
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
                self.db.execute("UPDATE videos SET state='pending',owner=NULL WHERE state='reserved' AND owner=?",(old['run_id'],))
                self.db.execute("UPDATE videos SET state='uncertain',reason='unknown_submit' WHERE state='submitting' AND owner=?",(old['run_id'],))
            scope=' AND channel_id=?' if channel_id else ''
            self.db.execute("UPDATE videos SET reopen_required=COALESCE(reopen_required,'page_interrupted') WHERE state IN ('submitted','summarized','renamed')"+scope,(channel_id,) if channel_id else ())
            existing = self.db.execute('SELECT * FROM runs WHERE schedule_key=?',(key,)).fetchone() if key else None
            if existing and existing['finished']:
                raise Stop('This scheduled round is already finished')
            rid = run_id or (existing['id'] if existing else uuid.uuid4().hex)
            if not existing and not run_id:
                self.db.execute('INSERT INTO runs(id,mode,channel_id,schedule_key,policy) VALUES(?,?,?,?,?)',(rid,mode,channel_id,key,policy))
                if policy=='collect-first':
                    previous=self.db.execute("SELECT * FROM runs WHERE channel_id=? AND policy='collect-first' AND id<>? ORDER BY rowid DESC LIMIT 1",(channel_id,rid)).fetchone()
                    if previous:
                        self.db.execute('UPDATE runs SET failure_streak=?,health_after=?,health_probe=? WHERE id=?',tuple(previous[k] for k in ('failure_streak','health_after','health_probe'))+(rid,))

            self._sync_pacing(rid)
            self.db.execute('INSERT OR REPLACE INTO lease(singleton,run_id,expires,last_seen) VALUES(1,?,?,?)',(rid,time.time()+900,time.time()))
            return dict(self.db.execute('SELECT * FROM runs WHERE id=?',(rid,)).fetchone())

    def _sync_pacing(self,run):
        r=self.db.execute('SELECT * FROM runs WHERE id=?',(run,)).fetchone()
        key=r['channel_id'] or '*'
        self.db.execute('INSERT OR IGNORE INTO pacing(scope) VALUES(?)',(key,))
        pace=self.db.execute('SELECT * FROM pacing WHERE scope=?',(key,)).fetchone()
        self.db.execute('UPDATE runs SET batch_count=?,batch_started=? WHERE id=?',(pace['processed'],pace['break_started'],run))
        return pace

    def _break_remaining(self,run):
        pace=self._sync_pacing(run)
        if pace['processed']<BREAK_EVERY:
            return 0
        started=pace['break_started']
        if started is None:
            started=time.time()
            self.db.execute('UPDATE pacing SET break_started=? WHERE scope=?',(started,pace['scope']))
        remaining=max(0,math.ceil(started+BREAK_SECONDS-time.time()))
        if remaining==0:
            self.db.execute('UPDATE pacing SET processed=0,break_started=NULL WHERE scope=?',(pace['scope'],))
        self._sync_pacing(run)
        return remaining

    def _count_processed(self,run,cid,vid):
        v=self.video(cid,vid)
        if v['processed_counted']:
            return
        if run is None:
            self.db.execute('INSERT OR IGNORE INTO pacing(scope) VALUES(?)',(cid,))
            key=cid
        else:
            key=self._sync_pacing(run)['scope']
        self.db.execute('UPDATE videos SET processed_counted=1 WHERE channel_id=? AND id=?',(cid,vid))
        self.db.execute('UPDATE pacing SET processed=processed+1,break_started=CASE WHEN processed=? THEN ? ELSE break_started END WHERE scope=?',(BREAK_EVERY-1,time.time(),key))
        if run is not None:
            self._sync_pacing(run)

    def reserve(self,run,parallel=False):
        with self.tx():
            self.check_lease(run)
            if parallel:
                raise Stop('Video processing is strictly serial')
            return self._reserve_serial(run)

    def _reserve_serial(self,run,archive_only=False):
        r=self.db.execute('SELECT * FROM runs WHERE id=?',(run,)).fetchone()
        history=int(r['mode']=='history')
        scope=' AND channel_id=?' if r['channel_id'] else ''
        args=(history,r['channel_id']) if r['channel_id'] else (history,)
        active=self.db.execute("SELECT * FROM videos WHERE history=? AND state IN ('submitting','submitted','uncertain')"+scope+' ORDER BY found LIMIT 1',args).fetchone()
        if active:
            if archive_only:
                raise Stop('Resolve the active submission before finalization')
            return self._claim(run,active)
        backlog=self.db.execute("SELECT * FROM videos WHERE history=? AND state IN ('summarized','renamed')"+scope+" ORDER BY (state='renamed') DESC,found LIMIT 1",args).fetchone()
        if backlog:
            self.db.execute("UPDATE runs SET phase='finalize' WHERE id=?",(run,))
            return self._claim(run,backlog)
        self.db.execute("UPDATE runs SET phase='generate' WHERE id=?",(run,))
        if archive_only:
            return {'stop':'no_finalize'}
        pending=self.db.execute("SELECT v.* FROM videos v JOIN channels c ON c.id=v.channel_id WHERE v.history=? AND v.state IN ('pending','reserved') AND v.missing=0 AND c.initialized=1 AND c.notebook IS NOT NULL"+(' AND v.channel_id=?' if r['channel_id'] else '')+" ORDER BY (v.state='reserved') DESC,c.turn,COALESCE(v.published,'') DESC,v.found LIMIT 1",args).fetchone()
        if not pending:
            return {'stop':'empty'}
        if r['mode']=='incremental' and r['armed']>=20:
            return {'stop':'round_cap','armed':r['armed']}
        remaining=self._break_remaining(run)
        if remaining:
            return {'stop':'break_required','seconds_remaining':remaining}
        if r['policy']=='collect-first' and r['health_after']:
            remaining=max(0,math.ceil(r['health_after']-time.time()))
            if remaining:
                return {'stop':'health_cooldown','seconds_remaining':remaining}
            self.db.execute('UPDATE runs SET health_probe=1,health_after=0 WHERE id=?',(run,))
        self.db.execute("UPDATE videos SET state='reserved' WHERE channel_id=? AND id=?",(pending['channel_id'],pending['id']))
        return self._claim(run,pending)

    def _claim(self,run,item):
        if item['owner']!=run and item['state'] in {'submitted','summarized','renamed'}:
            self.db.execute("UPDATE videos SET reopen_required=COALESCE(reopen_required,'page_interrupted') WHERE channel_id=? AND id=?",(item['channel_id'],item['id']))
        self.db.execute('UPDATE videos SET owner=? WHERE channel_id=? AND id=?',(run,item['channel_id'],item['id']))
        self.db.execute('UPDATE channels SET turn=? WHERE id=?',(time.time_ns(),item['channel_id']))
        return self.payload(self.video(item['channel_id'],item['id']))

    def finalize(self,run):
        with self.tx():
            self.check_lease(run)
            return self._reserve_serial(run,archive_only=True)

    def cleanup(self,run):
        with self.tx():
            lease=self.db.execute('SELECT * FROM lease WHERE singleton=1').fetchone()
            if lease and lease['run_id']!=run:
                raise Stop('Cannot clean another worker lease')
            if not self.db.execute('SELECT 1 FROM runs WHERE id=?',(run,)).fetchone():
                raise Stop('Unknown run')
            self.db.execute("UPDATE videos SET state='pending',owner=NULL WHERE owner=? AND state='reserved'",(run,))
            self.db.execute("UPDATE videos SET state='uncertain',reason='unknown_submit' WHERE owner=? AND state='submitting'",(run,))
            self.db.execute("UPDATE videos SET reopen_required=COALESCE(reopen_required,'page_interrupted') WHERE owner=? AND state IN ('submitted','summarized','renamed')",(run,))
            self.db.execute('DELETE FROM lease WHERE run_id=?',(run,))
            return {'cleaned':True}

    def adopt_unknown(self,cid,evidence):
        with self.tx():
            if evidence!='notebook-identity-checked':
                raise Stop('Notebook identity evidence required')
            self.channel(cid)
            if self.db.execute('SELECT 1 FROM lease WHERE expires>?',(time.time(),)).fetchone():
                raise Stop('Another worker owns the queue')
            items=self.db.execute("SELECT id FROM videos WHERE channel_id=? AND history=1 AND state IN ('uncertain','submitting')",(cid,)).fetchall()
            for item in items:
                self._count_processed(None,cid,item['id'])
            result=self.db.execute("UPDATE videos SET state='deferred',reason='submission_unconfirmed',owner=NULL WHERE channel_id=? AND history=1 AND state IN ('uncertain','submitting')",(cid,))
            if not self.db.execute("SELECT 1 FROM videos WHERE channel_id=? AND state IN ('uncertain','submitting')",(cid,)).fetchone():
                self.db.execute("DELETE FROM pauses WHERE scope=? AND reason='unknown_submit'",(cid,))
            return {'adopted':result.rowcount}

    def payload(self,row):
        d = dict(row)
        d['notebook'] = self.channel(row['channel_id'])['notebook']
        d['marker'] = f"YTG:{row['channel_id']}:{row['id']}"
        d['url'] = 'https://www.youtube.com/watch?v='+row['id']
        d['phase'] = 'finalize' if row['state'] in {'summarized','renamed','verified'} else 'generate'
        if row['topic'] and row['published']:
            d['expected_title'] = title_for(row)
        if row['state']=='reserved':
            d['prompt'] = d['url'] + '\n总结'
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
            r = self.db.execute('SELECT mode,channel_id FROM runs WHERE id=?',(run,)).fetchone()
            if r['mode']!='history':
                raise Stop('Only a historical round can be parked')
            scope=' AND channel_id=?' if r['channel_id'] else ''
            if self.db.execute("SELECT 1 FROM videos WHERE history=1 AND state IN ('submitting','submitted','summarized','renamed','uncertain')"+scope+' LIMIT 1',(r['channel_id'],) if r['channel_id'] else ()).fetchone():
                raise Stop('Resolve the active chat before parking')
            self.db.execute("UPDATE videos SET state='pending',owner=NULL WHERE owner=? AND state='reserved'",(run,))
            self.db.execute("UPDATE videos SET owner=NULL,reopen_required=COALESCE(reopen_required,'page_interrupted') WHERE owner=? AND state='summarized'",(run,))
            self.db.execute('UPDATE runs SET finished=1 WHERE id=?',(run,))
            self.db.execute('DELETE FROM lease WHERE run_id=?',(run,))

    def transition(self,run,cid,vid,action,value=None,evidence=None):
        with self.tx():
            self.check_lease(run)
            current_run=self.db.execute('SELECT * FROM runs WHERE id=?',(run,)).fetchone()
            if current_run['channel_id'] and current_run['channel_id']!=cid:
                raise Stop('Video is outside run channel scope')
            v = self.video(cid,vid)
            if v['owner'] != run:
                reclaimable = (
                    action=='skip' and v['state']=='uncertain' and not v['chat'] and evidence=='search-no-result'
                ) or (
                    action=='attach' and v['state']=='uncertain' and not v['chat'] and
                    evidence in {'video-match', 'marker-and-video-match'}
                )
                if not reclaimable:
                    raise Stop('Item is not owned by this run')
                self.db.execute("UPDATE videos SET owner=? WHERE channel_id=? AND id=?",(run,cid,vid))
                v = self.video(cid,vid)
            if action in {'renamed','verify'}:
                active=self.db.execute("SELECT id FROM videos WHERE channel_id=? AND history=? AND state IN ('submitting','submitted','uncertain') LIMIT 1",(cid,v['history'])).fetchone()
                other=self.db.execute("SELECT id FROM videos WHERE channel_id=? AND history=? AND state='renamed' AND id<>? LIMIT 1",(cid,v['history'],vid)).fetchone()
                if active or other:
                    raise Stop('Finish the active chat before another archive action')
            args = (cid,vid)
            if action=='arm':
                if self._break_remaining(run):
                    raise Stop('Finish the 120-second break before submitting')
                scope=' AND channel_id=?' if current_run['channel_id'] else ''
                params=(v['history'],vid,current_run['channel_id']) if current_run['channel_id'] else (v['history'],vid)
                if self.db.execute("SELECT 1 FROM videos WHERE history=? AND id<>? AND state IN ('submitting','submitted','summarized','renamed','uncertain')"+scope+' LIMIT 1',params).fetchone():
                    raise Stop('Finish the current video before submitting another')
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
                self.db.execute("UPDATE runs SET phase='finalize' WHERE id=?",(run,))
                self.set('source_failures',0)
                self.set('source_failures:'+cid,0)
                self.db.execute('UPDATE runs SET failure_streak=0,health_probe=0,health_after=0 WHERE id=?',(run,))
            elif action=='renamed':
                if v['state']!='summarized' or value!=title_for(v):
                    raise Stop('Exact title mismatch or summary not completed')
                self.db.execute("UPDATE videos SET state='renamed' WHERE channel_id=? AND id=?",args)
            elif action=='verify':
                if v['state']!='renamed' or value!=title_for(v) or evidence not in REOPEN_EVIDENCE | {CURRENT_PAGE_EVIDENCE}:
                    raise Stop('Require exact title, original video URL, complete summary, account and Notebook evidence')
                if v['reopen_required'] and evidence not in REOPEN_EVIDENCE:
                    raise Stop('This chat requires reopened verification after '+v['reopen_required'])
                self.db.execute("UPDATE videos SET state='verified',owner=NULL,reason=NULL,reopen_required=NULL WHERE channel_id=? AND id=?",args)
                self._count_processed(run,cid,vid)
            elif action=='require-reopen':
                if value not in REOPEN_REASONS or v['state'] not in {'submitting','submitted','summarized','renamed','uncertain'}:
                    raise Stop('Choose a supported reopen reason for an unfinished chat')
                self.db.execute('UPDATE videos SET reopen_required=? WHERE channel_id=? AND id=?',(value,*args))
            elif action=='uncertain':
                if v['state'] not in {'submitting','submitted','summarized','renamed','uncertain'}:
                    raise Stop('No submission to reconcile')
                self.db.execute("UPDATE videos SET state='uncertain',reason='unknown_submit',reopen_required=COALESCE(reopen_required,'page_interrupted') WHERE channel_id=? AND id=?",args)
                if current_run['policy']=='strict':
                    self.set_pause('unknown_submit',cid)
            elif action=='defer':
                collect=current_run['policy']=='collect-first'
                allowed=COLLECT_REASONS if collect else {'source_unavailable','metadata_unavailable','live_not_ready','archive_unverified'}
                if value not in allowed:
                    raise Stop('Unknown deferral reason')
                if v['state'] not in {'reserved','submitting','submitted','uncertain','summarized','renamed'}:
                    raise Stop('Cannot defer an already completed summary or repeat a deferral')
                if v['state'] in {'summarized','renamed'} and value!='archive_unverified':
                    raise Stop('Preserve generated content as archive_unverified')
                self.db.execute("UPDATE videos SET state='deferred',reason=?,deferral_policy=?,owner=NULL,metadata_checked=NULL WHERE channel_id=? AND id=?",(value,current_run['policy'],*args))
                self._count_processed(run,cid,vid)
                if collect and value in {'generation_timeout','submission_unconfirmed','source_unavailable'}:
                    if current_run['health_probe']:
                        self.set_pause('source_unavailable',cid)
                    else:
                        self.db.execute('UPDATE runs SET failure_streak=failure_streak+1,health_after=CASE WHEN failure_streak=4 THEN ? ELSE health_after END WHERE id=?',(time.time()+300,run))
                if not collect and value=='source_unavailable':
                    failures=int(self.setting('source_failures:'+cid,'0'))+1
                    self.set('source_failures:'+cid,failures)
                    self.set('source_failures',failures)
                    if failures>=3:
                        self.set_pause('source_unavailable',cid)
            elif action=='skip':
                if value not in {'content_mismatch','video_unreadable','summary_failed','other'}:
                    raise Stop('Choose a supported manual-review reason')
                known_failure = v['state']=='submitting' and evidence in {'gemini-refusal','known-video-error'}
                confirmed_unsaved = v['state']=='uncertain' and not v['chat'] and evidence=='search-no-result'
                if (v['state'] not in {'reserved','submitted','uncertain'} and not known_failure) or (v['state']=='submitted' and not v['chat']) or (v['state']=='uncertain' and not v['chat'] and not confirmed_unsaved):
                    raise Stop('Skip requires a known per-video failure; reconcile unknown submissions first')
                self.db.execute("UPDATE videos SET state='deferred',reason=?,owner=NULL WHERE channel_id=? AND id=?",
                                ('review:'+value,*args))
                self._count_processed(run,cid,vid)
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
            return {'pause':self.setting('pause'), 'pauses':self.pause_status(), 'busy':bool(lease),
                    'daily_due':self.daily_due(),
                    'states':[dict(x) for x in self.db.execute('SELECT history,state,count(*) AS count FROM videos GROUP BY history,state')]}
        return {'pause':self.setting('pause'), 'pauses':self.pause_status(), 'channels':[dict(x) for x in self.db.execute('SELECT * FROM channels')],
                'states':[dict(x) for x in self.db.execute('SELECT state,history,count(*) AS count FROM videos GROUP BY state,history')],
                'scans':[dict(x) for x in self.db.execute('SELECT * FROM scans')],
                'lease':[dict(x) for x in self.db.execute('SELECT * FROM lease')]}

    def report(self,mode='history',channel_id=None):
        if mode not in {'history','incremental'}:
            raise Stop('Unknown report mode')
        query='SELECT * FROM videos WHERE history=?'
        params=(int(mode=='history'),)
        if channel_id:
            self.channel(channel_id)
            query+=' AND channel_id=?'
            params+=(channel_id,)
        rows=list(self.db.execute(query+' ORDER BY found',params))
        counts={}
        review=[]
        deferred={}
        generated=0
        remaining=0
        total=0
        for row in rows:
            counts[row['state']]=counts.get(row['state'],0)+1
            if row['missing'] and row['state']=='pending':
                continue
            total+=1
            generated+=int(row['state'] in {'summarized','renamed','verified'} or (row['state']=='deferred' and row['reason']=='archive_unverified' and bool(row['topic'])))
            if row['state']=='deferred':
                deferred[row['reason']]=deferred.get(row['reason'],0)+1
                item={k:row[k] for k in ('channel_id','id','title','reason','chat','missing')}
                item['url']='https://www.youtube.com/watch?v='+row['id']
                if row['topic'] and row['published']:
                    item['expected_title']=title_for(row)
                review.append(item)
            elif row['state']!='verified':
                remaining+=1
        return {'mode':mode,'channel_id':channel_id,'complete':remaining==0,'remaining':remaining,
                'counts':counts,'deferred':[{'reason':k,'count':v} for k,v in sorted(deferred.items())],
                'manual_review':review,'generated':generated,'verified':counts.get('verified',0),
                'not_generated':total-generated,'success_rate':generated/total if total else 0}

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
    if s.pause_for(cid):
        raise Stop('Paused: '+s.pause_for(cid))
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
    try:
        info = fetch('https://www.youtube.com/watch?v='+vid,False)
    except Exception as exc:
        if any(text in str(exc).lower() for text in ('members-only','private video','video unavailable','video has been removed','video is unavailable')):
            return s.transition(run,cid,vid,'defer','metadata_unavailable')
        raise
    if info.get('id')!=vid or info.get('channel_id')!=cid:
        raise Stop('Video/channel identity mismatch')
    if info.get('live_status') in {'is_live','is_upcoming','post_live'}:
        return s.transition(run,cid,vid,'defer','live_not_ready')
    try:
        date = date_value(info.get('release_date') or info.get('upload_date'))
    except Stop:
        return s.transition(run,cid,vid,'defer','metadata_unavailable')
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
        r = s.db.execute('SELECT mode,channel_id FROM runs WHERE id=?',(run,)).fetchone()
        scope=' AND channel_id=?' if r['channel_id'] else ''
        active = s.db.execute("SELECT 1 FROM videos WHERE history=1 AND state IN ('submitting','submitted','summarized','renamed','uncertain')"+scope+' LIMIT 1',(r['channel_id'],) if r['channel_id'] else ()).fetchone()
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
        keys += ['reason','reopen_required']
    else:
        keys += ['phase','notebook','url','chat','reason','reopen_required']
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
    a = sub.add_parser('notebook'); a.add_argument('channel'); a.add_argument('url'); a.add_argument('--replace-missing', action='store_true', help='Replace a mapping only after the old notebook was verified missing in Gemini'); a.add_argument('--replace-verified', action='store_true', help='Replace an existing mapping after the target account and Notebook were verified in the visible Gemini UI'); a.add_argument('--evidence', help='Required with --replace-verified: account-notebook-checked')
    a = sub.add_parser('scan'); a.add_argument('--channel'); a.add_argument('--full',action='store_true'); a.add_argument('--run')
    a = sub.add_parser('begin'); a.add_argument('--mode',choices=['history','incremental'],required=True); a.add_argument('--scheduled',action='store_true'); a.add_argument('--channel'); a.add_argument('--policy',choices=['strict','collect-first'],default='strict'); a.add_argument('--run')
    a = sub.add_parser('next'); a.add_argument('run'); a.add_argument('--parallel',action='store_true')
    for name in ('prepare','park','end','renew','finalize','cleanup'):
        a = sub.add_parser(name); a.add_argument('run')
    a = sub.add_parser('metadata'); a.add_argument('run'); a.add_argument('channel'); a.add_argument('video'); a.add_argument('--force',action='store_true')
    a = sub.add_parser('step'); a.add_argument('run'); a.add_argument('channel'); a.add_argument('video'); a.add_argument('action',choices=['arm','attach','summary','renamed','verify','require-reopen','uncertain','defer','skip']); a.add_argument('--value'); a.add_argument('--evidence'); a.add_argument('--next',dest='advance',action='store_true')
    a = sub.add_parser('report'); a.add_argument('--mode',choices=['history','incremental'],default='history'); a.add_argument('--channel')
    a = sub.add_parser('pause'); a.add_argument('reason',choices=sorted(REASONS)); a.add_argument('--channel')
    a = sub.add_parser('resume'); a.add_argument('--confirmed',action='store_true',required=True); a.add_argument('--channel')
    a = sub.add_parser('adopt-unknown'); a.add_argument('--channel',required=True); a.add_argument('--evidence',required=True)
    args = p.parse_args(argv)
    s = None
    try:
        if not Path(args.db).is_file() and args.cmd!='add':
            raise Stop('Task database is missing; refusing to create an empty replacement')
        s = Store(args.db)
        if args.cmd=='status': result=s.status(verbose=args.verbose)
        elif args.cmd=='report': result=s.report(args.mode,args.channel)
        elif args.cmd=='add': result=add_channel(s,args.url)
        elif args.cmd=='notebook':
            gemini_url(args.url,'notebook')
            with s.tx():
                c = s.channel(args.channel)
                if c['notebook'] and c['notebook']!=args.url:
                    if args.replace_verified:
                        if args.evidence != 'account-notebook-checked':
                            raise Stop('--replace-verified requires --evidence account-notebook-checked')
                    elif not args.replace_missing:
                        raise Stop('Existing notebook mapping cannot be overwritten; verify the target account and Notebook in Gemini, then use --replace-verified --evidence account-notebook-checked')
                s.db.execute('UPDATE channels SET notebook=? WHERE id=?',(args.url,args.channel))
            result=dict(s.channel(args.channel))
        elif args.cmd=='scan':
            ids = [args.channel] if args.channel else [x[0] for x in s.db.execute('SELECT id FROM channels')]
            result=[scan(s,c,args.full or now().weekday()==0,run=args.run) for c in ids]
        elif args.cmd=='begin': result=s.begin(args.mode,args.scheduled,args.channel,args.policy,args.run)
        elif args.cmd=='next': result=s.reserve(args.run,args.parallel)
        elif args.cmd=='prepare': result=prepare(s,args.run)
        elif args.cmd=='finalize': result=s.finalize(args.run)
        elif args.cmd=='cleanup': result=s.cleanup(args.run)
        elif args.cmd=='adopt-unknown': result=s.adopt_unknown(args.channel,args.evidence)
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
            if args.reason in GLOBAL_PAUSES and args.channel: raise Stop('Global pause reasons cannot use --channel')
            if args.reason not in GLOBAL_PAUSES and not args.channel: raise Stop('Channel pause reasons require --channel')
            with s.tx(): s.set_pause(args.reason,args.channel)
            result={'paused':args.reason,'channel_id':args.channel}
        elif args.cmd=='resume':
            # Must only be called after human approval; never by a timer.
            with s.tx():
                s.clear_pause(args.channel)
                s.db.execute('UPDATE lease SET expires=0')
            result={'resumed':True,'channel_id':args.channel}
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
