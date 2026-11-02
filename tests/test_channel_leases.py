import concurrent.futures
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import ytg


class ChannelLeaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'tasks.sqlite3'
        self.s = ytg.Store(self.path)
        for cid in ('a', 'b'):
            self.s.db.execute('INSERT INTO channels(id,url,name,initialized,tracking) VALUES(?,?,?,1,1)',
                              (cid, 'https://www.youtube.com/@'+cid, cid))

    def tearDown(self):
        self.s.db.close()
        self.tmp.cleanup()

    def test_parallel_connections_and_same_channel_exclusion(self):
        barrier = threading.Barrier(2)

        def begin(cid):
            s = ytg.Store(self.path)
            try:
                barrier.wait(timeout=10)
                return s.begin('history', channel_id=cid)['id']
            finally:
                s.db.close()

        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            runs = list(pool.map(begin, ('a', 'b')))
        for run in runs:
            self.s.check_lease(run)
        with self.assertRaises(ytg.Stop):
            self.s.begin('history', channel_id='a')
        with self.assertRaises(ytg.Stop):
            self.s.begin('history')
        self.s.cleanup(runs[0])
        self.s.check_lease(runs[1])

    def test_global_lease_excludes_channel_workers(self):
        self.s.begin('history')
        with self.assertRaises(ytg.Stop):
            self.s.begin('history', channel_id='b')

    def test_expired_channel_recovery_does_not_touch_other_worker(self):
        a = self.s.begin('history', channel_id='a')['id']
        b = self.s.begin('history', channel_id='b')['id']
        self.s.db.execute('UPDATE lease SET expires=0 WHERE run_id=?', (a,))
        self.s.begin('history', channel_id='a')
        self.s.check_lease(b)
        with self.assertRaises(ytg.Stop):
            self.s.cleanup(a)

    def test_pauses_keep_scope_and_global_priority(self):
        a = self.s.begin('history', channel_id='a')['id']
        b = self.s.begin('history', channel_id='b')['id']
        self.s.set_pause('unknown_submit', 'a')
        with self.assertRaises(ytg.Stop):
            self.s.check_lease(a)
        self.s.check_lease(b)
        self.s.set_pause('quota')
        with self.assertRaises(ytg.Stop):
            self.s.check_lease(b)

    def test_legacy_migration_preserves_live_owner(self):
        run = self.s.begin('history', channel_id='a')['id']
        before = dict(self.s.db.execute('SELECT * FROM lease').fetchone())
        self.s.db.execute('DROP TABLE lease')
        self.s.db.execute('CREATE TABLE lease(singleton INTEGER PRIMARY KEY CHECK(singleton=1),run_id TEXT,expires REAL,last_seen REAL)')
        self.s.db.execute('INSERT INTO lease VALUES(1,?,?,?)',
                          (run, before['expires'], before['last_seen']))
        migrated = ytg.Store(self.path)
        try:
            self.assertEqual(dict(migrated.db.execute('SELECT * FROM lease').fetchone()), before)
            migrated.begin('history', channel_id='b')
            migrated.check_lease(run)
        finally:
            migrated.db.close()

    def test_scheduled_rounds_are_per_channel(self):
        with patch.object(ytg, 'now', return_value=ytg.datetime(2026, 10, 30, 12)):
            a = self.s.begin('incremental', scheduled=True, channel_id='a')['id']
            b = self.s.begin('incremental', scheduled=True, channel_id='b')['id']
            ytg.scan(self.s,'a',fetch=lambda *a,**k: {'channel_id':'a','entries':[]},run=a)
            self.s.end(a)
            self.assertFalse(self.s.daily_due('a'))
            self.assertTrue(self.s.daily_due('b'))
            self.s.check_lease(b)

    def test_channel_resume_does_not_expire_other_channel(self):
        self.s.begin('history', channel_id='a')
        b = self.s.begin('history', channel_id='b')['id']
        self.s.set_pause('unknown_submit', 'a')
        result = ytg.main(['--db', str(self.path), 'resume', '--confirmed', '--channel', 'a'])
        self.assertEqual(result, 0)
        self.s.check_lease(b)

    def test_adopt_unknown_allows_own_lease_and_ignores_other_channel(self):
        self.s.insert('a', {'id': 'vid-a-00001', 'title': '待核视频', 'upload_date': '20261030'}, 'scan', True)
        self.s.db.execute("UPDATE videos SET state='uncertain', reason='unknown_submit' WHERE channel_id='a' AND id='vid-a-00001'")
        run = self.s.begin('history', channel_id='a', policy='collect-first')['id']
        self.s.begin('history', channel_id='b')
        result = self.s.adopt_unknown('a', 'notebook-identity-checked', run)
        self.assertEqual(result, {'adopted': 1})
