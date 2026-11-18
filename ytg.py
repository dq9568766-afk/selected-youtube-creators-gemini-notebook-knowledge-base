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

YOUTUBE_API_KEY_ENV = 'YOUTUBE_DATA_API_KEY'


def youtube_data_api_key():
    """Read the current local key on every call.

    On Windows the user environment is authoritative.  A long-lived Python
    process can retain an expired process value after the user updates the
    environment, so checking HKCU first makes later requests pick up the new
    value without restarting the worker.
    """
    if os.name == 'nt':
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Environment') as key:
                value, _ = winreg.QueryValueEx(key, YOUTUBE_API_KEY_ENV)
            value = str(value).strip()
            if value:
                return value, 'windows-user'
        except (FileNotFoundError, OSError, TypeError, ValueError):
            pass
    value = os.environ.get(YOUTUBE_API_KEY_ENV, '').strip()
    if value:
        return value, 'process'
    return None, None

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS daily_budget(channel_id TEXT NOT NULL, day TEXT NOT NULL,
 used INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(channel_id,day));
CREATE TABLE IF NOT EXISTS initial_inventory(channel_id TEXT NOT NULL, tab TEXT NOT NULL,
 id TEXT NOT NULL, title TEXT NOT NULL, published TEXT, PRIMARY KEY(channel_id,tab,id));
CREATE TABLE IF NOT EXISTS run_inventory(run_id TEXT NOT NULL, channel_id TEXT NOT NULL,
 checked TEXT NOT NULL, PRIMARY KEY(run_id,channel_id));
CREATE TABLE IF NOT EXISTS run_candidates(run_id TEXT NOT NULL, channel_id TEXT NOT NULL,
 video_id TEXT NOT NULL, PRIMARY KEY(run_id,channel_id,video_id));
CREATE TABLE IF NOT EXISTS pacing(scope TEXT PRIMARY KEY, processed INTEGER NOT NULL DEFAULT 0,
 break_started REAL);
