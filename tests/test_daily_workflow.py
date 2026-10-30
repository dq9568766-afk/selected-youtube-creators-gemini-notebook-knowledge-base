import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import ytg

CID = 'UCX8KQ5xQlm0MnZkmHO7CBDw'
BOOK = 'https://gemini.google.com/notebook/11111111-1111-1111-1111-111111111111'


class DailyWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = ytg.Store(Path(self.tmp.name) / 'tasks.sqlite3')
        self.clock = patch('ytg.now', return_value=datetime(2026, 10, 30, 12))
        self.clock.start()
        self.s.db.execute('INSERT INTO channels(id,url,name,notebook,initialized) VALUES(?,?,?,?,1)',
                          (CID, 'https://www.youtube.com/@fixture', 'fixture', BOOK))

    def tearDown(self):
        self.clock.stop()
        self.s.db.close()
        self.tmp.cleanup()

    def fetch(self, *args, **kwargs):
        return {'channel_id': CID, 'entries': []}

    def add(self, i, history=False):
        vid = f'{i:011d}'
        self.s.insert(CID, {'id': vid, 'title': '标题', 'upload_date': '20261029'}, 'scan', history)
        self.s.db.execute('UPDATE videos SET metadata_checked=? WHERE id=?', (ytg.time.time(), vid))
        return vid

    def test_handoff_scans_then_returns_to_history_with_policy(self):
        self.add(1, True)
        history = self.s.begin('history', channel_id=CID, policy='collect-first')['id']
        self.assertEqual(ytg.prepare(self.s, history)['stop'], 'daily_due')
        daily = self.s.handoff(history)['run']
        self.assertEqual(self.s.db.execute('SELECT policy FROM runs WHERE id=?', (daily,)).fetchone()[0], 'collect-first')
        ytg.scan(self.s, CID, fetch=lambda *a, **k: {'channel_id': CID, 'entries':[{'id':f'{1:011d}', 'title':'标题'}]}, run=daily)
        restored = self.s.handoff(daily)
        self.assertEqual(restored['policy'], 'collect-first')
        self.assertEqual(ytg.prepare(self.s, restored['run'])['id'], f'{1:011d}')
        self.assertFalse(self.s.status(False, CID)['daily_due'])

    def test_scheduled_end_requires_successful_scan_and_settled_tasks(self):
        daily = self.s.begin('incremental', scheduled=True, channel_id=CID)['id']
        with self.assertRaises(ytg.Stop):
            self.s.end(daily)
        ytg.scan(self.s, CID, fetch=self.fetch, run=daily)
        self.add(2)
        with self.assertRaises(ytg.Stop):
            self.s.end(daily)
        self.assertTrue(self.s.daily_due(CID))

    def test_daily_budget_survives_new_manual_run_and_resets_next_day(self):
        for i in range(21):
            self.add(i)
        for i in range(20):
            run = self.s.begin('incremental', channel_id=CID, policy='collect-first')['id']
            v = self.s.reserve(run)
            self.s.transition(run, CID, v['id'], 'arm')
            self.s.transition(run, CID, v['id'], 'defer', 'content_failed')
            self.s.end(run)
        # Bypass pacing only in the test to isolate the independent daily cap.
        self.s.db.execute('UPDATE pacing SET processed=0,break_started=NULL')
        run = self.s.begin('incremental', channel_id=CID)['id']
        self.assertEqual(self.s.reserve(run)['stop'], 'daily_cap')
        self.s.end(run)
        with patch('ytg.now', return_value=datetime(2026, 10, 31, 12)):
            run = self.s.begin('incremental', channel_id=CID)['id']
            self.assertEqual(self.s.reserve(run)['state'], 'reserved')

    def test_incremental_single_failure_does_not_pause(self):
        first = self.add(1)
        self.add(2)
        run = self.s.begin('incremental', channel_id=CID, policy='collect-first')['id']
        v = self.s.reserve(run)
        self.s.transition(run, CID, v['id'], 'arm')
        self.s.transition(run, CID, v['id'], 'uncertain')
        self.s.transition(run, CID, v['id'], 'defer', 'submission_unconfirmed')
        self.assertFalse(self.s.pause_for(CID))
        self.assertNotEqual(self.s.reserve(run)['id'], v['id'])

    def test_earlier_same_day_scan_does_not_skip_new_daily_round(self):
        ytg.scan(self.s, CID, fetch=self.fetch)
        run = self.s.begin('incremental', scheduled=True, channel_id=CID)['id']
        with patch.object(ytg, 'extract') as fetch:
            fetch.return_value = self.fetch()
            result = ytg.scan(self.s, CID, fetch=fetch, run=run)
            self.assertEqual(fetch.call_count, 3)
            self.assertNotIn('skipped', result)

    def test_initial_partial_scan_is_frozen_and_committed_once(self):
        self.s.db.execute('UPDATE channels SET initialized=0')
        old = {'id': 'aaaaaaaaaaa', 'title': '旧视频', 'upload_date': '20261029'}
        new = {'id': 'bbbbbbbbbbb', 'title': '新视频', 'upload_date': '20261030'}
        def first(url, *a, **k):
            if url.endswith('/shorts'):
                raise RuntimeError('network')
            return {'channel_id': CID, 'entries': [old] if url.endswith('/videos') else []}
        self.assertFalse(ytg.scan(self.s, CID, fetch=first)['complete'])
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM videos').fetchone()[0], 0)
        calls = []
        def retry(url, *a, **k):
            calls.append(url)
            return {'channel_id': CID, 'entries': []}
        self.assertTrue(ytg.scan(self.s, CID, fetch=retry)['complete'])
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.s.video(CID, old['id'])['history'], 1)
        ytg.scan(self.s, CID, fetch=lambda url, *a, **k: {'channel_id': CID, 'entries': [new, old] if url.endswith('/videos') else []})
        self.assertEqual(self.s.video(CID, new['id'])['history'], 0)

    def test_missing_inventory_is_explicit_review(self):
        vid = self.add(1, True)
        ytg.scan(self.s, CID, full=True, fetch=self.fetch)
        report = self.s.report(channel_id=CID)
        self.assertEqual(report['manual_review'][0]['reason'], 'source_missing')
        self.assertEqual(report['not_generated'], 1)
        self.assertEqual(self.s.video(CID, vid)['state'], 'deferred')

    def test_unclassified_missing_pending_prevents_complete(self):
        self.add(1, True)
        self.s.db.execute('UPDATE videos SET missing=1')
        self.assertFalse(self.s.report(channel_id=CID)['complete'])

    def test_scan_cannot_write_other_channel_under_scoped_lease(self):
        self.s.db.execute('INSERT INTO channels(id,url,name) VALUES(?,?,?)', ('other', 'https://www.youtube.com/@other', 'other'))
        run = self.s.begin('incremental', channel_id=CID)['id']
        with self.assertRaises(ytg.Stop):
            ytg.scan(self.s, 'other', fetch=self.fetch, run=run)

    def test_failed_forced_rescan_invalidates_success_for_end(self):
        run = self.s.begin('incremental', scheduled=True, channel_id=CID)['id']
        ytg.scan(self.s, CID, fetch=self.fetch, run=run)
        # Require a full scan, then fail it; a prior recent scan cannot mask it.
        self.s.db.execute('UPDATE channels SET last_full=NULL')
        def broken(*a, **k):
            raise RuntimeError('network')
        self.assertFalse(ytg.scan(self.s, CID, full=True, fetch=broken, run=run)['complete'])
        with self.assertRaises(ytg.Stop):
            self.s.end(run)
