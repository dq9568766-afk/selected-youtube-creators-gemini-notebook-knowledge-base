"""Local-only bridge for history title maintenance. Never calls Google APIs."""
from __future__ import annotations

import json
import secrets
import sqlite3
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path

from ytg import Stop, gemini_url, title_for

MAX_EXTENSION_RECOVERY_ATTEMPTS = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS history_rename_config(
 channel_id TEXT PRIMARY KEY, account TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS history_rename_items(
 channel_id TEXT NOT NULL, video_id TEXT NOT NULL,
 chat_status TEXT NOT NULL DEFAULT 'pending',
 old_chat_title TEXT, reason TEXT, updated REAL,
 recovery_attempts INTEGER NOT NULL DEFAULT 0,
 verification_phase TEXT,
 verification_mode TEXT NOT NULL DEFAULT 'extension-reopen-v1',
 PRIMARY KEY(channel_id,video_id));
CREATE TABLE IF NOT EXISTS history_rename_sessions(
 run_id TEXT PRIMARY KEY, expires REAL NOT NULL, session_key TEXT);
"""


def config(s, cid):
    s.channel(cid)
    row = s.db.execute('SELECT * FROM history_rename_config WHERE channel_id=?', (cid,)).fetchone()
    if not row:
        raise Stop('Configure the visibly checked Google account first')
    return row


def configure(s, cid, account):
    import re
    if not account or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', account):
        raise Stop('An exact Google account email is required')
    with s.tx():
        s.channel(cid)
        if any(x['expires'] > time.time() for x in s.conflicting_leases(cid)):
            raise Stop('Finish the active channel worker before changing the rename mapping')
        columns={row['name'] for row in s.db.execute('PRAGMA table_info(history_rename_config)')}
        existing = s.db.execute('SELECT * FROM history_rename_config WHERE channel_id=?', (cid,)).fetchone()
        if existing and existing['account'] != account:
            raise Stop('Existing rename mapping differs; reconcile it explicitly before replacing')
        if 'notebooklm' in columns:
            s.db.execute('INSERT OR IGNORE INTO history_rename_config(channel_id,account,notebooklm) VALUES(?,?,?)', (cid, account, ''))
        else:
            s.db.execute('INSERT OR IGNORE INTO history_rename_config(channel_id,account) VALUES(?,?)', (cid, account))
    return {'configured': True, 'channel_id': cid}


def preflight(s, cid):
    config(s,cid)
    if s.db.execute("SELECT 1 FROM videos WHERE channel_id=? AND state IN ('submitting','submitted','uncertain') LIMIT 1",(cid,)).fetchone():
        raise Stop('Resolve the current submission before enabling batch titles')


def enable(s, run):
    with s.tx():
        s.check_lease(run)
        r = s.db.execute('SELECT * FROM runs WHERE id=?', (run,)).fetchone()
        if r['mode'] != 'history' or not r['channel_id']:
            raise Stop('Extension archive requires channel-scoped history')
        preflight(s,r['channel_id'])
        s.db.execute("UPDATE runs SET archive_mode='extension' WHERE id=?", (run,))
        s.db.execute("INSERT OR IGNORE INTO history_rename_items(channel_id,video_id) SELECT channel_id,id FROM videos WHERE channel_id=? AND history=1 AND state IN ('summarized','renamed')", (r['channel_id'],))
    return {'enabled': True, 'run': run}


def begin_extension(s, cid, policy='strict', run_id=None):
    preflight(s,cid)
    if run_id:
        existing=s.db.execute('SELECT mode,channel_id,archive_mode,finished FROM runs WHERE id=?',(run_id,)).fetchone()
        if not existing or existing['finished'] or existing['mode']!='history' or existing['channel_id']!=cid:
            raise Stop('Cannot reopen a finished, missing, or differently scoped history run')
        if existing['archive_mode']!='extension':
            raise Stop('Use history-rename enable to switch an existing history run')
    run=s.begin('history',channel_id=cid,policy=policy,run_id=run_id)
    try:
        enable(s,run['id'])
    except Exception:
        s.cleanup(run['id'])
        raise
    return dict(s.db.execute('SELECT * FROM runs WHERE id=?',(run['id'],)).fetchone())


def pending_report(s, cid):
    config(s, cid)
    rows = list(s.db.execute('SELECT video_id,chat_status,reason,recovery_attempts,verification_phase,verification_mode FROM history_rename_items WHERE channel_id=? ORDER BY video_id', (cid,)))
    return {'channel_id': cid, 'total': len(rows),
            'pending': sum(r['chat_status']=='pending' for r in rows),
            'chat_verified': sum(r['chat_status']=='verified' for r in rows),
            'manual_review': [dict(r) for r in rows if r['reason'] or r['chat_status']=='review']}


class Bridge:
    def __init__(self, s, run, include_verified=False, video_id=None):
        self.s, self.run = s, run
        self.key = secrets.token_urlsafe(32)
        self.current = None
        with s.tx():
            s.check_lease(run)
            r = s.db.execute('SELECT * FROM runs WHERE id=?', (run,)).fetchone()
            if r['mode'] != 'history' or r['archive_mode'] != 'extension' or not r['channel_id']:
                raise Stop('Enable the external extension for this channel history run first')
            self.cid = r['channel_id']
            self.video_id = video_id
            self.cfg = dict(config(s, self.cid))
            if s.pause_for(self.cid):
                raise Stop('Keep the existing pause; this bridge does not resume work')
            if s.db.execute("SELECT 1 FROM videos WHERE channel_id=? AND state IN ('reserved','submitting','submitted','uncertain')", (self.cid,)).fetchone():
                raise Stop('Settle the active video before switching browsers')
            if include_verified:
                if video_id:
                    row=s.db.execute("SELECT id FROM videos WHERE channel_id=? AND id=? AND history=1 AND state='verified' AND topic IS NOT NULL AND chat IS NOT NULL",(self.cid,video_id)).fetchone()
                    if not row:
                        raise Stop('The selected test video is not a verified historical chat in this channel')
                    s.db.execute("INSERT OR IGNORE INTO history_rename_items(channel_id,video_id) VALUES(?,?)",(self.cid,video_id))
                    s.db.execute("UPDATE history_rename_items SET chat_status='pending',reason='include_verified',updated=? WHERE channel_id=? AND video_id=? AND chat_status='verified'",(time.time(),self.cid,video_id))
                else:
                    s.db.execute("INSERT OR IGNORE INTO history_rename_items(channel_id,video_id) SELECT channel_id,id FROM videos WHERE channel_id=? AND history=1 AND state='verified' AND topic IS NOT NULL AND chat IS NOT NULL", (self.cid,))
                    s.db.execute("UPDATE history_rename_items SET chat_status='pending',reason='include_verified',updated=? WHERE channel_id=? AND chat_status='verified'",(time.time(),self.cid))
            elif video_id:
                raise Stop('--video requires --include-verified for a one-item history check')
            if s.db.execute('SELECT 1 FROM history_rename_sessions WHERE run_id=? AND expires>?', (run,time.time())).fetchone():
                raise Stop('A rename bridge is already active for this run')
            s.db.execute('INSERT OR REPLACE INTO history_rename_sessions(run_id,expires,session_key) VALUES(?,?,?)', (run,time.time()+120,self.key))
        self.notebook = s.channel(self.cid)['notebook']

    def guard(self):
        with self.s.tx():
            session = self.s.db.execute('SELECT session_key,expires FROM history_rename_sessions WHERE run_id=?', (self.run,)).fetchone()
            if not session or session['expires'] <= time.time() or session['session_key'] != self.key:
                raise Stop('This rename bridge has been superseded; stop the old worker')
            self.s.check_lease(self.run)
            if self.s.pause_for(self.cid):
                raise Stop('Paused; stop the external worker')
            self.s.db.execute('UPDATE history_rename_sessions SET expires=? WHERE run_id=? AND session_key=?', (time.time()+120,self.run,self.key))

    def pace(self):
        self.guard()
        with self.s.tx():
            remaining=self.s._break_remaining(self.run)
        if remaining:
            return {'ready':False,'seconds_remaining':remaining,'kind':'break'}
        return self.s.browser_access(owner=self.run)

    def next(self):
        self.guard()
        row = self.s.db.execute("""SELECT v.*,h.chat_status,h.old_chat_title
            FROM videos v JOIN history_rename_items h ON h.channel_id=v.channel_id AND h.video_id=v.id
            WHERE v.channel_id=? AND v.history=1 AND h.chat_status='pending'
            AND (? IS NULL OR v.id=?) ORDER BY v.found LIMIT 1""", (self.cid,self.video_id,self.video_id)).fetchone()
        if not row:
            return {'done': True, 'report': pending_report(self.s,self.cid)}
        if row['state'] not in {'summarized','renamed','verified'} or not row['chat']:
            raise Stop('A batch item has no checked summary/chat; resolve it in the queue')
        gemini_url(row['chat'],'chat')
        self.current = {'video_id':row['id'], 'title':title_for(row), 'chat':row['chat'],
                        'original_title':row['title'], 'old_chat_title':row['old_chat_title'],
                        'chat_status':row['chat_status']}
        return {**self.current, 'account':self.cfg['account'], 'notebook':self.notebook,
                'channel_id':self.cid}

    def result(self, data):
        self.guard()
        job = self.current
        if not job or data.get('video_id') != job['video_id'] or data.get('title') != job['title']:
            raise Stop('Result does not match the current batch item')
        kind = data.get('kind')
        if kind != 'chat':
            raise Stop('Unknown rename target')
        if data.get('status') != 'verified':
            # Failures remain pending until explicitly placed in manual review.
            reason = data.get('reason')
            allowed = {'ui_changed','account_mismatch','notebook_mismatch','video_mismatch','summary_unconfirmed','save_unverified','interrupted'}
            if reason not in allowed:
                raise Stop('Unsupported batch failure')
            attempts = data.get('recovery_attempts', 0)
            phase = data.get('verification_phase')
            if not isinstance(attempts, int) or not 0 <= attempts <= MAX_EXTENSION_RECOVERY_ATTEMPTS:
                raise Stop('Invalid recovery attempt count')
            if phase is not None and (not isinstance(phase, str) or not 0 < len(phase) <= 128):
                raise Stop('Invalid verification phase')
            with self.s.tx():
                self.s.db.execute('UPDATE history_rename_items SET reason=?,recovery_attempts=?,verification_phase=?,updated=? WHERE channel_id=? AND video_id=?',(reason,attempts,phase,time.time(),self.cid,job['video_id']))
            return {'stopped':reason, 'pending':True}
        expected = self.notebook
        if data.get('account') != self.cfg['account'] or data.get('notebook') != expected:
            raise Stop('Result account/Notebook differs from registered mapping')
        if kind=='chat' and (data.get('chat') != job['chat'] or data.get('video_checked') is not True or data.get('summary_present') is not True or data.get('membership_checked') is not True):
            raise Stop('Chat result lacks reopened summary, video or membership evidence')
        attempts = data.get('recovery_attempts', 0)
        phase = data.get('verification_phase')
        mode = data.get('verification_mode', 'extension-reopen-v1')
        if not isinstance(attempts, int) or not 0 <= attempts <= MAX_EXTENSION_RECOVERY_ATTEMPTS:
            raise Stop('Invalid recovery attempt count')
        if not isinstance(phase, str) or not 0 < len(phase) <= 128:
            raise Stop('Missing verification phase')
        if mode not in {'extension-reopen-v1', 'extension-reopen-v2'}:
            raise Stop('Unknown extension verification mode')
        with self.s.tx():
            v = self.s.video(self.cid,job['video_id'])
            if v['history'] != 1 or v['state'] not in {'summarized','renamed','verified'}:
                raise Stop('Batch item was changed by another operation')
            if title_for(v)!=job['title'] or v['chat']!=job['chat']:
                raise Stop('Batch metadata changed; reload the item')
            old = data.get('old_chat_title')
            if not isinstance(old,str) or not old or len(old)>4096:
                raise Stop('Missing pre-rename chat title')
            self.s.db.execute("UPDATE history_rename_items SET chat_status='verified',old_chat_title=COALESCE(old_chat_title,?),reason=NULL,recovery_attempts=?,verification_phase=?,verification_mode=?,updated=? WHERE channel_id=? AND video_id=?", (old,attempts,phase,mode,time.time(),self.cid,job['video_id']))
            self.s.db.execute("UPDATE videos SET state='verified',owner=NULL,reason=NULL,reopen_required=NULL WHERE channel_id=? AND id=?", (self.cid,job['video_id']))
            self.s._count_processed(self.run,self.cid,job['video_id'])
            self.s.db.execute('UPDATE channels SET tracking=1 WHERE id=?',(self.cid,))
        return {'recorded':kind,'video_id':job['video_id']}

    def checkpoint(self, data):
        self.guard()
        old = data.get('old_chat_title')
        if not self.current or data.get('video_id')!=self.current['video_id'] or not isinstance(old,str) or not 0<len(old)<=4096:
            raise Stop('Invalid pre-rename checkpoint')
        with self.s.tx():
            self.s.db.execute('UPDATE history_rename_items SET old_chat_title=COALESCE(old_chat_title,?),updated=? WHERE channel_id=? AND video_id=?',(old,time.time(),self.cid,self.current['video_id']))
        return {'checkpointed':True}

    def close(self):
        with self.s.tx():
            deleted = self.s.db.execute('DELETE FROM history_rename_sessions WHERE run_id=? AND session_key=?',(self.run,self.key)).rowcount
        if deleted:
            self.s.cleanup(self.run)


def serve(s, run, port, include_verified=False, video_id=None):
    # The HTTP server is single-threaded through this lock/connection. Request
    # handling runs on the main thread; no browser credentials cross this bridge.
    from http.server import HTTPServer
    if not 1024 <= port <= 65535:
        raise Stop('Use a local port between 1024 and 65535')
    bridge = Bridge(s,run,include_verified,video_id)

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def log_message(self,*args):
            pass

        def origin(self):
            origin = self.headers.get('Origin','')
            return origin if origin.startswith('chrome-extension://') and '/' not in origin[19:] else None

        def respond(self, status, value):
            self.send_response(status)
            if self.origin():
                self.send_header('Access-Control-Allow-Origin', self.origin())
                self.send_header('Vary','Origin')
            self.send_header('Content-Type','application/json; charset=utf-8')
            self.send_header('Cache-Control','no-store')
            self.end_headers()
            self.wfile.write(json.dumps(value,ensure_ascii=False).encode())

        def do_OPTIONS(self):
            if not self.origin():
                return self.respond(403,{'stopped':'extension origin required'})
            self.send_response(204)
            self.send_header('Access-Control-Allow-Origin',self.origin())
            self.send_header('Access-Control-Allow-Methods','POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers','Content-Type, X-YTG-Pairing')
            self.end_headers()

        def do_POST(self):
            if self.headers.get('Host') != f'127.0.0.1:{port}' or not self.origin() or not secrets.compare_digest(self.headers.get('X-YTG-Pairing',''),bridge.key):
                return self.respond(403,{'stopped':'pairing required'})
            try:
                length = int(self.headers.get('Content-Length','0'))
                if not 0 < length <= 32768:
                    raise Stop('Invalid request size')
                data = json.loads(self.rfile.read(length))
                if not isinstance(data,dict):
                    raise Stop('Invalid request')
                if self.path=='/next':
                    value=bridge.next()
                elif self.path=='/heartbeat':
                    bridge.guard()
                    value={'alive':True}
                elif self.path=='/pace':
                    value=bridge.pace()
                elif self.path=='/result':
                    value=bridge.result(data)
                elif self.path=='/checkpoint':
                    value=bridge.checkpoint(data)
                elif self.path=='/release':
                    self.server.release_requested=True
                    value={'released':True}
                else:
                    raise Stop('Unknown local action')
                self.respond(200,value)
            except (Stop,ValueError,sqlite3.Error):
                self.respond(409,{'stopped':'Local queue gate rejected the action; check run, pause and item state'})

    try:
        with HTTPServer(('127.0.0.1',port),Handler) as server:
            # Local handoff contains only the ephemeral bridge pairing code.
            # Do not store it in the repository, browser logs or task database.
            path = Path(s.db.execute('PRAGMA database_list').fetchone()[2]).parent/f'history-rename-{run}.local.json'
            path.write_text(json.dumps({'bridge':f'http://127.0.0.1:{port}','pairing':bridge.key}),encoding='utf-8')
            print(json.dumps({'rename_service':'ready','pairing_file':str(path),'run':run},ensure_ascii=False),flush=True)
            server.timeout=1
            server.release_requested=False
            while not server.release_requested:
                server.handle_request()
    except KeyboardInterrupt:
        return {'stopped':'operator_stop','progress_preserved':True}
    finally:
        if 'path' in locals():
            path.unlink(missing_ok=True)
        bridge.close()


def auto_run(s):
    rows = list(s.db.execute(
        "SELECT r.id FROM runs r JOIN lease l ON l.run_id=r.id "
        "WHERE r.mode='history' AND r.archive_mode='extension' AND r.finished=0 AND l.expires>? "
        "ORDER BY r.started DESC", (time.time(),)
    ))
    if not rows:
        raise Stop('没有找到可启动的历史扩展任务；请先开始一个 extension 历史任务')
    if len(rows) != 1:
        raise Stop('找到多个可启动的历史扩展任务；请使用明确的 RUN 编号')
    return rows[0]['id']


def dispatch(s,args):
    if args.operation=='configure':
        if not args.channel:
            raise Stop('--channel is required')
        return configure(s,args.channel,args.account)
    if args.operation=='report':
        return pending_report(s,args.channel)
    if args.operation=='enable':
        return enable(s,args.run)
    if args.operation=='serve':
        run = auto_run(s) if args.auto else args.run
        if not run:
            raise Stop('请提供 RUN 编号，或使用 --auto')
        return serve(s,run,args.port,args.include_verified,args.video)
    if args.operation=='review':
        if not args.video or not args.reason or len(args.reason)>128:
            raise Stop('--video and a short --reason are required')
        with s.tx():
            s.check_lease(args.run)
            r=s.db.execute('SELECT * FROM runs WHERE id=?',(args.run,)).fetchone()
            if r['mode']!='history' or r['channel_id']!=args.channel:
                raise Stop('Review requires the owning history channel run')
            h=s.db.execute('SELECT * FROM history_rename_items WHERE channel_id=? AND video_id=?',(args.channel,args.video)).fetchone()
            if not h:
                raise Stop('Unknown history rename item')
            s.db.execute("UPDATE history_rename_items SET chat_status=CASE WHEN chat_status='pending' THEN 'review' ELSE chat_status END,reason=?,updated=? WHERE channel_id=? AND video_id=?",(args.reason,time.time(),args.channel,args.video))
            if h['chat_status']!='verified':
                s.db.execute("UPDATE videos SET state='deferred',reason='archive_unverified',owner=NULL WHERE channel_id=? AND id=? AND history=1 AND state IN ('summarized','renamed')",(args.channel,args.video))
                s._count_processed(args.run,args.channel,args.video)
        return {'manual_review':args.video}
    raise Stop('Unknown rename action')