CREATE TABLE IF NOT EXISTS browser_pacing(scope TEXT PRIMARY KEY, last_access REAL NOT NULL DEFAULT 0,
 last_submit REAL NOT NULL DEFAULT 0,recovery_remaining INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS account_coordinator(
 singleton INTEGER PRIMARY KEY CHECK(singleton=1), owner TEXT,
 channel_id TEXT, run_id TEXT, lease_until REAL NOT NULL DEFAULT 0,
 last_kind TEXT, last_granted REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS pauses(scope TEXT PRIMARY KEY, reason TEXT NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS gemini_limit_events(
 id INTEGER PRIMARY KEY AUTOINCREMENT, detected REAL NOT NULL, reason TEXT NOT NULL,
 scope TEXT NOT NULL, channel_id TEXT, run_id TEXT, video_id TEXT,
 video_state TEXT, retry_count INTEGER, run_mode TEXT, policy TEXT,
 armed INTEGER, daily_used INTEGER, account_processed INTEGER,
 account_last_submit REAL, active_submission_count INTEGER NOT NULL DEFAULT 0,
 source TEXT NOT NULL DEFAULT 'pause', observed_type TEXT, reset_at REAL);
CREATE TABLE IF NOT EXISTS gemini_submission_events(
 id INTEGER PRIMARY KEY AUTOINCREMENT, submitted REAL NOT NULL, channel_id TEXT NOT NULL,
 run_id TEXT NOT NULL, video_id TEXT NOT NULL, attempt INTEGER NOT NULL,
 mode TEXT NOT NULL, policy TEXT NOT NULL, recovery_remaining INTEGER NOT NULL,
 unresolved_submissions INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS gemini_limit_event_items(
 event_id INTEGER NOT NULL, channel_id TEXT, run_id TEXT, video_id TEXT,
 video_state TEXT, retry_count INTEGER, run_mode TEXT, policy TEXT,
 armed INTEGER, daily_used INTEGER,
 PRIMARY KEY(event_id,channel_id,video_id));
CREATE TABLE IF NOT EXISTS channels(
 id TEXT PRIMARY KEY, url TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
 notebook TEXT UNIQUE, initialized INTEGER NOT NULL DEFAULT 0,
 last_full TEXT, last_check TEXT, turn INTEGER NOT NULL DEFAULT 0,
 tracking INTEGER NOT NULL DEFAULT 0,
 rename_strategy TEXT,
 title_format TEXT,
 membership_status TEXT,
 config_confirmed INTEGER NOT NULL DEFAULT 0);
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
 scope TEXT PRIMARY KEY, run_id TEXT NOT NULL UNIQUE,
 expires REAL NOT NULL, last_seen REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS videos(
 channel_id TEXT NOT NULL REFERENCES channels(id), id TEXT NOT NULL,
 title TEXT NOT NULL, published TEXT, history INTEGER NOT NULL,
 found INTEGER NOT NULL, seen TEXT NOT NULL, missing INTEGER NOT NULL DEFAULT 0,
 state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN
 ('pending','reserved','submitting','submitted','summarized','renamed','verified','uncertain','blocked','deferred')),
 chat TEXT UNIQUE, topic TEXT, topic_source TEXT, topic_status TEXT NOT NULL DEFAULT 'pending',
 notebook_summary_checked INTEGER NOT NULL DEFAULT 0,
 chat_cleanup_status TEXT NOT NULL DEFAULT 'none', chat_cleanup_reason TEXT,
 final_title TEXT, reason TEXT, owner TEXT, metadata_checked REAL,
  title_source TEXT,
 PRIMARY KEY(channel_id,id));
CREATE INDEX IF NOT EXISTS videos_owner_state ON videos(owner,state,found);
CREATE INDEX IF NOT EXISTS videos_queue ON videos(history,state,found);
"""
METADATA_TTL = 3600  # Legacy compatibility; complete publication caches no longer expire.
SCAN_WINDOW = 50
SCAN_OVERLAP = 10
REASONS = {'quota','login','captcha','ui_changed','network','unknown_submit',
           'source_unavailable','metadata_unavailable','live_not_ready','ambiguous_match','save_unverified'}
GLOBAL_PAUSES = {'quota','login','captcha','ui_changed','network'}
BREAK_EVERY = 20
# The extension and mapping rebuild use a cautious 20/30 trial pace. Normal
# page action windows are spaced by 10 seconds; recovery backoff remains longer.
BREAK_SECONDS = 30
ACCOUNT_COORDINATOR_HOLD = 10
REOPEN_REASONS = {'operation_error','page_interrupted','title_not_updated','membership_unclear'}
REOPEN_EVIDENCE = {'reopen-video-summary','reload-notebook-reopen-video-summary','reload-notebook-reopen-marker-summary'}
CURRENT_PAGE_EVIDENCE = 'current-page-video-summary'
SUMMARY_NOTEBOOK_EVIDENCE = 'youtube-content-complete-notebook'
API_TITLE_CHANNELS = {'UCBKDRq35-L8xev4O7ZqBeLg','UC8UCbiPrm2zN9nZHKdTevZA'}
COLLECT_REASONS = {'generation_timeout','submission_unconfirmed','chat_unidentified','content_failed','archive_unverified','source_unavailable','metadata_unavailable','live_not_ready'}
RENAME_STRATEGIES = {'serial', 'extension'}
TITLE_FORMATS = {'original', 'topic', 'date-topic'}
MEMBERSHIP_STATUSES = {'yes', 'no', 'unknown'}
CONFIRMED_MEMBERSHIP_STATUSES = {'yes', 'no'}
MEMBERS_ONLY_AVAILABILITIES = {
    'members_only', 'member_only', 'subscriber_only', 'premium_only',
}

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

DB_APPLICATION_ID = 0x59544751  # YTGQ; prevents accidentally opening another SQLite file.

def database_backup(source, destination):
    """Create a consistent backup while SQLite is online.

    The SQLite backup API includes WAL state; copying *.sqlite3 directly does
    not reliably do that while a worker is running.
    """
    source = Path(source)
    destination = Path(destination)
    if source.resolve() == destination.resolve():
        raise Stop('Backup destination must differ from the task database')
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise Stop('Backup destination already exists; choose a new path')
    src = sqlite3.connect(source, timeout=30)
    dst = None
    try:
        src.row_factory = sqlite3.Row
        check = src.execute('PRAGMA integrity_check').fetchone()[0]
        if check != 'ok':
            raise Stop('Task database integrity check failed; backup not created')
        dst = sqlite3.connect(destination)
        src.backup(dst)
        dst.commit()
        if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise Stop('Backup integrity check failed')
    finally:
        if dst is not None:
            dst.close()
        src.close()
    return {'backup': str(destination), 'integrity': 'ok'}

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
    configured = row['title_format'] if 'title_format' in row.keys() else None
    if configured == 'original':
        return row['title']
    if row['final_title']:
        return row['final_title']
    topic = topic or row['topic']
    if not topic or not re.fullmatch(r'[\u3400-\u4dbf\u4e00-\u9fff]{1,5}', topic):
        raise Stop('Topic must contain 1–5 Han characters')
    if configured == 'topic':
        return f"{topic}-{row['title']}"
    if configured == 'date-topic':
        return f"{date_value(row['published'])}-{topic}-{row['title']}"

    # Legacy databases did not persist the user's title choice. Preserve their
    # previous behavior until the channel is explicitly configured.
    if row['channel_id'] == 'UCBKDRq35-L8xev4O7ZqBeLg':
        published = date_value(row['published'])
        return f"{published}-{topic}-{row['title']}"
    prefix = f"{date_value(row['published'])}-" if row['published'] else ''
    return f"{prefix}{topic}-{row['title']}"


def topic_from_title(title):
    """Return a conservative 1–5 Han-character candidate from an original title."""
    text = re.sub(r'（[^）]*）|\([^)]*\)|\[[^]]*\]', ' ', title or '')
    text = re.sub(r'^(王局拍案|新闻特写|新闻大写|王志安说|王志安：|王局：)\s*', '', text)
    runs = re.findall(r'[\u3400-\u4dbf\u4e00-\u9fff]{1,}', text)
    ignored = {'今天','最新消息','完整版','现场直播','王局拍案','新闻特写','新闻大写'}
    for run in runs:
        if run in ignored or len(run) > 5:
            continue
        return run
    return None


def extract_topics(s, cid):
    """Batch-generate local title-based topic candidates; never calls a service."""
    channel = s.channel(cid)
    if channel['title_format'] == 'original':
        return {'channel_id': cid, 'skipped': True, 'reason': 'original_title_format'}
    changed = review = 0
    with s.tx():
        rows = s.db.execute("SELECT id,title FROM videos WHERE channel_id=? AND history=1", (cid,)).fetchall()
        for row in rows:
            topic = topic_from_title(row['title'])
            if not topic:
                s.db.execute("UPDATE videos SET topic=NULL,topic_source='title_rule',topic_status='review',final_title=NULL WHERE channel_id=? AND id=?", (cid,row['id']))
                review += 1
                continue
            s.db.execute("UPDATE videos SET topic=?,topic_source='title_rule',topic_status='rule_generated',final_title=NULL WHERE channel_id=? AND id=?", (topic,cid,row['id']))
            changed += 1
    return {'channel_id': cid, 'changed': changed, 'review': review}


def generate_titles(s, cid):
    """Persist current titles after topic/date data is available."""
    channel = s.channel(cid)
    if channel['title_format'] == 'original':
        return {'channel_id': cid, 'generated': 0, 'skipped': True}
    generated = review = 0
    with s.tx():
        rows = s.db.execute("SELECT * FROM videos WHERE channel_id=? AND history=1", (cid,)).fetchall()
        for row in rows:
            try:
                value = title_for(row)
            except Stop:
                s.db.execute("UPDATE videos SET final_title=NULL,topic_status='review' WHERE channel_id=? AND id=?", (cid,row['id']))
                review += 1
                continue
            s.db.execute("UPDATE videos SET final_title=? WHERE channel_id=? AND id=?", (value,cid,row['id']))
            generated += 1
    return {'channel_id': cid, 'generated': generated, 'review': review}


def history_pipeline(s, cid, full=True):
    """Run inventory, local topic extraction, date backfill, and title generation."""
    import backfill
    require_channel_config(s, cid)
    inventory = scan(s, cid, full=full)
    if not inventory['complete']:
        return {'channel_id': cid, 'inventory': inventory, 'complete': False}
    channel = s.channel(cid)
    date_report = None
    if channel['title_format'] == 'date-topic':
        date_report = backfill.start(s, cid)
        job = date_report['id']
        while not date_report['complete']:
            date_report = backfill.report(s, job)
            if date_report['complete']:
                break
            if date_report.get('status') == 'review':
                break
            backfill.step(s, job)
        if not date_report['complete']:
            return {'channel_id': cid, 'inventory': inventory, 'date_backfill': date_report,
                    'title_generation': {'generated': 0, 'review': date_report.get('total', 0)},
                    'complete': False}
    titles = generate_titles(s, cid)
    result = {'channel_id': cid, 'inventory': inventory, 'date_backfill': date_report,
              'title_generation': titles, 'complete': titles['review'] == 0}
    if date_report is not None and date_report.get('complete'):
        result['history_summary_next_action'] = (
            '日期补齐和标题候选已完成。请向用户报告日期覆盖率及 unavailable/review 项；'
            '用户明确开始历史总结后，先创建或继续本频道 Codex Goal，'
            '再核对 Gemini Notebook、账号、频道状态和租约，然后启动 history run。'
        )
    return result


def metadata_fresh(row):
    # Date-based titles require a verified publication date. Do not arm a task
    # whose target title cannot be computed without guessing that date.
    configured = row['title_format'] if 'title_format' in row.keys() else None
    date_required = configured == 'date-topic' or (
        not configured and row['channel_id'] == 'UCBKDRq35-L8xev4O7ZqBeLg'
    )
    if date_required and not row['published']:
        return False
    # A channel listing can serve an auto-translated title instead of the
    # original video title. Only the official API confirms the original.
    if row['channel_id'] in API_TITLE_CHANNELS and youtube_data_api_key()[0]:
        return row['title_source']=='api' and bool(row['title'].strip()) and row['title'] != row['id']
    return bool(row['title'].strip()) and row['title'] != row['id']

def inventory_date(entry):
    try:
        return date_value(entry.get('release_date') or entry.get('upload_date'))
    except (Stop, TypeError):
        return None

def date_boundary_requires_full(previous_checked, entries):
    if not previous_checked:
        return False
    boundary = previous_checked[:10]
    dates = [d for d in (inventory_date(entry) for entry in entries) if d]
    return bool(dates and min(dates) > boundary)

def generation_decision(elapsed, substantive=False, growing=False, complete=False, blocker=None,
                        policy="collect-first", retry_count=0):
    if blocker:
        return 'stop'
    if complete and substantive:
        return 'summary'
    if policy == 'collect-first':
        if elapsed < 20:
            return 'wait'
        return 'retry' if not substantive and retry_count == 0 else 'defer'
    if elapsed >= 300 or (elapsed >= 120 and not (substantive and growing)):
        return 'defer'
    return 'wait'

class Store:
    def __init__(self,path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA busy_timeout=10000')
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('PRAGMA journal_mode=WAL')
        application_id = self.db.execute('PRAGMA application_id').fetchone()[0]
        if application_id not in (0, DB_APPLICATION_ID):
            raise Stop('This is not the canonical YouTube task database')
        if application_id == 0:
            self.db.execute(f'PRAGMA application_id={DB_APPLICATION_ID}')
        if self.path.exists() and self.path.stat().st_size:
            check = self.db.execute('PRAGMA integrity_check').fetchone()[0]
            if check != 'ok':
                raise Stop('Task database integrity check failed; refusing to write')
        had_pacing = bool(self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pacing'").fetchone())
        self.db.executescript(SCHEMA)
        browser_columns={r['name'] for r in self.db.execute('PRAGMA table_info(browser_pacing)')}
        if 'recovery_remaining' not in browser_columns:
            self.db.execute('ALTER TABLE browser_pacing ADD COLUMN recovery_remaining INTEGER NOT NULL DEFAULT 0')
        limit_columns={r['name'] for r in self.db.execute('PRAGMA table_info(gemini_limit_events)')}
        if 'observed_type' not in limit_columns:
            self.db.execute('ALTER TABLE gemini_limit_events ADD COLUMN observed_type TEXT')
        if 'reset_at' not in limit_columns:
            self.db.execute('ALTER TABLE gemini_limit_events ADD COLUMN reset_at REAL')
        submission_columns={r['name'] for r in self.db.execute('PRAGMA table_info(gemini_submission_events)')}
        if 'unresolved_submissions' not in submission_columns:
            self.db.execute('ALTER TABLE gemini_submission_events ADD COLUMN unresolved_submissions INTEGER NOT NULL DEFAULT 0')
        import backfill
        self.db.executescript(backfill.SCHEMA)
        backfill.migrate(self)
        # Older task databases are upgraded in place without resetting task state.
        columns = {r['name'] for r in self.db.execute('PRAGMA table_info(videos)')}
        channel_columns = {r['name'] for r in self.db.execute('PRAGMA table_info(channels)')}
        if 'tracking' not in channel_columns:
            with self.tx():
                self.db.execute('ALTER TABLE channels ADD COLUMN tracking INTEGER NOT NULL DEFAULT 0')
        channel_columns = {r['name'] for r in self.db.execute('PRAGMA table_info(channels)')}
        with self.tx():
            for name, declaration in [
                ('rename_strategy', 'TEXT'),
                ('title_format', 'TEXT'),
                ('membership_status', 'TEXT'),
                ('config_confirmed', 'INTEGER NOT NULL DEFAULT 0'),
            ]:
                if name not in channel_columns:
                    self.db.execute(f'ALTER TABLE channels ADD COLUMN {name} {declaration}')
        if 'metadata_checked' not in columns:
            with self.tx():
                columns = {r['name'] for r in self.db.execute('PRAGMA table_info(videos)')}
                if 'metadata_checked' not in columns:
                    self.db.execute('ALTER TABLE videos ADD COLUMN metadata_checked REAL')
        if 'title_source' not in columns:
            with self.tx():
                columns = {r['name'] for r in self.db.execute('PRAGMA table_info(videos)')}
                if 'title_source' not in columns:
                    self.db.execute('ALTER TABLE videos ADD COLUMN title_source TEXT')
        columns = {r['name'] for r in self.db.execute('PRAGMA table_info(videos)')}
        with self.tx():
            for name, declaration in [
                ('topic_source', 'TEXT'),
                ('topic_status', "TEXT NOT NULL DEFAULT 'pending'"),
                ('final_title', 'TEXT'),
                ('notebook_summary_checked', 'INTEGER NOT NULL DEFAULT 0'),
                ('chat_cleanup_status', "TEXT NOT NULL DEFAULT 'none'"),
                ('chat_cleanup_reason', 'TEXT'),
            ]:
                if name not in columns:
                    self.db.execute(f'ALTER TABLE videos ADD COLUMN {name} {declaration}')
        run_columns = {r['name'] for r in self.db.execute('PRAGMA table_info(runs)')}
        with self.tx():
            if 'archive_mode' not in run_columns:
                self.db.execute("ALTER TABLE runs ADD COLUMN archive_mode TEXT NOT NULL DEFAULT 'serial'")
            for name, declaration in [('scan_stamp','TEXT'), ('outcome','TEXT'), ('parent_run','TEXT')]:
                if name not in run_columns:
                    self.db.execute(f'ALTER TABLE runs ADD COLUMN {name} {declaration}')
            if 'channel_id' not in run_columns:
                self.db.execute('ALTER TABLE runs ADD COLUMN channel_id TEXT')
            if not self.setting('daily_budget_version'):
                self.db.execute("INSERT OR IGNORE INTO daily_budget SELECT COALESCE(channel_id,'*'),substr(schedule_key,1,10),sum(armed) FROM runs WHERE schedule_key IS NOT NULL GROUP BY COALESCE(channel_id,'*'),substr(schedule_key,1,10)")
                self.set('daily_budget_version','1')
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
            if not had_pacing or self.setting('pacing_version')!='3':
                seen=set()
                for r in self.db.execute('SELECT * FROM runs ORDER BY rowid DESC').fetchall():
                    key='gemini-account'
                    if key in seen:
                        continue
                    seen.add(key)
                    reliable='batch_count' in run_columns and r['channel_id'] and r['policy']=='collect-first'
                    count=r['batch_count'] if reliable else BREAK_EVERY
                    started=(r['batch_started'] if reliable and count>=BREAK_EVERY else None)
                    if count>=BREAK_EVERY and started is None:
                        started=time.time()
                    self.db.execute('INSERT OR IGNORE INTO pacing VALUES(?,?,?)',(key,count,started))
                self.set('pacing_version','3')
            self.db.execute("INSERT OR IGNORE INTO pacing(scope) VALUES('gemini-account')")
            legacy=list(self.db.execute("SELECT processed,break_started FROM pacing WHERE scope NOT IN ('*','gemini-account')"))
            if legacy:
                processed=sum(x['processed'] for x in legacy)
                starts=[x['break_started'] for x in legacy if x['break_started'] is not None]
                self.db.execute("UPDATE pacing SET processed=max(processed,?),break_started=COALESCE(break_started,?) WHERE scope='gemini-account'",(processed,min(starts) if starts else None))
                # These rows are from the pre-account-wide pacing model.  If
                # left behind, every new CLI process migrates them again and
                # recreates an already-finished batch break.  Account-wide
                # pacing is authoritative now; retire only the obsolete
                # counters, never video state or leases.
                self.db.execute("UPDATE pacing SET processed=0,break_started=NULL WHERE scope NOT IN ('*','gemini-account')")
        self._migrate_pause()
        import history_rename
        self.db.executescript(history_rename.SCHEMA)
        history_rename_columns = {r['name'] for r in self.db.execute('PRAGMA table_info(history_rename_items)')}
        history_rename_session_columns = {r['name'] for r in self.db.execute('PRAGMA table_info(history_rename_sessions)')}
        with self.tx():
            for name, declaration in [
                ('recovery_attempts', 'INTEGER NOT NULL DEFAULT 0'),
                ('verification_phase', 'TEXT'),
                ('verification_mode', "TEXT NOT NULL DEFAULT 'extension-reopen-v1'"),
            ]:
                if name not in history_rename_columns:
                    self.db.execute(f'ALTER TABLE history_rename_items ADD COLUMN {name} {declaration}')
            if 'session_key' not in history_rename_session_columns:
                self.db.execute('ALTER TABLE history_rename_sessions ADD COLUMN session_key TEXT')
        # Preserve an existing worker's ownership and expiry during the upgrade.
        with self.tx():
            if 'singleton' in {r['name'] for r in self.db.execute('PRAGMA table_info(lease)')}:
                self.db.execute('ALTER TABLE lease RENAME TO lease_legacy')
                self.db.execute('CREATE TABLE lease(scope TEXT PRIMARY KEY, run_id TEXT NOT NULL UNIQUE, expires REAL NOT NULL, last_seen REAL NOT NULL DEFAULT 0)')
                self.db.execute("INSERT INTO lease SELECT COALESCE(r.channel_id,'*'),l.run_id,l.expires,l.last_seen FROM lease_legacy l LEFT JOIN runs r ON r.id=l.run_id")
                self.db.execute('DROP TABLE lease_legacy')

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

    def pause_for_run(self,run):
        """Allow scheduled collect-first incrementals past old unknown submits."""
        row=self.db.execute('SELECT mode,policy,schedule_key,channel_id FROM runs WHERE id=?',(run,)).fetchone()
        reason=self.pause_for(row['channel_id'] if row else None)
        if (reason=='unknown_submit' and row and row['mode']=='incremental'
                and row['policy']=='collect-first' and row['schedule_key']):
            return ''
        return reason

    def set_pause(self,reason,cid=None,observed_type=None,reset_at=None):
        scope='*' if reason in GLOBAL_PAUSES else (cid or '*')
        event = self.record_gemini_limit(reason,cid,observed_type,reset_at) if reason in {'quota','captcha','network','login'} else None
        self.db.execute('INSERT OR REPLACE INTO pauses VALUES(?,?,?)',(scope,reason,time.time()))
        return event

    def record_gemini_limit(self,reason,cid=None,observed_type=None,reset_at=None):
        """Record only queue/send metadata when Gemini reports a global block."""
        all_active = self.db.execute("""
            SELECT v.channel_id,v.id,v.state,v.retry_count,v.owner,
                   r.id AS run_id,r.mode,r.policy,r.armed
            FROM videos v LEFT JOIN runs r ON r.id=v.owner
            WHERE v.state IN ('submitting','submitted','uncertain')
              AND (? IS NULL OR v.channel_id=?)
            ORDER BY v.found
        """,(cid,cid)).fetchall()
        active = all_active[0] if all_active else None
        lease = self.db.execute("""
            SELECT l.run_id,r.channel_id,r.mode,r.policy,r.armed
            FROM lease l LEFT JOIN runs r ON r.id=l.run_id
            WHERE l.expires>? AND (? IS NULL OR l.scope IN ('*',?))
            ORDER BY l.last_seen DESC LIMIT 1
        """,(time.time(),cid,cid)).fetchone()
        run_id = active['run_id'] if active and active['run_id'] else (lease['run_id'] if lease else None)
        run = self.db.execute('SELECT * FROM runs WHERE id=?',(run_id,)).fetchone() if run_id else None
        channel_id = active['channel_id'] if active and len(all_active)==1 else (run['channel_id'] if run and not all_active else cid)
        video_id = active['id'] if active else None
        video_state = active['state'] if active else None
        retry_count = active['retry_count'] if active else None
        mode = run['mode'] if run else (lease['mode'] if lease else None)
        policy = run['policy'] if run else (lease['policy'] if lease else None)
        armed = run['armed'] if run else (lease['armed'] if lease else None)
        pacing = self.db.execute("SELECT processed FROM pacing WHERE scope='gemini-account'").fetchone()
        browser_pacing = self.db.execute("SELECT last_submit FROM browser_pacing WHERE scope='gemini-account'").fetchone()
        daily_used = self.budget_used(channel_id) if channel_id else self.budget_used()
        cur = self.db.execute("""
            INSERT INTO gemini_limit_events(
              detected,reason,scope,channel_id,run_id,video_id,video_state,
              retry_count,run_mode,policy,armed,daily_used,account_processed,
              account_last_submit,active_submission_count,observed_type,reset_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,(time.time(),reason,'*' if reason in GLOBAL_PAUSES else (cid or '*'),
              channel_id,run_id,video_id,video_state,retry_count,mode,policy,armed,
              daily_used,pacing['processed'] if pacing else 0,
              browser_pacing['last_submit'] if browser_pacing else 0,
              len(all_active),observed_type,reset_at))
        event_id=cur.lastrowid
        seen_runs=set()
        for row in all_active:
            row_channel=row['channel_id']
            row_daily=self.budget_used(row_channel)
            self.db.execute("""
                INSERT INTO gemini_limit_event_items(
                  event_id,channel_id,run_id,video_id,video_state,retry_count,
                  run_mode,policy,armed,daily_used)
                VALUES(?,?,?,?,?,?,?,?,?,?)
            """,(event_id,row_channel,row['run_id'],row['id'],row['state'],row['retry_count'],
                  row['mode'],row['policy'],row['armed'],row_daily))
            seen_runs.add(row['run_id'])
        if lease:
            lease_rows=self.db.execute("""
                SELECT l.run_id,r.channel_id,r.mode,r.policy,r.armed
                FROM lease l LEFT JOIN runs r ON r.id=l.run_id
                WHERE l.expires>? AND (? IS NULL OR l.scope IN ('*',?))
                ORDER BY l.last_seen DESC
            """,(time.time(),cid,cid)).fetchall()
            for row in lease_rows:
                if row['run_id'] in seen_runs:
                    continue
                self.db.execute("""
                    INSERT INTO gemini_limit_event_items(
                      event_id,channel_id,run_id,video_id,video_state,retry_count,
                      run_mode,policy,armed,daily_used)
                    VALUES(?,?,?,?,?,?,?,?,?,?)
                """,(event_id,row['channel_id'],row['run_id'],None,None,None,row['mode'],
                      row['policy'],row['armed'],self.budget_used(row['channel_id']) if row['channel_id'] else self.budget_used()))
        return {'event_id':event_id,'recorded':True,'active_channels':len({row['channel_id'] for row in all_active})}

    def gemini_limit_report(self,channel_id=None):
        where = ''
        params = ()
        if channel_id:
            where = ' WHERE channel_id=? OR channel_id IS NULL'
            params = (channel_id,)
        total = self.db.execute('SELECT count(*) FROM gemini_limit_events'+where,params).fetchone()[0]
        by_reason = [dict(x) for x in self.db.execute(
            'SELECT reason,count(*) AS count FROM gemini_limit_events'+where+' GROUP BY reason ORDER BY count DESC,reason',params)]
        by_channel = [dict(x) for x in self.db.execute(
            "SELECT i.channel_id,COALESCE(c.name,i.channel_id,'global') AS channel_name,count(DISTINCT i.event_id) AS count "
            "FROM gemini_limit_event_items i LEFT JOIN channels c ON c.id=i.channel_id "
            "JOIN gemini_limit_events e ON e.id=i.event_id"+
            (' WHERE e.channel_id=? OR e.channel_id IS NULL' if channel_id else '')+
            ' GROUP BY i.channel_id,c.name ORDER BY count DESC',params)]
        context = self.db.execute(
            'SELECT count(*) AS total,coalesce(sum(video_id IS NOT NULL),0) AS with_video,'
            'coalesce(sum(active_submission_count>0),0) AS active_submission_events,'
            'coalesce(sum(retry_count>0),0) AS retry_context_events,'
            'coalesce(sum(armed),0) AS armed_total FROM gemini_limit_events'+where,params).fetchone()
        item_context = self.db.execute(
            "SELECT count(DISTINCT i.event_id) AS item_events,"
            "coalesce(sum(i.video_id IS NOT NULL),0) AS item_videos,"
            "coalesce(sum(i.retry_count>0),0) AS item_retries "
            "FROM gemini_limit_event_items i JOIN gemini_limit_events e ON e.id=i.event_id"+
            (' WHERE e.channel_id=? OR e.channel_id IS NULL' if channel_id else ''),params).fetchone()
        if item_context['item_events']:
            context = dict(context)
            context['with_video'] = item_context['item_videos']
            context['active_submission_events'] = item_context['item_videos']
            context['retry_context_events'] = item_context['item_retries']
        recent = [dict(x) for x in self.db.execute(
            'SELECT id,detected,reason,observed_type,reset_at,channel_id,run_id,video_id,video_state,retry_count,run_mode,policy,armed,daily_used,account_processed,account_last_submit FROM gemini_limit_events'+where+' ORDER BY detected DESC LIMIT 20',params)]
        for event in recent:
            event['detected_at'] = datetime.fromtimestamp(event.pop('detected')).astimezone().isoformat()
            if event.get('reset_at'):
                event['reset_at']=datetime.fromtimestamp(event['reset_at']).astimezone().isoformat()
        submission_where=' WHERE channel_id=?' if channel_id else ''
        submission_params=(channel_id,) if channel_id else ()
        submissions=self.db.execute('SELECT count(*) AS attempts, count(DISTINCT channel_id) AS channels, '
            'coalesce(sum(attempt>1),0) AS retries, coalesce(max(unresolved_submissions),0) AS peak_unresolved '
            'FROM gemini_submission_events'+submission_where,submission_params).fetchone()
        recent_submissions=[dict(x) for x in self.db.execute(
            'SELECT submitted,channel_id,run_id,video_id,attempt,mode,policy,recovery_remaining,unresolved_submissions '
            'FROM gemini_submission_events'+submission_where+' ORDER BY submitted DESC LIMIT 30',submission_params)]
        for item in recent_submissions:
            item['submitted_at']=datetime.fromtimestamp(item.pop('submitted')).astimezone().isoformat()
        recommendations=[]
        if context['active_submission_events']:
            recommendations.append('限流发生时存在未完成发送，恢复前先核对当前视频，避免重复提交。')
        if context['retry_context_events']:
            recommendations.append('限流记录包含重试上下文，后续应单独比较首次提交与重试的触发比例。')
        if total and not context['with_video']:
            recommendations.append('限流发生时没有可关联的视频，说明暂停登记可能晚于页面提示；应在看到提示后立即登记。')
        return {'total_events':total,'channel_id':channel_id,'by_reason':by_reason,
                'by_channel':by_channel,'context':dict(context),
                'recommendations':recommendations,'recent_events':recent,
                'submission_history':dict(submissions),'recent_submissions':recent_submissions}

    def clear_pause(self,cid=None):
        self.db.execute('DELETE FROM pauses WHERE scope=?',(cid or '*',))

    def pause_status(self):
        return [dict(x) for x in self.db.execute("SELECT NULLIF(scope,'*') AS channel_id,reason,updated FROM pauses ORDER BY updated DESC")]

    def channel(self,cid):
        r = self.db.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
        if not r:
            raise Stop('Unknown channel')
        return r

    def channel_config(self, cid):
        """Return the persisted first-run choices for one channel."""
        channel = self.channel(cid)
        complete = bool(
            channel['config_confirmed']
            and channel['rename_strategy'] in RENAME_STRATEGIES
            and channel['title_format'] in TITLE_FORMATS
            and channel['membership_status'] in CONFIRMED_MEMBERSHIP_STATUSES
        )
        return {
            'channel_id': cid,
            'complete': complete,
            'rename_strategy': channel['rename_strategy'],
            'title_format': channel['title_format'],
            'membership_status': channel['membership_status'],
            'config_confirmed': int(bool(channel['config_confirmed'])),
        }

    def configure_channel(
        self,
        cid,
        *,
        rename_strategy=None,
        title_format=None,
        membership_status=None,
    ):
        """Persist the first-run choices without changing queue state."""
        for value, allowed, label in (
            (rename_strategy, RENAME_STRATEGIES, 'rename strategy'),
            (title_format, TITLE_FORMATS, 'title format'),
            (membership_status, MEMBERSHIP_STATUSES, 'membership status'),
        ):
            if value is not None and value not in allowed:
                raise Stop(f'Unknown {label}')
        current = self.channel(cid)
        values = {
            'rename_strategy': rename_strategy or current['rename_strategy'],
            'title_format': title_format or current['title_format'],
            'membership_status': membership_status or current['membership_status'],
        }
        confirmed = int(all(values[name] in allowed for name, allowed in (
            ('rename_strategy', RENAME_STRATEGIES),
            ('title_format', TITLE_FORMATS),
            ('membership_status', CONFIRMED_MEMBERSHIP_STATUSES),
        )))
        with self.tx():
            self.db.execute(
                """UPDATE channels
                   SET rename_strategy=?, title_format=?, membership_status=?,
                       config_confirmed=?
                 WHERE id=?""",
                (values['rename_strategy'], values['title_format'],
                 values['membership_status'], confirmed, cid),
            )
        return self.channel(cid)

    def date_backfill_status(self, cid):
        """Return date coverage and whether date-topic history is blocked."""
        channel = self.channel(cid)
        row = self.db.execute(
            """SELECT count(*) AS total,
                      sum(CASE WHEN published IS NOT NULL THEN 1 ELSE 0 END) AS dated
                 FROM videos WHERE channel_id=? AND history=1""",
            (cid,),
        ).fetchone()
        total = int(row['total'] or 0)
        dated = int(row['dated'] or 0)
        missing = total - dated
        job = self.db.execute(
            'SELECT * FROM metadata_jobs WHERE channel_id=? ORDER BY created DESC LIMIT 1',
            (cid,),
        ).fetchone()
        unresolved = missing
        if job and missing:
            unresolved = self.db.execute(
                """SELECT count(*) FROM videos v
                   LEFT JOIN metadata_items m
                     ON m.job=? AND m.video=v.id
                  WHERE v.channel_id=? AND v.history=1 AND v.published IS NULL
                    AND (m.video IS NULL OR m.state IN ('pending','fetching','review'))""",
                (job['id'], cid),
            ).fetchone()[0]
        required = channel['title_format'] == 'date-topic'
        blocked = bool(required and channel['initialized'] and unresolved)
        return {
            'required': required,
            'total': total,
            'dated': dated,
            'missing': missing,
            'date_coverage': dated / total if total else 1,
            'all_dates_complete': missing == 0,
            'backfill_job': dict(job) if job else None,
            'backfill_unresolved': int(unresolved),
            'blocked': blocked,
            'next_action': (
                'cleanup RUN; metadata-backfill start --channel CHANNEL_ID; '
                'run metadata-backfill until report complete'
                if blocked else None
            ),
        }

    def video(self,cid,vid):
        r = self.db.execute(
            'SELECT v.*, c.title_format FROM videos v JOIN channels c '
            'ON c.id=v.channel_id WHERE v.channel_id=? AND v.id=?',
            (cid,vid),
        ).fetchone()
        if not r:
            raise Stop('Unknown video')
        return r

    def requeue_manual_review(self, cid, reasons=None):
        """Explicitly requeue deferred historical summaries for human review."""
        self.channel(cid)
        allowed = set(reasons or {'generation_timeout','content_failed','chat_unidentified'})
        if not allowed or not allowed <= {'generation_timeout','content_failed','chat_unidentified','submission_unconfirmed'}:
            raise Stop('Unsupported manual-review reason')
        marks=','.join('?'*len(allowed))
        with self.tx():
            rows=self.db.execute(
                f"SELECT id FROM videos WHERE channel_id=? AND history=1 AND state='deferred' AND reason IN ({marks})",
                (cid,*sorted(allowed))).fetchall()
            ids=[row['id'] for row in rows]
            if ids:
                self.db.execute(
                    f"UPDATE videos SET state='pending',reason=NULL,owner=NULL,processed_counted=0,chat=NULL,reopen_required=0 WHERE channel_id=? AND id IN ({','.join('?'*len(ids))})",
                    (cid,*ids))
        return {'channel_id':cid,'requeued':len(ids),'reasons':sorted(allowed),'video_ids':ids}

    def insert(self,cid,entry,stamp,history):
        vid = entry.get('id')
        if not re.fullmatch(r'[A-Za-z0-9_-]{11}',vid or ''):
            raise Stop('Invalid video id in inventory')
        title = entry.get('title') or vid
        try:
            published = date_value(entry.get('release_date') or entry.get('upload_date'))
        except (Stop, TypeError):
            published = None
        # Keep the title used by an existing chat stable even if YouTube edits it.
        self.db.execute('''INSERT INTO videos(channel_id,id,title,published,history,found,seen)
          VALUES(?,?,?,?,?,?,?) ON CONFLICT(channel_id,id) DO UPDATE SET
          seen=excluded.seen,missing=0,
          metadata_checked=CASE WHEN videos.state='pending' AND COALESCE(videos.title_source,'')<>'api' AND videos.title<>excluded.title THEN NULL ELSE videos.metadata_checked END,
          title=CASE WHEN videos.state='pending' AND COALESCE(videos.title_source,'')<>'api' THEN excluded.title ELSE videos.title END,
          title_source=CASE WHEN videos.state='pending' AND COALESCE(videos.title_source,'')<>'api' THEN NULL ELSE videos.title_source END,
          published=CASE WHEN videos.state='pending' AND excluded.published IS NOT NULL THEN excluded.published ELSE videos.published END,
          processed_counted=CASE WHEN videos.state='deferred' AND videos.reason='live_not_ready' AND videos.deferral_policy='strict' THEN 0 ELSE videos.processed_counted END,
          state=CASE WHEN videos.state='deferred' AND videos.reason='live_not_ready' AND videos.deferral_policy='strict' THEN 'pending' ELSE videos.state END,
          reason=CASE WHEN videos.state='deferred' AND videos.reason='live_not_ready' AND videos.deferral_policy='strict' THEN NULL ELSE videos.reason END''',
          (cid,vid,title,published,int(history),time.time_ns(),stamp))
        if title.strip() and title != vid and (
            not youtube_data_api_key()[0] or cid not in API_TITLE_CHANNELS
        ):
            self.db.execute("UPDATE videos SET metadata_checked=? WHERE channel_id=? AND id=? AND state='pending' AND title=?", (time.time(),cid,vid,title))

    @staticmethod
    def _member_only_entry(entry):
        availability = str(entry.get('availability') or '').strip().lower()
        access = str(entry.get('access') or '').strip().lower()
        return availability in MEMBERS_ONLY_AVAILABILITIES or access in MEMBERS_ONLY_AVAILABILITIES


    def check_lease(self,run):
        r = self.db.execute('SELECT * FROM lease WHERE run_id=?',(run,)).fetchone()
        if not r or r['run_id'] != run or r['expires'] < time.time():
            raise Stop('Run has no live lease; reopen the same run before continuing')
        run_row=self.db.execute('SELECT channel_id FROM runs WHERE id=?',(run,)).fetchone()
        reason=self.pause_for_run(run)
        if reason:
            raise Stop('Paused: '+reason)
        self.db.execute('UPDATE lease SET expires=?,last_seen=? WHERE run_id=?',(time.time()+900,time.time(),run))

    def conflicting_leases(self,channel_id):
        return self.db.execute("SELECT * FROM lease WHERE (?='*' OR scope='*' OR scope=?)",
                               (channel_id or '*',channel_id or '*')).fetchall()

    def begin(self,mode,scheduled=False,channel_id=None,policy='strict',run_id=None,
              allow_history_handoff=False):
        with self.tx():
            if mode not in {'history','incremental'}:
                raise Stop('Unknown run mode')
            if policy not in {'strict','collect-first'} or (policy=='collect-first' and not channel_id):
                raise Stop('collect-first requires a channel-scoped run')
            if run_id:
                saved=self.db.execute('SELECT * FROM runs WHERE id=?',(run_id,)).fetchone()
                if not saved or saved['finished'] or saved['mode']!=mode or saved['channel_id']!=channel_id:
                    raise Stop('Cannot reopen a finished, missing, or differently scoped run')
                policy=saved['policy']
            reason=self.pause_for(channel_id)
            if (reason=='unknown_submit' and mode=='incremental' and scheduled
                    and policy=='collect-first' and channel_id):
                reason=''
            if reason:
                raise Stop('Paused: '+reason)
            if channel_id:
                channel = self.channel(channel_id)
                date_status = self.date_backfill_status(channel_id)
                if mode == 'history' and date_status['blocked']:
                    raise Stop('date_backfill_required: '+date_status['next_action'])
            t = now()
            key = None
            if scheduled:
                if mode!='incremental':
                    raise Stop('Scheduled daily rounds must use incremental mode')
                if (t.hour,t.minute)<(5,0):
                    raise Stop('Not yet the daily local 05:00 due window')
                key = t.date().isoformat() + (':'+channel_id if channel_id else '')
            old_leases = self.conflicting_leases(channel_id)
            takeover = bool(mode == 'incremental' and scheduled and channel_id)
            live_leases = [old for old in old_leases if old['expires'] > time.time()]
            if any(old['scope']=='*' for old in live_leases):
                raise Stop('Another worker owns the queue scope')
            if live_leases and not takeover:
                raise Stop('Another worker owns the queue scope')
            if takeover and any(old['scope'] != channel_id for old in live_leases):
                raise Stop('Another worker owns the queue scope')
            for old in old_leases:
                self.db.execute("UPDATE videos SET state='pending',owner=NULL WHERE state='reserved' AND owner=?",(old['run_id'],))
                self.db.execute("UPDATE videos SET state='uncertain',reason='unknown_submit',owner=NULL WHERE state='submitting' AND owner=?",(old['run_id'],))
                self.db.execute('DELETE FROM lease WHERE run_id=?',(old['run_id'],))
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
            self.db.execute('INSERT INTO lease(scope,run_id,expires,last_seen) VALUES(?,?,?,?)',(channel_id or '*',rid,time.time()+900,time.time()))
            return dict(self.db.execute('SELECT * FROM runs WHERE id=?',(rid,)).fetchone())

    def _sync_pacing(self,run):
        key='gemini-account'
        self.db.execute('INSERT OR IGNORE INTO pacing(scope) VALUES(?)',(key,))
        pace=self.db.execute('SELECT * FROM pacing WHERE scope=?',(key,)).fetchone()
        self.db.execute('UPDATE runs SET batch_count=?,batch_started=? WHERE id=?',(pace['processed'],pace['break_started'],run))
        return pace

    def coordinator_status(self, at=None):
        at=time.time() if at is None else at
        self.db.execute('INSERT OR IGNORE INTO account_coordinator(singleton) VALUES(1)')
        row=self.db.execute('SELECT * FROM account_coordinator WHERE singleton=1').fetchone()
        result=dict(row)
        result['active']=bool(row['owner'] and row['lease_until']>at)
        result['seconds_remaining']=max(0,math.ceil(row['lease_until']-at))
        return result

    def _coordinator_wait(self,owner,at):
        owner=owner or 'unscoped'
        self.db.execute('INSERT OR IGNORE INTO account_coordinator(singleton) VALUES(1)')
        row=self.db.execute('SELECT owner,lease_until FROM account_coordinator WHERE singleton=1').fetchone()
        if row['owner'] and row['owner']!=owner and row['lease_until']>at:
            return math.ceil(row['lease_until']-at)
        return 0

    def _coordinator_grant(self,owner,kind,at,lease_seconds):
        owner=owner or 'unscoped'
        run=self.db.execute('SELECT channel_id FROM runs WHERE id=?',(owner,)).fetchone()
        self.db.execute("""UPDATE account_coordinator
            SET owner=?,channel_id=?,run_id=?,lease_until=?,last_kind=?,last_granted=?
            WHERE singleton=1""",(owner,run['channel_id'] if run else None,
            owner if run else None,at+lease_seconds,kind,at))

    def _coordinator_release(self,owner):
        self.db.execute("UPDATE account_coordinator SET owner=NULL,channel_id=NULL,run_id=NULL,lease_until=0 WHERE singleton=1 AND owner=?",(owner,))

    def browser_access(self, kind='access', at=None, owner=None):
        """Claim one shared Gemini UI action slot; callers must act immediately."""
        if kind not in {'access','submit'}:
            raise Stop('Browser pacing kind must be access or submit')
        at=time.time() if at is None else at
        with self.tx():
            if owner:
                run=self.db.execute('SELECT channel_id FROM runs WHERE id=?',(owner,)).fetchone()
                reason=self.pause_for_run(owner) if run else self.pause_for(None)
                if reason:
                    raise Stop('Paused: '+reason)
            self.db.execute("INSERT OR IGNORE INTO browser_pacing(scope) VALUES('gemini-account')")
            row=self.db.execute("SELECT * FROM browser_pacing WHERE scope='gemini-account'").fetchone()
            access_interval=30 if row['recovery_remaining'] else 10
            eligible=max(row['last_access']+access_interval,
                         row['last_submit']+10 if kind=='submit' else 0)
            remaining=max(math.ceil(eligible-at),self._coordinator_wait(owner,at))
            if remaining:
                return {'ready':False,'seconds_remaining':remaining,'kind':kind}
            self._coordinator_grant(owner,kind,at,max(access_interval,10))
            self.db.execute("UPDATE browser_pacing SET last_access=?,last_submit=CASE WHEN ?='submit' THEN ? ELSE last_submit END WHERE scope='gemini-account'",
                            (at,kind,at))
            return {'ready':True,'kind':kind}

    def _claim_submit_slot(self, recovery_retry_exempt=False, owner=None):
        """Called inside the transition transaction before committing a send intent."""
        self.db.execute("INSERT OR IGNORE INTO browser_pacing(scope) VALUES('gemini-account')")
        row=self.db.execute("SELECT * FROM browser_pacing WHERE scope='gemini-account'").fetchone()
        at=time.time()
        recovering=row['recovery_remaining']>0
        access_interval=10 if recovery_retry_exempt else (30 if recovering else 10)
        submit_interval=180 if recovering and not recovery_retry_exempt else 10
        eligible=max(row['last_access']+access_interval,row['last_submit']+submit_interval)
        remaining=max(math.ceil(eligible-at),self._coordinator_wait(owner,at))
        if remaining:
            return remaining
        self._coordinator_grant(owner,'submit',at,ACCOUNT_COORDINATOR_HOLD)
        if recovery_retry_exempt:
            self.db.execute("UPDATE browser_pacing SET last_access=?,last_submit=? WHERE scope='gemini-account'",(at,at))
        else:
            self.db.execute("UPDATE browser_pacing SET last_access=?,last_submit=?,recovery_remaining=max(0,recovery_remaining-1) WHERE scope='gemini-account'",(at,at))
        return 0

    def _record_submission(self,run,cid,vid,attempt,recovery_remaining):
        unresolved=self.db.execute("SELECT count(*) FROM videos WHERE state IN ('submitting','submitted','uncertain')").fetchone()[0]
        self.db.execute("INSERT INTO gemini_submission_events(submitted,channel_id,run_id,video_id,attempt,mode,policy,recovery_remaining,unresolved_submissions) VALUES(?,?,?,?,?,?,?,?,?)",
            (time.time(),cid,run['id'],vid,attempt,run['mode'],run['policy'],recovery_remaining,unresolved))

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
            # ``pace`` is the row read before the reset above.  Do not let the
            # stale snapshot leak back into the run or make the next prepare
            # believe that the same batch break is still active.
            pace=self.db.execute('SELECT * FROM pacing WHERE scope=?',(pace['scope'],)).fetchone()
        self._sync_pacing(run)
        return remaining

    def _count_processed(self,run,cid,vid):
        v=self.video(cid,vid)
        if v['processed_counted']:
            return
        if run is None:
            key='gemini-account'
            self.db.execute('INSERT OR IGNORE INTO pacing(scope) VALUES(?)',(key,))
        else:
            key=self._sync_pacing(run)['scope']
        self.db.execute('UPDATE videos SET processed_counted=1 WHERE channel_id=? AND id=?',(cid,vid))
        self.db.execute('UPDATE pacing SET processed=processed+1,break_started=CASE WHEN processed=? THEN ? ELSE break_started END WHERE scope=?',(BREAK_EVERY-1,time.time(),key))
        if run is not None:
            self._sync_pacing(run)
            self._coordinator_release(run)

    def reserve(self,run,parallel=False):
        with self.tx():
            self.check_lease(run)
            if parallel:
                raise Stop('Video processing is strictly serial')
            return self._reserve_serial(run)

    def _reserve_serial(self,run,archive_only=False):
        r=self.db.execute('SELECT * FROM runs WHERE id=?',(run,)).fetchone()
        history=int(r['mode']=='history')
        channel = self.channel(r['channel_id']) if r['channel_id'] else None
        if channel and not channel['notebook']:
            return {'stop':'notebook_required',
                    'next_action':'verify and register the channel Gemini Notebook before processing'}
        if history and r['channel_id']:
            date_status = self.date_backfill_status(r['channel_id'])
            if date_status['blocked']:
                return {'stop':'date_backfill_required','next_action':date_status['next_action']}
        scope=' AND channel_id=?' if r['channel_id'] else ''
        args=(history,r['channel_id']) if r['channel_id'] else (history,)
        active=self.db.execute("SELECT * FROM videos WHERE history=? AND state IN ('submitting','submitted','uncertain')"+scope+' ORDER BY found LIMIT 1',args).fetchone()
        if active:
            if archive_only:
                raise Stop('Resolve the active submission before finalization')
            return self._claim(run,active)
        backlog=self.db.execute("SELECT * FROM videos WHERE history=? AND state IN ('summarized','renamed')"+scope+" ORDER BY (state='renamed') DESC,found LIMIT 1",args).fetchone()
        batch = r['mode']=='history' and r['archive_mode']=='extension'
        unqueued = backlog and not self.db.execute('SELECT 1 FROM history_rename_items WHERE channel_id=? AND video_id=?',(backlog['channel_id'],backlog['id'])).fetchone()
        if backlog and (not batch or archive_only or unqueued):
            self.db.execute("UPDATE runs SET phase='finalize' WHERE id=?",(run,))
            return self._claim(run,backlog)
        self.db.execute("UPDATE runs SET phase='generate' WHERE id=?",(run,))
        if archive_only:
            return {'stop':'no_finalize'}
        candidate_scope = " AND EXISTS (SELECT 1 FROM run_candidates rc WHERE rc.run_id=? AND rc.channel_id=v.channel_id AND rc.video_id=v.id)" if r['mode']=='incremental' and r['schedule_key'] else ''
        pending_args = (history, r['channel_id'], run) if r['channel_id'] and candidate_scope else ((history, run) if candidate_scope else args)
        pending=self.db.execute("SELECT v.* FROM videos v JOIN channels c ON c.id=v.channel_id WHERE v.history=? AND v.state IN ('pending','reserved') AND v.missing=0 AND c.initialized=1 AND c.notebook IS NOT NULL"+(' AND v.channel_id=?' if r['channel_id'] else '')+candidate_scope+" ORDER BY (v.state='reserved') DESC,c.turn,COALESCE(v.published,'') DESC,v.found LIMIT 1",pending_args).fetchone()
        if not pending:
            if backlog and batch:
                return {'stop':'archive_pending','next_action':'history-rename serve RUN; finish external extension batch'}
            return {'stop':'empty'}
        if r['mode']=='incremental' and r['armed']>=20:
            return {'stop':'round_cap','armed':r['armed']}
        if r['mode']=='incremental' and self.budget_used(r['channel_id'])>=20:
            return {'stop':'daily_cap','used':self.budget_used(r['channel_id'])}
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
            owner=self.db.execute('SELECT channel_id FROM runs WHERE id=?',(run,)).fetchone()
            if owner and any(l['run_id']!=run for l in self.conflicting_leases(owner['channel_id'])):
                raise Stop('Cannot clean another worker lease')
            if not self.db.execute('SELECT 1 FROM runs WHERE id=?',(run,)).fetchone():
                raise Stop('Unknown run')
            self.db.execute("UPDATE videos SET state='pending',owner=NULL WHERE owner=? AND state='reserved'",(run,))
            self.db.execute("UPDATE videos SET state='uncertain',reason='unknown_submit' WHERE owner=? AND state='submitting'",(run,))
            self.db.execute("UPDATE videos SET reopen_required=COALESCE(reopen_required,'page_interrupted') WHERE owner=? AND state IN ('submitted','summarized','renamed')",(run,))
            self.db.execute('DELETE FROM lease WHERE run_id=?',(run,))
            # A crashed or forcibly terminated rename bridge cannot execute
            # Bridge.close(); cleanup must invalidate its session explicitly.
            self.db.execute('DELETE FROM history_rename_sessions WHERE run_id=?',(run,))
            self._coordinator_release(run)
            return {'cleaned':True}

    def adopt_unknown(self,cid,evidence,run_id=None):
        with self.tx():
            if evidence!='notebook-identity-checked':
                raise Stop('Notebook identity evidence required')
            self.channel(cid)
            if any(l['expires']>time.time() and l['run_id']!=run_id for l in self.conflicting_leases(cid)):
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
        if row['topic'] or row['title_format'] == 'original':
            d['expected_title'] = title_for(row)
        if row['state']=='reserved':
            d['prompt'] = d['url'] + '\n总结'
        return d

    def budget_used(self,channel_id=None):
        if channel_id:
            row=self.db.execute('SELECT COALESCE(sum(used),0) FROM daily_budget WHERE day=? AND channel_id IN (?,?)',(now().date().isoformat(),channel_id,'*')).fetchone()
        else:
            row=self.db.execute('SELECT COALESCE(sum(used),0) FROM daily_budget WHERE day=?',(now().date().isoformat(),)).fetchone()
        return row[0]

    def daily_due(self,channel_id=None):
        if channel_id is None:
            return any(self.daily_due(c[0]) for c in self.db.execute('SELECT id FROM channels WHERE initialized=1 AND tracking=1'))
        tracked = self.db.execute('SELECT 1 FROM channels WHERE id=? AND initialized=1 AND tracking=1',(channel_id,)).fetchone()
        if not tracked:
            return False
        t = now()
        if (t.hour,t.minute)<(5,0):
            return False
        day=t.date().isoformat()
        r = self.db.execute('SELECT 1 FROM runs WHERE schedule_key IN (?,?) AND finished=1',
                            (day+':'+channel_id,day)).fetchone()
        return not bool(r)

    def daily_candidates(self):
        """Return the scheduled incremental candidates without filtering pauses or leases."""
        return [dict(row) for row in self.db.execute(
            """SELECT id, name, notebook
               FROM channels
               WHERE initialized=1 AND tracking=1
               ORDER BY rowid""") if self.daily_due(row['id'])]

    def history_complete(self, channel_id):
        """Whether all historical items and archive work are settled."""
        outstanding = self.db.execute(
            """SELECT 1 FROM videos
               WHERE channel_id=? AND history=1
                 AND state NOT IN ('verified','deferred')
               LIMIT 1""",
            (channel_id,),
        ).fetchone()
        if outstanding:
            return False
        rename_pending = self.db.execute(
            """SELECT 1 FROM history_rename_items
               WHERE channel_id=? AND chat_status='pending' LIMIT 1""",
            (channel_id,),
        ).fetchone()
        return not bool(rename_pending)

    def park(self,run):
        """Release a historical worker at a safe boundary for the daily round."""
        with self.tx():
            self.check_lease(run)
            if self.db.execute('SELECT 1 FROM history_rename_sessions WHERE run_id=? AND expires>?',(run,time.time())).fetchone():
                raise Stop('Stop the external rename bridge before handing off history')
            r = self.db.execute('SELECT mode,channel_id FROM runs WHERE id=?',(run,)).fetchone()
            if r['mode']!='history':
                raise Stop('Only a historical round can be parked')
            scope=' AND channel_id=?' if r['channel_id'] else ''
            if self.db.execute("SELECT 1 FROM videos WHERE history=1 AND (state IN ('submitting','submitted','uncertain') OR (state IN ('summarized','renamed') AND NOT EXISTS (SELECT 1 FROM history_rename_items h WHERE h.channel_id=videos.channel_id AND h.video_id=videos.id)))"+scope+' LIMIT 1',(r['channel_id'],) if r['channel_id'] else ()).fetchone():
                raise Stop('Resolve the active chat before parking')
            self.db.execute("UPDATE videos SET state='pending',owner=NULL WHERE owner=? AND state='reserved'",(run,))
            self.db.execute("UPDATE videos SET owner=NULL,reopen_required=COALESCE(reopen_required,'page_interrupted') WHERE owner=? AND state='summarized'",(run,))
            self.db.execute('UPDATE runs SET finished=1 WHERE id=?',(run,))
            self.db.execute('DELETE FROM lease WHERE run_id=?',(run,))
            self._coordinator_release(run)

    def transition(self,run,cid,vid,action,value=None,evidence=None):
        with self.tx():
            self.check_lease(run)
            current_run=self.db.execute('SELECT * FROM runs WHERE id=?',(run,)).fetchone()
            if current_run['channel_id'] and current_run['channel_id']!=cid:
                raise Stop('Video is outside run channel scope')
            if action in {'arm','retry'}:
                if self.db.execute('SELECT 1 FROM history_rename_sessions WHERE run_id=? AND expires>?',(run,time.time())).fetchone():
                    raise Stop('Stop the external rename bridge before generating another video')
                pause=self.pause_for_run(run)
                if pause:
                    raise Stop('Submission blocked by '+pause+' pause')
            v = self.video(cid,vid)
            if action == 'retitle':
                if cid != 'UCBKDRq35-L8xev4O7ZqBeLg':
                    raise Stop('Retitle maintenance is enabled only for the Wang Zhi\'an channel')
                if v['state'] not in {'summarized','renamed','verified','deferred'}:
                    raise Stop('Only an archived or explicitly deferred chat can be retitled')
                if value != title_for(v) or evidence not in REOPEN_EVIDENCE | {CURRENT_PAGE_EVIDENCE}:
                    raise Stop('Require the exact computed title and visible chat-title evidence')
                return {'retitled':vid,'title':value,'state':v['state']}
            if v['owner'] != run:
                reclaimable = (
                    action=='skip' and v['state']=='uncertain' and not v['chat'] and evidence=='search-no-result'
                ) or (
                    action=='attach' and v['state']=='uncertain' and not v['chat'] and
                    evidence in {'video-match', 'marker-and-video-match'}
                ) or (
                    action=='cleanup-invalid' and v['state']=='deferred' and v['chat'] and
                    v['chat_cleanup_status']!='verified'
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
                    raise Stop(f'Finish the {BREAK_SECONDS}-second break before submitting')
                scope=' AND channel_id=?' if current_run['channel_id'] else ''
                params=(v['history'],vid,current_run['channel_id']) if current_run['channel_id'] else (v['history'],vid)
                batch = current_run['mode']=='history' and current_run['archive_mode']=='extension'
                outstanding = "state IN ('submitting','submitted','summarized','renamed','uncertain')"
                if batch:
                    outstanding = "(state IN ('submitting','submitted','uncertain') OR (state IN ('summarized','renamed') AND NOT EXISTS (SELECT 1 FROM history_rename_items h WHERE h.channel_id=videos.channel_id AND h.video_id=videos.id)))"
                if self.db.execute("SELECT 1 FROM videos WHERE history=? AND id<>? AND "+outstanding+scope+' LIMIT 1',params).fetchone():
                    raise Stop('Finish the current video before submitting another')
                if v['state']!='reserved' or not metadata_fresh(v):
                    raise Stop('Only a reserved item with verified metadata can be armed')
                r = self.db.execute('SELECT * FROM runs WHERE id=?',(run,)).fetchone()
                if r['mode']=='incremental' and r['armed']>=20:
                    raise Stop('20 submissions already reserved in this round')
                if r['mode']=='incremental':
                    if self.budget_used(cid)>=20:
                        raise Stop('Channel daily submission budget exhausted')
                recovery_remaining=self.db.execute("SELECT recovery_remaining FROM browser_pacing WHERE scope='gemini-account'").fetchone()
                recovery_remaining=recovery_remaining['recovery_remaining'] if recovery_remaining else 0
                wait=self._claim_submit_slot(owner=run)
                if wait:
                    return {'ready':False,'seconds_remaining':wait,'kind':'submit'}
                if r['mode']=='incremental':
                    self.db.execute('INSERT INTO daily_budget VALUES(?,?,1) ON CONFLICT(channel_id,day) DO UPDATE SET used=used+1',(cid,now().date().isoformat()))
                self.db.execute('UPDATE runs SET armed=armed+1 WHERE id=?',(run,))
                self.db.execute("UPDATE videos SET state='submitting' WHERE channel_id=? AND id=?",args)
                self._record_submission(r,cid,vid,1,recovery_remaining)
            elif action=='retry':
                attached = v['state']=='submitted' and v['chat'] and evidence=='20s-no-body-stopped-same-chat'
                current_page = (v['state']=='submitting' and not v['chat'] and
                                evidence=='20s-no-body-stopped-current-page')
                strict_recovery = (
                    current_run['policy']=='strict' and current_run['mode']=='history'
                    and current_run['channel_id']==cid and v['history']
                    and v['state']=='uncertain' and v['reason']=='unknown_submit'
                    and not v['chat'] and evidence=='search-no-result'
                )
                fast_retry = (current_run['policy']=='collect-first' and
                              (attached or current_page) and not v['reopen_required'])
                if v['retry_count']!=0 or not (strict_recovery or fast_retry):
                    raise Stop('Retry requires confirmed first request, stopped generation, uninterrupted page and no prior retry')
                if current_run['mode']=='incremental':
                    if current_run['armed']>=20 or self.budget_used(cid)>=20:
                        raise Stop('Submission budget exhausted; defer generation_timeout instead')
                recovery_remaining=self.db.execute("SELECT recovery_remaining FROM browser_pacing WHERE scope='gemini-account'").fetchone()
                recovery_remaining=recovery_remaining['recovery_remaining'] if recovery_remaining else 0
                wait=self._claim_submit_slot(recovery_retry_exempt=(current_run['policy']=='collect-first'), owner=run)
                if wait:
                    return {'ready':False,'seconds_remaining':wait,'kind':'submit'}
                if current_run['mode']=='incremental':
                    self.db.execute('INSERT INTO daily_budget VALUES(?,?,1) ON CONFLICT(channel_id,day) DO UPDATE SET used=used+1',(cid,now().date().isoformat()))
                self.db.execute('UPDATE runs SET armed=armed+1 WHERE id=?',(run,))
                self.db.execute("UPDATE videos SET state='submitting',retry_count=retry_count+1 WHERE channel_id=? AND id=?",args)
                self._record_submission(current_run,cid,vid,2,recovery_remaining)
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
                if v['state']!='submitted' or evidence!=SUMMARY_NOTEBOOK_EVIDENCE:
                    raise Stop('Require a completed response verified in the registered Gemini Notebook')
                configured = v['title_format']
                topic = None if configured == 'original' else (value or v['topic'])
                title_for(v, topic)
                self.db.execute("UPDATE videos SET state='summarized',topic=?,final_title=NULL,notebook_summary_checked=1,topic_status=CASE WHEN ? IS NULL THEN topic_status ELSE 'confirmed' END WHERE channel_id=? AND id=?",(topic, value, *args))
                self.db.execute("UPDATE runs SET phase='finalize' WHERE id=?",(run,))
                if current_run['mode']=='history' and current_run['archive_mode']=='extension':
                    self.db.execute('INSERT OR IGNORE INTO history_rename_items(channel_id,video_id) VALUES(?,?)',args)
                    # Count the checked generation once, not again at batch rename.
                    self._count_processed(run,cid,vid)
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
                if not v['notebook_summary_checked']:
                    raise Stop('Summary was not verified in the registered Gemini Notebook')
                if v['reopen_required'] and evidence not in REOPEN_EVIDENCE:
                    raise Stop('This chat requires reopened verification after '+v['reopen_required'])
                self.db.execute("UPDATE videos SET state='verified',owner=NULL,reason=NULL,reopen_required=NULL WHERE channel_id=? AND id=?",args)
                self._count_processed(run,cid,vid)
            elif action=='cleanup-invalid':
                if v['state']!='deferred' or v['reason'] not in {'generation_timeout','content_failed','chat_unidentified','review:content_mismatch','review:summary_failed'}:
                    raise Stop('Only a deferred failed chat can be cleaned')
                if evidence!='chat-deleted-in-notebook':
                    raise Stop('Delete the invalid chat in the registered Notebook and provide deletion evidence')
                self.db.execute("UPDATE videos SET chat_cleanup_status='verified',chat_cleanup_reason=? WHERE channel_id=? AND id=?",(v['reason'],*args))
            elif action=='require-reopen':
                if value not in REOPEN_REASONS or v['state'] not in {'submitting','submitted','summarized','renamed','uncertain'}:
                    raise Stop('Choose a supported reopen reason for an unfinished chat')
                self.db.execute('UPDATE videos SET reopen_required=? WHERE channel_id=? AND id=?',(value,*args))
            elif action=='uncertain':
                if v['state'] not in {'submitting','submitted','summarized','renamed','uncertain'}:
                    raise Stop('No submission to reconcile')
                # Scheduled collect-first incrementals may continue after a
                # page-interrupted submission.  Preserve the item as an
                # explicit deferral and never make it eligible for resend;
                # the worker must open a fresh blank Notebook chat before the
                # next arm.  Strict runs and historical runs retain the
                # conservative unknown-submit pause.
                if current_run['mode']=='incremental' and current_run['policy']=='collect-first':
                    self.db.execute("UPDATE videos SET state='deferred',reason='submission_unconfirmed',deferral_policy='collect-first',owner=NULL,reopen_required=COALESCE(reopen_required,'page_interrupted') WHERE channel_id=? AND id=?",args)
                    self._count_processed(run,cid,vid)
                else:
                    self.db.execute("UPDATE videos SET state='uncertain',reason='unknown_submit',reopen_required=COALESCE(reopen_required,'page_interrupted') WHERE channel_id=? AND id=?",args)
                if current_run['policy']=='strict':
                    self.set_pause('unknown_submit',cid)
            elif action=='defer':
                collect=current_run['policy']=='collect-first'
                # Strict runs may also defer confirmed generation failures and
                # identity/content failures; WORKER requires these to remain
                # explicit rather than being misclassified as source errors.
                allowed=COLLECT_REASONS if collect else {
                    'generation_timeout','submission_unconfirmed','chat_unidentified',
                    'content_failed','source_unavailable','metadata_unavailable',
                    'live_not_ready','archive_unverified','post_retry_unverified'
                }
                if value=='post_retry_unverified' and not (
                    current_run['policy']=='strict' and current_run['mode']=='history'
                    and current_run['channel_id']==cid and v['history']
                    and v['state']=='uncertain' and v['retry_count']==1
                ):
                    raise Stop('Require an unconfirmed historical submission after the recovery retry')
                if value not in allowed:
                    raise Stop('Unknown deferral reason')
                if v['state'] not in {'reserved','submitting','submitted','uncertain','summarized','renamed'}:
                    raise Stop('Cannot defer an already completed summary or repeat a deferral')
                if v['state'] in {'summarized','renamed'} and value!='archive_unverified':
                    raise Stop('Preserve generated content as archive_unverified')
                self.db.execute("UPDATE videos SET state='deferred',reason=?,deferral_policy=?,owner=NULL,metadata_checked=NULL WHERE channel_id=? AND id=?",(value,current_run['policy'],*args))
                if v['chat'] and value in {'generation_timeout','content_failed','chat_unidentified'}:
                    self.db.execute("UPDATE videos SET chat_cleanup_status='required',chat_cleanup_reason=? WHERE channel_id=? AND id=?",(value,*args))
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
            r=self.db.execute('SELECT * FROM runs WHERE id=?',(run,)).fetchone()
            if r['mode']=='incremental' and r['schedule_key']:
                if not r['scan_stamp'] or r['scan_stamp'][:10]!=now().date().isoformat():
                    raise Stop('Complete this round inventory scan before ending')
                channels=[r['channel_id']] if r['channel_id'] else [c[0] for c in self.db.execute('SELECT id FROM channels')]
                for cid in channels:
                    checked=self.db.execute('SELECT checked FROM run_inventory WHERE run_id=? AND channel_id=?',(run,cid)).fetchone()
                    if not checked or checked[0][:10]!=now().date().isoformat():
                        raise Stop('Complete all scoped channel inventories before ending')
                if r['channel_id']:
                    remaining=self.db.execute("SELECT count(*) FROM videos v JOIN run_candidates rc ON rc.channel_id=v.channel_id AND rc.video_id=v.id WHERE rc.run_id=? AND v.state NOT IN ('verified','deferred')",(run,)).fetchone()[0]
                else:
                    remaining=self.db.execute("SELECT count(*) FROM videos WHERE history=0 AND state NOT IN ('verified','deferred')").fetchone()[0]
                if remaining and self.budget_used(r['channel_id'])<20 and r['armed']<20:
                    raise Stop('Incremental tasks remain; continue before ending')
                self.db.execute('UPDATE runs SET outcome=? WHERE id=?',('budget_exhausted' if remaining else 'complete',run))
            unresolved = self.db.execute("SELECT count(*) FROM videos WHERE owner=? AND state IN ('submitting','submitted','summarized','renamed','uncertain')",(run,)).fetchone()[0]
            if r['mode']=='history' and r['channel_id'] and self.db.execute("SELECT 1 FROM history_rename_items WHERE channel_id=? AND chat_status='pending' LIMIT 1",(r['channel_id'],)).fetchone():
                raise Stop('Finish or explicitly review the history rename backlog before ending')
            if unresolved:
                raise Stop('Finish or pause the outstanding chat before ending the round')
            self.db.execute("UPDATE videos SET state='pending',owner=NULL WHERE owner=? AND state='reserved'",(run,))
            self.db.execute('UPDATE runs SET finished=1 WHERE id=?',(run,))
            self.db.execute('DELETE FROM lease WHERE run_id=?',(run,))
            self._coordinator_release(run)

    def handoff(self,run):
        r=self.db.execute('SELECT * FROM runs WHERE id=?',(run,)).fetchone()
        if not r or not r['channel_id']:
            raise Stop('Handoff requires a channel-scoped run')
        cid=r['channel_id']
        if r['mode']=='history':
            if not self.history_complete(cid):
                raise Stop('history_pending: finish the channel history before incremental handoff')
            if not self.daily_due(cid):
                raise Stop('Daily incremental is not due')
            self.park(run)
            target=self.begin('incremental',scheduled=True,channel_id=cid,policy='collect-first',
                              allow_history_handoff=True)
            self.db.execute('UPDATE runs SET parent_run=? WHERE id=?',(run,target['id']))
            return {'run':target['id'],'mode':'incremental','channel_id':cid,'next_action':'scan --channel CHANNEL --run RUN; process until empty/daily_cap; handoff RUN'}
        if not r['parent_run']:
            raise Stop('No historical parent to resume; end this daily round normally')
        parent=self.db.execute('SELECT * FROM runs WHERE id=?',(r['parent_run'],)).fetchone()
        self.end(run)
        target=self.begin('history',channel_id=cid,policy=parent['policy'])
        self.db.execute('UPDATE runs SET archive_mode=? WHERE id=?',(parent['archive_mode'],target['id']))
        return {'run':target['id'],'mode':'history','channel_id':cid,'policy':target['policy'],'next_action':'prepare RUN; continue in the same worker'}

    def status(self,verbose=True,channel_id=None):
        if not verbose:
            lease_query = 'SELECT run_id,expires FROM lease WHERE expires>?'
            lease_params = [time.time()]
            if channel_id:
                lease_query += " AND (scope='*' OR scope=?)"
                lease_params.append(channel_id)
            lease = self.db.execute(lease_query, lease_params).fetchone()
            return {'pause':self.setting('pause'), 'pauses':self.pause_status(), 'busy':bool(lease),
                    'leases':[dict(x) for x in self.db.execute(lease_query.replace('run_id,expires','scope,run_id,expires'), lease_params)],
                    'daily_due':self.daily_due(channel_id), 'channel_id':channel_id,
                    'daily_used':self.budget_used(channel_id),
                    'coordinator':self.coordinator_status(),
                    'states':[dict(x) for x in self.db.execute('SELECT history,state,count(*) AS count FROM videos'+(' WHERE channel_id=?' if channel_id else '')+' GROUP BY history,state',(channel_id,) if channel_id else ())]}
        return {'pause':self.setting('pause'), 'pauses':self.pause_status(), 'coordinator':self.coordinator_status(), 'channels':[dict(x) for x in self.db.execute('SELECT * FROM channels')],
                'states':[dict(x) for x in self.db.execute('SELECT state,history,count(*) AS count FROM videos GROUP BY state,history')],
                'scans':[dict(x) for x in self.db.execute('SELECT * FROM scans')],
                'lease':[dict(x) for x in self.db.execute('SELECT * FROM lease')]}

    def report(self,mode='history',channel_id=None):
        if mode not in {'history','incremental'}:
            raise Stop('Unknown report mode')
        if mode == 'incremental':
            # A daily report must describe the scheduled day's discovery set.
            # The old global history=0 backlog can include baseline rows created
            # by a recovered database and is not evidence of new uploads.
            day = now().date().isoformat()
            query='''SELECT DISTINCT v.*, c.title_format
                     FROM videos v
                     JOIN channels c ON c.id=v.channel_id
                     WHERE v.history=0
                       AND EXISTS (
                           SELECT 1 FROM run_candidates rc
                           JOIN runs r ON r.id=rc.run_id
                           WHERE rc.channel_id=v.channel_id
                             AND rc.video_id=v.id
                             AND r.mode='incremental'
                             AND r.schedule_key LIKE ?
                       )'''
            params=(day+'%',)
        else:
            query='SELECT v.*, c.title_format FROM videos v JOIN channels c ON c.id=v.channel_id WHERE v.history=1'
            params=()
        if channel_id:
            self.channel(channel_id)
            query+=' AND v.channel_id=?'
            params+=(channel_id,)
        rows=list(self.db.execute(query+' ORDER BY found',params))
        channel_reports={}
        for row in rows:
            key=row['channel_id']
            if key not in channel_reports:
                channel_reports[key]={
                    'channel_id':key,
                    'channel_name':self.db.execute('SELECT name FROM channels WHERE id=?',(key,)).fetchone()[0],
                    'counts':{},
                    'candidates':[],
                }
            bucket=channel_reports[key]
            bucket['counts'][row['state']]=bucket['counts'].get(row['state'],0)+1
            candidate={
                'video_id':row['id'],
                'original_title':row['title'],
                'state':row['state'],
            }
            if row['final_title']:
                candidate['final_title']=row['final_title']
            if row['state']=='deferred':
                candidate['reason']=row['reason']
            if row['topic'] or row['title_format']=='original':
                candidate['expected_title']=title_for(row)
            bucket['candidates'].append(candidate)
        counts={}
        review=[]
        deferred={}
        archive_unverified=0
        generated=0
        remaining=0
        total=0
        for row in rows:
            counts[row['state']]=counts.get(row['state'],0)+1
            total+=1
            generated+=int(row['state'] in {'summarized','renamed','verified'} or (row['state']=='deferred' and row['reason']=='archive_unverified' and bool(row['topic'])))
            if row['state']=='deferred':
                if row['reason']=='archive_unverified':
                    archive_unverified+=1
                else:
                    deferred[row['reason']]=deferred.get(row['reason'],0)+1
                item={k:row[k] for k in ('channel_id','id','title','reason','chat','missing')}
                item['url']='https://www.youtube.com/watch?v='+row['id']
                if row['topic'] or row['title_format'] == 'original':
                    item['expected_title']=title_for(row)
                review.append(item)
            elif row['state']!='verified':
                remaining+=1
        rename_pending = self.db.execute("SELECT count(*) FROM history_rename_items WHERE chat_status='pending'"+(' AND channel_id=?' if channel_id else ''),(channel_id,) if channel_id else ()).fetchone()[0] if mode=='history' else 0
        rename_review = [dict(x) for x in self.db.execute("SELECT channel_id,video_id,chat_status,reason FROM history_rename_items WHERE chat_status='review'"+(' AND channel_id=?' if channel_id else ''),(channel_id,) if channel_id else ())] if mode=='history' else []
        rename_pending_ids = {row[0] for row in self.db.execute("SELECT video_id FROM history_rename_items WHERE chat_status='pending'"+(' AND channel_id=?' if channel_id else ''),(channel_id,) if channel_id else ())} if mode=='history' else set()
        notebook_unverified = sum(1 for row in rows if row['state'] in {'summarized','renamed','verified'} and not row['notebook_summary_checked'])
        notebook_verified = sum(1 for row in rows if row['state'] in {'summarized','renamed','verified'} and row['notebook_summary_checked'])
        cleanup_pending = [
            {
                'channel_id': row['channel_id'],
                'video_id': row['id'],
                'reason': row['reason'],
                'chat': row['chat'],
                'url': 'https://www.youtube.com/watch?v='+row['id'],
            }
            for row in rows
            if row['state']=='deferred' and row['chat'] and row['chat_cleanup_status']!='verified'
        ]
        cleanup_required = len(cleanup_pending)
        archive_review_ids={row['id'] for row in rows if row['state']=='deferred' and row['reason']=='archive_unverified'}
        archive_review_ids.update(item['video_id'] for item in rename_review)
        manual_review_count = sum(1 for row in rows if row['state']=='deferred' and row['reason']!='archive_unverified') + len(archive_review_ids)
        completed_count = sum(1 for row in rows if row['state']=='verified')
        date_status = self.date_backfill_status(channel_id) if channel_id else None
        complete = remaining==0 and rename_pending==0 and notebook_unverified==0
        if mode=='history' and channel_id and complete:
            with self.tx():
                self.db.execute('UPDATE channels SET tracking=1 WHERE id=?',(channel_id,))
        summary_deferred=[{'reason':k,'count':v} for k,v in sorted(deferred.items())]
        result={'mode':mode,'channel_id':channel_id,'complete':complete,'remaining':remaining,'rename_pending':rename_pending,
                'rename_manual_review':rename_review,'counts':counts,'deferred':summary_deferred,
                'summary_deferred':summary_deferred,'archive_unverified':archive_unverified,
                'archive_manual_review_count':len(archive_review_ids),
                'summary_done_pending_rename':len(rename_pending_ids),
                'manual_review_count':manual_review_count,
                'completed_count':completed_count,
                 'notebook_verified':notebook_verified,
                 'notebook_unverified':notebook_unverified,
                 'cleanup_required':cleanup_required,
                 'cleanup_pending':cleanup_pending,
                'manual_review':review,'generated':generated,'summary_completed':generated,
                'verified':counts.get('verified',0),
                'not_generated':total-generated,'success_rate':generated/total if total else 0,
                'date_backfill':date_status,
                'channels':list(channel_reports.values())}
        if mode == 'incremental':
            legacy_query='''SELECT COUNT(*) FROM videos v
                            WHERE v.history=0
                              AND v.state NOT IN ('verified','deferred')'''
            legacy_params=()
            if channel_id:
                legacy_query+=' AND v.channel_id=?'
                legacy_params=(channel_id,)
            result['scope']='scheduled-day'
            result['schedule_key_prefix']=day
            result['legacy_backlog']=self.db.execute(legacy_query,legacy_params).fetchone()[0]
        return result

class MetadataFailure(Stop):
    """Only a safe category is exposed; raw extractor diagnostics stay in memory."""

def metadata_failure(exc):
    message = str(exc).lower()
    if message in {'youtube_auth_required','youtube_rate_limited','metadata_network_error','metadata_error'}:
        return message
    if any(x in message for x in ('not a bot', 'captcha', '/sorry', 'unusual traffic')):
        return 'youtube_auth_required'
    if re.search(r'\b429\b|too many requests', message):
        return 'youtube_rate_limited'
    if any(x in message for x in ('timed out', 'timeout', 'connection reset', 'unable to connect', 'name resolution')):
        return 'metadata_network_error'
    return 'metadata_error'

class Quiet:
    def debug(self,*a): pass
    def warning(self,*a):
        category = metadata_failure(' '.join(map(str,a)))
        if category in {'youtube_auth_required', 'youtube_rate_limited'}:
            raise MetadataFailure(category)
    def error(self,*a): self.warning(*a)

def extract(url,flat=False,limit=None):
    from yt_dlp import YoutubeDL
    opts = {'skip_download':True,'extract_flat':flat,'lazy_playlist':True,
            'quiet':True,'no_warnings':True,'logger':Quiet(),
            'ignoreerrors':False,'noplaylist':not flat,'retries':0,'extractor_retries':0,
            'socket_timeout':30,'cachedir':False,'writesubtitles':False,
            'writeautomaticsub':False,'writethumbnail':False,'writeinfojson':False,
            'playlistend':limit}
    try:
        with YoutubeDL(opts) as ydl:
            data = ydl.extract_info(url,download=False) if flat else ydl.extract_info(url,download=False,process=False)
            if not data:
                raise Stop('Empty metadata response')
            if flat:
                # Consume lazy entries while the extractor context remains alive.
                return {**data,'entries':list(data.get('entries',[]))}
            return data
    except MetadataFailure:
        raise


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


def preflight(s, cid):
    """Report local readiness without opening a browser or changing queue state."""
    channel = dict(s.channel(cid))
    config = s.channel_config(cid)
    date_status = s.date_backfill_status(cid)
    notebook_registered = bool(channel['notebook'])
    _, api_key_source = youtube_data_api_key()
    try:
        import yt_dlp  # noqa: F401
        yt_dlp_ready = True
    except ImportError:
        yt_dlp_ready = False
    return {
        # A local CLI cannot certify the approved browser session. WorkBuddy
        # must perform that visible-page check before queue operations.
        'ready': False,
        'channel': {
            'id': channel['id'],
            'name': channel['name'],
            'notebook_registered': notebook_registered,
        },
        'configuration': config,
        'date_backfill': date_status,
        'database': {
            'path': str(s.path),
            'canonical': True,
            'write_performed': False,
        },
        'requirements': {
            'browser': {
                'required': True,
                'satisfied': False,
                'evidence': 'WorkBuddy must prove readable Gemini DOM and one harmless UI action in its approved browser',
            },
            'notebook': {
                'required': True,
                'satisfied': notebook_registered,
            },
            'yt_dlp': {
                'required': True,
                'satisfied': yt_dlp_ready,
            },
            'date_backfill': {
                'required': date_status['required'],
                'satisfied': not date_status['blocked'],
            },
            'youtube_data_api': {
                'required': date_status['required'],
                'configured': api_key_source is not None,
                'source': api_key_source,
                'key': 'present' if api_key_source else 'missing',
                'next_action': (
                    None if api_key_source else
                    '在 Windows 当前用户环境变量中设置 YOUTUBE_DATA_API_KEY；不要发送到聊天或写入项目文件'
                ),
            },
        },
        'notebook_setup': {
            'required': True,
            'registered': notebook_registered,
            'next_action': (
                None if notebook_registered else
                '在当前智能体允许的 Gemini 内置浏览器中创建并核验频道 Notebook，'
                '再运行 notebook CHANNEL_ID NOTEBOOK_URL 登记'
            ),
        },
    }


def require_channel_config(s, cid):
    config = s.channel_config(cid)
    if not config['complete']:
        raise Stop('Channel setup is incomplete; confirm rename strategy, title format, and membership first')


def require_processing_setup(s, cid):
    """Require the local choices and the verified Notebook mapping before queue work."""
    require_channel_config(s, cid)
    if not s.channel(cid)['notebook']:
        raise Stop('Notebook setup is incomplete; verify and register the channel Gemini Notebook first')

def scan(s,cid,full=False,fetch=extract,run=None):
    import backfill
    original_fetch=fetch
    if original_fetch is extract:
        fetch=lambda url,flat=False,limit=None: backfill.fetch(s,url,flat,limit,original_fetch)
    c = s.channel(cid)
    round_info = None
    if run:
        with s.tx():
            s.check_lease(run)
            round_info = s.db.execute('SELECT schedule_key,scan_stamp,channel_id,mode FROM runs WHERE id=?',(run,)).fetchone()
            if round_info['channel_id'] and round_info['channel_id']!=cid:
                raise Stop('Scan channel is outside run scope')
            day = (round_info['schedule_key'] or '')[:10]
            checked=s.db.execute('SELECT checked FROM run_inventory WHERE run_id=? AND channel_id=?',(run,cid)).fetchone()
            if (day and checked and checked[0][:10]==now().date().isoformat()
                    and (not full or (c['last_full'] or '')[:10]==now().date().isoformat())):
                return {'channel_id':cid,'complete':True,'skipped':'already_checked_today','tabs':[],
                        'date_coverage':'full' if full else 'recent-window','excluded_tabs':['shorts']}
            s.db.execute('DELETE FROM run_inventory WHERE run_id=? AND channel_id=?',(run,cid))
    pause = s.pause_for_run(run) if run else s.pause_for(cid)
    if pause:
        raise Stop('Paused: '+pause)
    history_inventory = bool(run and round_info['mode'] == 'history' and full)
    stamp = now().isoformat()
    complete = True
    results = []
    initial_key='initial_staging:'+cid
    if not c['initialized'] and not s.setting(initial_key):
        with s.tx():
            if run: s.check_lease(run)
            s.set(initial_key,stamp)
    # Shorts are outside this project's summary scope. Do not fetch or enqueue them.
    for tab in ('videos','streams'):
        try:
            saved=s.db.execute('SELECT status,checked FROM scans WHERE channel_id=? AND tab=?',(cid,tab)).fetchone()
            if not c['initialized'] and saved and saved['checked']>=s.setting(initial_key) and saved['status'] in {'ok','absent'}:
                results.append({'tab':tab,'status':saved['status'],'scope':'initial_saved'})
                continue
            windowed = bool(c['initialized'] and not full)
            url = c['url']+'/'+tab
            info = fetch(url,True,limit=SCAN_WINDOW) if windowed else fetch(url,True)
            entries = info.get('entries',[])
            if info.get('channel_id') not in {None,cid}:
                raise Stop('Channel identity changed')
            if windowed and date_boundary_requires_full(saved['checked'] if saved else None, entries):
                info = fetch(url,True)
                entries = info.get('entries',[])
                if info.get('channel_id') not in {None,cid}:
                    raise Stop('Channel identity changed')
                windowed = False
            elif windowed and len(entries)>=SCAN_WINDOW:
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
                excluded_members = 0
                for entry in entries:
                    if not entry:
                        raise Stop('Null entry makes the inventory incomplete')
                    if c['membership_status'] == 'no' and s._member_only_entry(entry):
                        excluded_members += 1
                        continue
                    if c['initialized']:
                        was_present = bool(s.db.execute(
                            'SELECT 1 FROM videos WHERE channel_id=? AND id=?',
                            (cid, entry.get('id'))).fetchone())
                        s.insert(cid,entry,stamp,history_inventory)
                        if (run and round_info['mode']=='incremental' and round_info['schedule_key']
                                and not was_present):
                            s.db.execute(
                                'INSERT OR IGNORE INTO run_candidates(run_id,channel_id,video_id) VALUES(?,?,?)',
                                (run, cid, entry['id']))
                        if history_inventory:
                            s.db.execute('UPDATE videos SET history=1 WHERE channel_id=? AND id=?',(cid,entry['id']))
                    else:
                        if not re.fullmatch(r'[A-Za-z0-9_-]{11}',entry.get('id') or ''):
                            raise Stop('Invalid video id in inventory')
                        s.db.execute('INSERT OR REPLACE INTO initial_inventory VALUES(?,?,?,?,?)',(cid,tab,entry['id'],entry.get('title') or entry['id'],entry.get('release_date') or entry.get('upload_date')))
                    count += 1
                s.db.execute('INSERT OR REPLACE INTO scans VALUES(?,?,?,?,?)',(cid,tab,count,'ok',stamp))
            results.append({'tab':tab,'status':'ok','count':count,'excluded_members':excluded_members,
                            'scope':'recent' if windowed else 'full'})
        except Exception as exc:
            category = metadata_failure(exc)
            if isinstance(exc, MetadataFailure) or category in {'youtube_auth_required','youtube_rate_limited','metadata_network_error'}:
                raise MetadataFailure(str(exc) if isinstance(exc, MetadataFailure) else category) from exc
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
            if not c['initialized']:
                for entry in s.db.execute('SELECT id,title,published AS upload_date FROM initial_inventory WHERE channel_id=?',(cid,)).fetchall():
                    s.insert(cid,dict(entry),stamp,True)
                s.db.execute('DELETE FROM initial_inventory WHERE channel_id=?',(cid,))
            s.db.execute('UPDATE channels SET initialized=1,last_check=? WHERE id=?',(stamp,cid))
            if run:
                s.db.execute('UPDATE runs SET scan_stamp=? WHERE id=?',(stamp,run))
                s.db.execute('INSERT OR REPLACE INTO run_inventory VALUES(?,?,?)',(run,cid,stamp))
            if full or not c['initialized']:
                s.db.execute('UPDATE channels SET last_full=? WHERE id=?',(stamp,cid))
                # Missing means absent from a complete public listing; never delete a task/chat.
                s.db.execute('UPDATE videos SET missing=1 WHERE channel_id=? AND seen<>?',(cid,stamp))
                s.db.execute("UPDATE videos SET state='deferred',reason='source_missing',deferral_policy='inventory',owner=NULL WHERE channel_id=? AND missing=1 AND state='pending'",(cid,))
    if not complete:
        date_coverage = 'partial'
    else:
        scopes = [item.get('scope') for item in results if item.get('status') == 'ok']
        if scopes and all(scope == 'full' for scope in scopes):
            date_coverage = 'full'
        elif 'full' in scopes:
            date_coverage = 'mixed'
        else:
            date_coverage = 'recent-window'
    date_status = s.date_backfill_status(cid)
    topic_result = extract_topics(s, cid) if complete and (full or not c['initialized']) else None
    title_result = generate_titles(s, cid) if complete and (full or not c['initialized']) else None
    result = {'channel_id':cid,'complete':complete,'tabs':results,
              'date_coverage':date_coverage,'date_backfill':date_status,
              'excluded_tabs':['shorts']}
    if topic_result is not None:
        result['topic_extraction'] = topic_result
    if title_result is not None:
        result['title_generation'] = title_result
    if date_status['blocked']:
        result['next_action'] = date_status['next_action']
    return result

def api_metadata(s,run,cid,vid):
    """Resolve the original title and publication date from the official Data API."""
    import backfill
    try:
        items = backfill.api_batch(s,[vid])
    except (Stop,MetadataFailure):
        raise
    except Exception as exc:
        raise MetadataFailure(metadata_failure(exc)) from exc
    item = items.get(vid)
    if not item:
        return s.transition(run,cid,vid,'defer','metadata_unavailable')
    snippet = item.get('snippet') or {}
    if snippet.get('channelId') and snippet['channelId']!=cid:
        raise Stop('Video/channel identity mismatch')
    if snippet.get('liveBroadcastContent') in {'live','upcoming'}:
        return s.transition(run,cid,vid,'defer','live_not_ready')
    title = (snippet.get('title') or '').strip()
    if not title:
        raise Stop('Metadata did not return an original title')
    published = (snippet.get('publishedAt') or '')[:10].replace('-','')
    date = None
    if published:
        try:
            date = date_value(published)
        except (Stop,TypeError):
            date = None
    with s.tx():
        s.check_lease(run)
        result = s.db.execute("UPDATE videos SET title=?,published=COALESCE(published,?),metadata_checked=?,title_source='api' WHERE channel_id=? AND id=? AND state='reserved' AND owner=?",
                              (title,date,time.time(),cid,vid,run))
        if result.rowcount!=1:
            raise Stop('Reservation changed during metadata lookup')
    return s.payload(s.video(cid,vid))

def metadata(s,run,cid,vid,fetch=extract,force=False):
    # Check before doing any network call, then again under the mutation lock.
    with s.tx():
        s.check_lease(run)
        v = s.video(cid,vid)
        if v['owner']!=run or v['state']!='reserved':
            raise Stop('Metadata update only allowed for the current reserved item')
        if not force and metadata_fresh(v):
            return s.payload(v)
    if cid in API_TITLE_CHANNELS and youtube_data_api_key()[0]:
        return api_metadata(s,run,cid,vid)
    try:
        import backfill
        info = backfill.fetch(s,'https://www.youtube.com/watch?v='+vid,extractor=fetch) if fetch is extract else fetch('https://www.youtube.com/watch?v='+vid,False)
    except Exception as exc:
        category = metadata_failure(exc)
        if isinstance(exc, MetadataFailure):
            raise
        if category != 'metadata_error':
            raise MetadataFailure(category) from exc
        if any(text in str(exc).lower() for text in ('members-only','private video','video unavailable','video has been removed','video is unavailable')):
            return s.transition(run,cid,vid,'defer','metadata_unavailable')
        raise MetadataFailure(category) from exc
    if info.get('id')!=vid or info.get('channel_id')!=cid:
        raise Stop('Video/channel identity mismatch')
    if info.get('live_status') in {'is_live','is_upcoming','post_live'}:
        return s.transition(run,cid,vid,'defer','live_not_ready')
    try:
        date = date_value(info.get('release_date') or info.get('upload_date'))
    except Stop:
        date = None
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
        active = s.db.execute("SELECT 1 FROM videos WHERE history=1 AND (state IN ('submitting','submitted','uncertain') OR (state IN ('summarized','renamed') AND NOT EXISTS (SELECT 1 FROM history_rename_items h WHERE h.channel_id=videos.channel_id AND h.video_id=videos.id)))"+scope+' LIMIT 1',(r['channel_id'],) if r['channel_id'] else ()).fetchone()
        if r['mode']=='history' and not active and s.daily_due(r['channel_id']):
            return {'stop':'daily_due','next_action':'handoff RUN; scan and process returned incremental run; handoff back to history in the same worker'}
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

def title_refresh(s,cid,limit=None,states=None):
    """Correct stored titles to the original YouTube titles through the official API."""
    import backfill
    s.channel(cid)
    wanted = [x.strip() for x in (states or 'pending,deferred').split(',') if x.strip()]
    rows = s.db.execute("SELECT id,title,title_source,published,state,chat FROM videos WHERE channel_id=? AND state IN (%s) ORDER BY (published IS NULL) DESC, published DESC, id" % ','.join('?'*len(wanted)),(cid,*wanted)).fetchall()
    if limit:
        rows = rows[:limit]
    by_id = {r['id']: r for r in rows}
    ids = list(by_id)
    changed = []; missing = []; mismatch = []
    for start in range(0,len(ids),50):
        batch = ids[start:start+50]
        items = backfill.api_batch(s,batch)
        for vid in batch:
            item = items.get(vid)
            snippet = (item or {}).get('snippet') or {}
            title = (snippet.get('title') or '').strip()
            if not item or not title:
                missing.append(vid); continue
            if snippet.get('channelId') and snippet['channelId']!=cid:
                mismatch.append(vid); continue
            published = (snippet.get('publishedAt') or '')[:10].replace('-','')
            row = by_id[vid]
            if row['title']!=title or row['title_source']!='api' or not row['published']:
                with s.tx():
                    s.db.execute("UPDATE videos SET title=?,title_source='api',published=COALESCE(published,?) WHERE channel_id=? AND id=?",(title,published or None,cid,vid))
                changed.append(vid)
    return {'channel_id':cid,'total':len(ids),'changed':len(changed),'missing':len(missing),'mismatch':len(mismatch),'changed_ids':changed[:80],'missing_ids':missing[:80],'mismatch_ids':mismatch[:80]}

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--db',default=str(default_db_path()))
    p.add_argument('--verbose',action='store_true',help='Return full records for troubleshooting')
    sub = p.add_subparsers(dest='cmd',required=True)
    a = sub.add_parser('status'); a.add_argument('--channel')
    sub.add_parser('daily-candidates', help='List due tracking channels without filtering pauses or leases')
    a = sub.add_parser('db-check', help='Check the canonical database without changing task state')
    a.add_argument('--backup', help='Also create a consistent SQLite online backup at this path')
    a = sub.add_parser('preflight'); a.add_argument('--channel', required=True)
    a = sub.add_parser('channel-config'); a.add_argument('channel')
    a.add_argument('--rename-strategy', choices=sorted(RENAME_STRATEGIES))
    a.add_argument('--title-format', choices=sorted(TITLE_FORMATS))
    a.add_argument('--membership', choices=sorted(MEMBERSHIP_STATUSES))
    a = sub.add_parser('pace'); a.add_argument('kind',choices=['access'],help='Claim a slot before one non-submission Gemini UI action'); a.add_argument('--run',required=True,help='Current run that owns the account coordination slot')
    a = sub.add_parser('metadata-backfill'); a.add_argument('operation',choices=['start','run','status','report','resume']); a.add_argument('job',nargs='?'); a.add_argument('--channel'); a.add_argument('--confirmed',action='store_true')
    a = sub.add_parser('add'); a.add_argument('url')
    a = sub.add_parser('notebook'); a.add_argument('channel'); a.add_argument('url'); a.add_argument('--replace-missing', action='store_true', help='Replace a mapping only after the old notebook was verified missing in Gemini'); a.add_argument('--replace-verified', action='store_true', help='Replace an existing mapping after the target account and Notebook were verified in the visible Gemini UI'); a.add_argument('--evidence', help='Required with --replace-verified: account-notebook-checked')
    a = sub.add_parser('scan'); a.add_argument('--channel'); a.add_argument('--full',action='store_true'); a.add_argument('--run')
    a = sub.add_parser('history-pipeline'); a.add_argument('--channel', required=True); a.add_argument('--full',action='store_true')
    a = sub.add_parser('begin'); a.add_argument('--mode',choices=['history','incremental'],required=True); a.add_argument('--scheduled',action='store_true'); a.add_argument('--channel'); a.add_argument('--policy',choices=['strict','collect-first'],default='strict'); a.add_argument('--run')
    a.add_argument('--archive',choices=['serial','extension'],default=None,help='History only: batch rename in the external extension')
    a = sub.add_parser('history-rename'); a.add_argument('operation',choices=['configure','enable','candidates','resume','serve','report','review']); a.add_argument('run',nargs='?'); a.add_argument('--auto',action='store_true',help='Deprecated: requires explicit selection'); a.add_argument('--channel'); a.add_argument('--account'); a.add_argument('--port',type=int,default=8765); a.add_argument('--include-verified',action='store_true'); a.add_argument('--video'); a.add_argument('--reason')
    a = sub.add_parser('next'); a.add_argument('run'); a.add_argument('--parallel',action='store_true')
    for name in ('prepare','park','end','renew','finalize','cleanup','handoff'):
        a = sub.add_parser(name); a.add_argument('run')
    a = sub.add_parser('metadata'); a.add_argument('run'); a.add_argument('channel'); a.add_argument('video'); a.add_argument('--force',action='store_true')
    a = sub.add_parser('title-refresh'); a.add_argument('channel'); a.add_argument('--limit',type=int); a.add_argument('--states',help='Comma separated states to correct, default pending,deferred')
    a = sub.add_parser('requeue-manual-review'); a.add_argument('channel'); a.add_argument('--reasons',default='generation_timeout,content_failed,chat_unidentified')
    a = sub.add_parser('step'); a.add_argument('run'); a.add_argument('channel'); a.add_argument('video'); a.add_argument('action',choices=['arm','retry','attach','summary','renamed','verify','cleanup-invalid','retitle','require-reopen','uncertain','defer','skip']); a.add_argument('--value'); a.add_argument('--evidence'); a.add_argument('--next',dest='advance',action='store_true')
    a = sub.add_parser('report'); a.add_argument('--mode',choices=['history','incremental'],default='history'); a.add_argument('--channel')
    a = sub.add_parser('gemini-limit-report'); a.add_argument('--channel')
    a = sub.add_parser('pause'); a.add_argument('reason',choices=sorted(REASONS)); a.add_argument('--channel')
    a.add_argument('--limit-type',choices=['temporary_rate_limit','usage_exhausted','high_traffic','captcha','login_required','robot_suspected','service_error','unknown'])
    a.add_argument('--reset-at',help='Visible Gemini reset time in ISO-8601 including timezone')
    a = sub.add_parser('resume'); a.add_argument('--confirmed',action='store_true',required=True); a.add_argument('--channel')
    a = sub.add_parser('adopt-unknown'); a.add_argument('--channel',required=True); a.add_argument('--evidence',required=True); a.add_argument('--run',help='Current channel run holding the lease')
    args = p.parse_args(argv)
    s = None
    try:
        if not Path(args.db).is_file() and args.cmd!='add':
            raise Stop('Task database is missing; refusing to create an empty replacement')
        if args.cmd=='db-check':
            s = Store(args.db)
            result={'database': str(Path(args.db)), 'integrity':'ok', 'application_id':DB_APPLICATION_ID}
            if args.backup:
                result.update(database_backup(args.db,args.backup))
        else:
            s = Store(args.db)
        if args.cmd=='status': result=s.status(verbose=args.verbose,channel_id=args.channel)
        elif args.cmd=='daily-candidates': result=s.daily_candidates()
        elif args.cmd=='preflight': result=preflight(s,args.channel)
        elif args.cmd=='channel-config':
            if not any((args.rename_strategy, args.title_format, args.membership)):
                result=s.channel_config(args.channel)
            else:
                result=dict(s.configure_channel(
                    args.channel,
                    rename_strategy=args.rename_strategy,
                    title_format=args.title_format,
                    membership_status=args.membership,
                ))
        elif args.cmd=='pace':
            s.check_lease(args.run)
            result=s.browser_access(args.kind,owner=args.run)
        elif args.cmd=='metadata-backfill':
            import backfill
            if args.operation=='start':
                if not args.channel: raise Stop('--channel is required')
                result=backfill.start(s,args.channel)
            elif args.operation=='resume':
                if not args.confirmed: raise Stop('Explicit recovery confirmation required')
                with s.tx():
                    access=s.db.execute('SELECT blocked,blocked_until FROM metadata_access WHERE id=1').fetchone()
                    if access and access['blocked']=='quota_exhausted' and access['blocked_until']>time.time():
                        raise Stop('YouTube metadata quota has not reset yet')
                    s.db.execute('UPDATE metadata_access SET blocked=NULL,blocked_until=0,next_at=0 WHERE id=1')
                    if args.job:
                        s.db.execute("UPDATE metadata_jobs SET status='ready' WHERE id=? AND status='review'",(args.job,))
                result={'metadata_resumed':True,'job':args.job}
            else:
                if not args.job: raise Stop('Job ID is required')
                result=backfill.step(s,args.job) if args.operation=='run' else backfill.report(s,args.job)
        elif args.cmd=='report': result=s.report(args.mode,args.channel)
        elif args.cmd=='gemini-limit-report': result=s.gemini_limit_report(args.channel)
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
                # Registering a verified Notebook is the explicit opt-in for a
                # new channel.  Tracking is enabled here, before the history
                # scan, so scheduled incremental can run independently while
                # the historical queue is still unfinished.
                s.db.execute('UPDATE channels SET notebook=?, tracking=1 WHERE id=?',(args.url,args.channel))
            result=dict(s.channel(args.channel))
        elif args.cmd=='scan':
            ids = [args.channel] if args.channel else [x[0] for x in s.db.execute('SELECT id FROM channels')]
            for cid in ids:
                require_channel_config(s, cid)
            result=[scan(s,c,args.full or now().weekday()==0,run=args.run) for c in ids]
        elif args.cmd=='history-pipeline':
            result=history_pipeline(s,args.channel,full=True)
        elif args.cmd=='begin':
            if args.channel:
                require_processing_setup(s, args.channel)
            if args.archive=='extension' and (args.mode!='history' or not args.channel):
                raise Stop('Extension archive requires channel-scoped history')
            if args.archive and args.run:
                existing=s.db.execute('SELECT archive_mode FROM runs WHERE id=?',(args.run,)).fetchone()
                if not existing or existing['archive_mode']!=args.archive:
                    raise Stop('Use history-rename enable to switch an existing history run')
            if args.archive=='extension':
                import history_rename
                if args.scheduled:
                    raise Stop('Extension history runs cannot be scheduled incrementals')
                result=history_rename.begin_extension(s,args.channel,args.policy,args.run)
            else:
                result=s.begin(args.mode,args.scheduled,args.channel,args.policy,args.run)
                if args.archive:
                    result=dict(s.db.execute('SELECT * FROM runs WHERE id=?',(result['id'],)).fetchone())
        elif args.cmd=='history-rename':
            import history_rename
            result=history_rename.dispatch(s,args)
        elif args.cmd=='next': result=s.reserve(args.run,args.parallel)
        elif args.cmd=='prepare': result=prepare(s,args.run)
        elif args.cmd=='finalize': result=s.finalize(args.run)
        elif args.cmd=='cleanup': result=s.cleanup(args.run)
        elif args.cmd=='handoff': result=s.handoff(args.run)
        elif args.cmd=='adopt-unknown': result=s.adopt_unknown(args.channel,args.evidence,args.run)
        elif args.cmd=='park': s.park(args.run); result={'parked':True}
        elif args.cmd=='end': s.end(args.run); result={'ended':True}
        elif args.cmd=='renew':
            with s.tx(): s.check_lease(args.run)
            result={'renewed':True}
        elif args.cmd=='metadata': result=metadata(s,args.run,args.channel,args.video,force=args.force)
        elif args.cmd=='title-refresh': result=title_refresh(s,args.channel,args.limit,args.states)
        elif args.cmd=='requeue-manual-review':
            result=s.requeue_manual_review(args.channel,[x.strip() for x in args.reasons.split(',') if x.strip()])
        elif args.cmd=='step':
            if args.advance:
                result=advance(s,args.run,args.channel,args.video,args.action,args.value,args.evidence)
            else:
                result=s.transition(args.run,args.channel,args.video,args.action,args.value,args.evidence)
        elif args.cmd=='pause':
            if args.reason in GLOBAL_PAUSES and args.channel: raise Stop('Global pause reasons cannot use --channel')
            if args.reason not in GLOBAL_PAUSES and not args.channel: raise Stop('Channel pause reasons require --channel')
            reset_at=None
            if args.reset_at:
                try:
                    parsed=datetime.fromisoformat(args.reset_at)
                    if parsed.tzinfo is None: raise ValueError('timezone required')
                    reset_at=parsed.timestamp()
                except ValueError: raise Stop('--reset-at must be ISO-8601 with an explicit timezone')
            if args.limit_type and args.reason not in {'quota','captcha','network','login'}:
                raise Stop('--limit-type requires quota, captcha, network, or login pause')
            if args.reset_at and args.reason!='quota': raise Stop('--reset-at is only valid for quota pause')
            with s.tx(): event=s.set_pause(args.reason,args.channel,args.limit_type,reset_at)
            result={'paused':args.reason,'channel_id':args.channel}
            if event:
                result['gemini_limit_event']=event
                result['gemini_limit_report']=s.gemini_limit_report(args.channel)
        elif args.cmd=='resume':
            # Must only be called after human approval; never by a timer.
            with s.tx():
                if not args.channel:
                    pause=s.db.execute("SELECT reason FROM pauses WHERE scope='*'").fetchone()
                    if pause and pause['reason']=='quota':
                        reset=s.db.execute("SELECT reset_at FROM gemini_limit_events WHERE reason='quota' AND reset_at IS NOT NULL ORDER BY detected DESC LIMIT 1").fetchone()
                        if reset and reset['reset_at']>time.time():
                            raise Stop('Gemini quota reset time has not arrived; keep the global pause until the displayed time')
                s.clear_pause(args.channel)
                s.db.execute("INSERT OR IGNORE INTO browser_pacing(scope) VALUES('gemini-account')")
                s.db.execute("UPDATE browser_pacing SET recovery_remaining=3 WHERE scope='gemini-account'")
                if args.channel:
                    s.db.execute('UPDATE lease SET expires=0 WHERE scope IN (?,?)',(args.channel,'*'))
                else:
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
    # Helpers must share these exact exception classes with the CLI entrypoint.
    sys.modules.setdefault('ytg',sys.modules[__name__])
    sys.stdout.reconfigure(encoding='utf-8')
    sys.exit(main())
