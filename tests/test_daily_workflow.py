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
        self.browser_gate=patch.object(self.s,'_claim_submit_slot',return_value=0)
        self.browser_gate.start()
        self.clock = patch('ytg.now', return_value=datetime(2026, 10, 30, 12))
        self.clock.start()
        self.s.db.execute('INSERT INTO channels(id,url,name,notebook,initialized,tracking) VALUES(?,?,?,?,1,1)',
                          (CID, 'https://www.youtube.com/@fixture', 'fixture', BOOK))

    def tearDown(self):
        self.browser_gate.stop()
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
        history = self.s.begin('history', channel_id=CID, policy='collect-first')['id']
        self.assertEqual(ytg.prepare(self.s, history)['stop'], 'daily_due')
        daily = self.s.handoff(history)['run']
        self.assertEqual(self.s.db.execute('SELECT policy FROM runs WHERE id=?', (daily,)).fetchone()[0], 'collect-first')
        ytg.scan(self.s, CID, fetch=self.fetch, run=daily)
        restored = self.s.handoff(daily)
        self.assertEqual(restored['policy'], 'collect-first')
        self.assertEqual(ytg.prepare(self.s, restored['run']), {'stop':'empty'})
        self.assertFalse(self.s.status(False, CID)['daily_due'])

    def test_due_incremental_does_not_preempt_remaining_history_tasks(self):
        self.add(1,True)
        history=self.s.begin('history',channel_id=CID,policy='collect-first')['id']
        self.assertEqual(ytg.prepare(self.s,history)['stop'], 'daily_due')
        with patch.object(self.s, 'daily_due', return_value=True):
            with self.assertRaisesRegex(ytg.Stop, 'history_pending'):
                self.s.handoff(history)

    def test_untracked_channel_is_not_daily_due(self):
        self.s.db.execute('UPDATE channels SET tracking=0 WHERE id=?', (CID,))
        self.assertFalse(self.s.daily_due(CID))
        self.assertFalse(self.s.status(False, CID)['daily_due'])

    def test_scheduled_incremental_allows_unfinished_history(self):
        self.add(1, history=True)
        run = self.s.begin('incremental', scheduled=True, channel_id=CID, policy='collect-first')['id']
        self.assertEqual(self.s.db.execute('SELECT mode FROM runs WHERE id=?', (run,)).fetchone()[0], 'incremental')

    def test_daily_candidates_include_paused_and_leased_channels(self):
        history = self.s.begin('history', channel_id=CID, policy='collect-first')
        self.s.set_pause('unknown_submit', CID)
        candidates = self.s.daily_candidates()
        self.assertEqual([row['id'] for row in candidates], [CID])
        self.assertEqual(self.s.db.execute('SELECT run_id FROM lease').fetchone()[0], history['id'])

    def test_scheduled_incremental_reserves_only_ids_discovered_by_its_scan(self):
        old = self.add(1)
        new = 'new00000001'
        run = self.s.begin('incremental', scheduled=True, channel_id=CID, policy='collect-first')['id']

        def fetch(url, *args, **kwargs):
            if url.endswith('/videos'):
                return {'channel_id': CID, 'entries': [
                    {'id': old, 'title': '旧积压', 'upload_date': '20261029'},
                    {'id': new, 'title': '新视频', 'upload_date': '20261030'},
                ]}
            return {'channel_id': CID, 'entries': []}

        ytg.scan(self.s, CID, fetch=fetch, run=run)
        item = ytg.prepare(self.s, run)
        self.assertEqual(item['id'], new)
        self.s.cleanup(run)

    def test_incremental_report_uses_current_scheduled_candidates(self):
        old = self.add(1)
        self.s.db.execute("UPDATE videos SET history=0 WHERE id=?", (old,))
        run = self.s.begin('incremental', scheduled=True, channel_id=CID, policy='collect-first')['id']
        new = 'new00000001'

        def fetch(url, *args, **kwargs):
            if url.endswith('/videos'):
                return {'channel_id': CID, 'entries': [
                    {'id': old, 'title': '旧积压', 'upload_date': '20261029'},
                    {'id': new, 'title': '新视频', 'upload_date': '20261030'},
                ]}
            return {'channel_id': CID, 'entries': []}

        ytg.scan(self.s, CID, fetch=fetch, run=run)
        report = self.s.report('incremental', CID)
        self.assertEqual(report['scope'], 'scheduled-day')
        self.assertEqual(report['remaining'], 1)
        self.assertEqual(report['counts']['pending'], 1)
        self.assertEqual(report['legacy_backlog'], 2)
        self.assertEqual(report['channels'][0]['channel_id'], CID)
        self.assertEqual(report['channels'][0]['candidates'][0]['original_title'], '新视频')
        self.s.cleanup(run)

    def test_scheduled_collect_first_incremental_ignores_old_unknown_pause(self):
        self.s.set_pause('unknown_submit', CID)
        run = self.s.begin('incremental', scheduled=True, channel_id=CID, policy='collect-first')['id']
        ytg.scan(self.s, CID, fetch=self.fetch, run=run)
        self.assertEqual(self.s.pause_for(CID), 'unknown_submit')
        self.s.cleanup(run)

    def test_history_handoff_is_allowed_only_after_history_completion(self):
        history = self.s.begin('history', channel_id=CID, policy='collect-first')['id']
        with patch.object(self.s, 'daily_due', return_value=True):
            daily = self.s.handoff(history)['run']
        self.assertEqual(self.s.db.execute('SELECT mode FROM runs WHERE id=?', (daily,)).fetchone()[0], 'incremental')

    def test_history_report_enables_tracking_only_after_completion(self):
        self.s.db.execute('UPDATE channels SET tracking=0 WHERE id=?', (CID,))
        vid = self.add(1, history=True)
        self.assertFalse(self.s.history_complete(CID))
        self.assertFalse(self.s.report('history', CID)['complete'])
        self.assertEqual(self.s.db.execute('SELECT tracking FROM channels WHERE id=?', (CID,)).fetchone()[0], 0)
        self.s.db.execute("UPDATE videos SET state='verified', notebook_summary_checked=1 WHERE id=?", (vid,))
        self.assertTrue(self.s.report('history', CID)['complete'])
        self.assertEqual(self.s.db.execute('SELECT tracking FROM channels WHERE id=?', (CID,)).fetchone()[0], 1)

    def test_aggregate_daily_due_ignores_untracked_channels(self):
        self.s.db.execute('UPDATE channels SET tracking=0 WHERE id=?', (CID,))
        other = 'UC8UCbiPrm2zN9nZHKdTevZA'
        self.s.db.execute('INSERT INTO channels(id,url,name,initialized,tracking) VALUES(?,?,?,1,0)',
                          (other, 'https://www.youtube.com/@other', 'other'))
        self.assertFalse(self.s.daily_due())

    def test_scheduled_end_requires_successful_scan_and_settled_tasks(self):
        daily = self.s.begin('incremental', scheduled=True, channel_id=CID)['id']
        with self.assertRaises(ytg.Stop):
            self.s.end(daily)
        ytg.scan(self.s, CID, fetch=self.fetch, run=daily)
        self.add(2)
        self.s.end(daily)
        self.assertFalse(self.s.daily_due(CID))

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
        self.assertFalse(self.s.pause_for(CID))
        self.assertEqual(self.s.video(CID, v['id'])['state'], 'deferred')
        self.assertEqual(self.s.video(CID, v['id'])['reason'], 'submission_unconfirmed')
        self.assertNotEqual(self.s.reserve(run)['id'], v['id'])

    def test_earlier_same_day_scan_does_not_skip_new_daily_round(self):
        ytg.scan(self.s, CID, fetch=self.fetch)
        run = self.s.begin('incremental', scheduled=True, channel_id=CID)['id']
        with patch.object(ytg, 'extract') as fetch:
            fetch.return_value = self.fetch()
            result = ytg.scan(self.s, CID, fetch=fetch, run=run)
            self.assertEqual(fetch.call_count, 2)
            self.assertNotIn('skipped', result)

    def test_recent_window_expands_when_date_boundary_is_not_reached(self):
        boundary = {'id': 'aaaaaaaaaaa', 'title': '边界视频', 'upload_date': '20261030'}
        first = lambda url, *a, **k: {
            'channel_id': CID,
            'entries': [boundary] if url.endswith('/videos') else [],
        }
        ytg.scan(self.s, CID, fetch=first)
        calls = []
        new = {'id': 'bbbbbbbbbbb', 'title': '新视频', 'upload_date': '20261031'}

        def second(url, *args, **kwargs):
            calls.append((url, kwargs.get('limit')))
            if url.endswith('/videos') and kwargs.get('limit') == ytg.SCAN_WINDOW:
                return {'channel_id': CID, 'entries': [new]}
            if url.endswith('/videos'):
                return {'channel_id': CID, 'entries': [new, boundary]}
            return {'channel_id': CID, 'entries': []}

        with patch('ytg.now', return_value=datetime(2026, 10, 31, 12)):
            result = ytg.scan(self.s, CID, fetch=second)

        self.assertTrue(result['complete'])
        self.assertEqual(result['date_coverage'], 'mixed')
        self.assertEqual(result['tabs'][0]['scope'], 'full')
        self.assertIn(('https://www.youtube.com/@fixture/videos', None), calls)
        self.assertEqual(self.s.video(CID, new['id'])['history'], 0)

    def test_scan_result_separates_completion_from_date_coverage(self):
        result = ytg.scan(self.s, CID, fetch=self.fetch)
        self.assertTrue(result['complete'])
        self.assertEqual(result['date_coverage'], 'recent-window')
        self.assertEqual(result['excluded_tabs'], ['shorts'])

    def test_initial_partial_scan_is_frozen_and_committed_once(self):
        self.s.db.execute('UPDATE channels SET initialized=0')
        old = {'id': 'aaaaaaaaaaa', 'title': '旧视频', 'upload_date': '20261029'}
        new = {'id': 'bbbbbbbbbbb', 'title': '新视频', 'upload_date': '20261030'}
        def first(url, *a, **k):
            return {'channel_id': CID, 'entries': [old] if url.endswith('/videos') else []}
        self.assertTrue(ytg.scan(self.s, CID, fetch=first)['complete'])
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM videos').fetchone()[0], 1)
        calls = []
        def retry(url, *a, **k):
            calls.append(url)
            return {'channel_id': CID, 'entries': []}
        self.assertTrue(ytg.scan(self.s, CID, fetch=retry)['complete'])
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.s.video(CID, old['id'])['history'], 1)
        ytg.scan(self.s, CID, fetch=lambda url, *a, **k: {'channel_id': CID, 'entries': [new, old] if url.endswith('/videos') else []})
        self.assertEqual(self.s.video(CID, new['id'])['history'], 0)

    def test_scan_excludes_shorts_from_fetch_and_inventory(self):
        calls = []
        short = {'id': 'ccccccccccc', 'title': '不应入库'}
        def fetch(url, *a, **k):
            calls.append(url)
            return {'channel_id': CID, 'entries': [short] if url.endswith('/shorts') else []}
        result = ytg.scan(self.s, CID, fetch=fetch)
        self.assertTrue(result['complete'])
        self.assertFalse(any(url.endswith('/shorts') for url in calls))
        self.assertIsNone(self.s.db.execute('SELECT 1 FROM videos WHERE id=?', (short['id'],)).fetchone())

    def test_scan_excludes_explicit_members_only_entries_without_membership(self):
        self.s.configure_channel(CID, membership_status='no')
        member = {
            'id': 'membr000001', 'title': '会员视频',
            'upload_date': '20261029', 'availability': 'members_only',
        }

        def fetch(url, *args, **kwargs):
            return {'channel_id': CID, 'entries': [member] if url.endswith('/videos') else []}

        result = ytg.scan(self.s, CID, fetch=fetch)
        self.assertEqual(result['tabs'][0]['excluded_members'], 1)
        self.assertIsNone(self.s.db.execute(
            'SELECT 1 FROM videos WHERE channel_id=? AND id=?', (CID, member['id'])
        ).fetchone())

    def test_initial_inventory_preserves_release_date(self):
        self.s.db.execute('UPDATE channels SET initialized=0')
        entry = {'id': 'relse000001', 'title': '有发布日期', 'release_date': '20261028'}

        def fetch(url, *args, **kwargs):
            return {'channel_id': CID, 'entries': [entry] if url.endswith('/videos') else []}

        ytg.scan(self.s, CID, fetch=fetch)
        self.assertEqual(self.s.video(CID, entry['id'])['published'], '20261028')

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
