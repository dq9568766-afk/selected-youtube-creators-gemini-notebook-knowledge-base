import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import ytg

CID = 'UCX8KQ5xQlm0MnZkmHO7CBDw'
OTHER = 'UCYO_jab_esuFRV4b17AJtAw'
VID = 'aaaaaaaaaaa'
CHAT = 'https://gemini.google.com/app/aaaaaaaaaaaaaaaa'
BOOK = 'https://gemini.google.com/notebook/11111111-1111-1111-1111-111111111111'


class CollectFirstTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = ytg.Store(Path(self.tmp.name) / 'tasks.sqlite3')
        self.clock = patch('ytg.now', return_value=datetime(2026, 10, 27, 4, 0))
        self.clock.start()
        for cid, book in [(CID, BOOK), (OTHER, BOOK.replace('1', '2'))]:
            self.s.db.execute('INSERT INTO channels(id,url,name,notebook,initialized) VALUES(?,?,?,?,1)',
                              (cid, 'https://www.youtube.com/channel/' + cid, cid, book))
        self.add(VID)

    def tearDown(self):
        self.clock.stop()
        self.s.db.close()
        self.tmp.cleanup()

    def add(self, vid, cid=CID):
        self.s.insert(cid, {'id': vid, 'title': '原始标题', 'upload_date': '20261026'}, 'scan', True)

    def begin(self):
        run = self.s.begin('history', channel_id=CID, policy='collect-first')['id']
        self.s.reserve(run)
        return run

    def test_all_item_deferrals_allow_next_without_chat_or_retry(self):
        run = self.begin()
        for i, reason in enumerate(['generation_timeout', 'submission_unconfirmed', 'chat_unidentified',
                                    'content_failed', 'metadata_unavailable']):
            vid = VID if i == 0 else str(i) * 11
            if i:
                self.add(vid)
                self.s.reserve(run)
            self.s.transition(run, CID, vid, 'arm')
            self.s.transition(run, CID, vid, 'defer', reason)
            self.assertEqual(self.s.pause_for(CID), '')
            self.assertEqual(self.s.video(CID, vid)['retry_count'], 0)
        self.assertTrue(self.s.report(channel_id=CID)['complete'])
        self.assertEqual(len(self.s.report(channel_id=CID)['manual_review']), 5)

    def test_uncertain_is_item_level_and_never_resends(self):
        run = self.begin()
        self.s.transition(run, CID, VID, 'arm')
        self.s.transition(run, CID, VID, 'uncertain')
        self.assertEqual(self.s.pause_for(CID), '')
        with self.assertRaises(ytg.Stop):
            self.s.transition(run, CID, VID, 'retry', evidence='manual-review-regenerate')
        self.s.transition(run, CID, VID, 'defer', 'submission_unconfirmed')
        self.add('bbbbbbbbbbb')
        self.assertEqual(self.s.reserve(run)['id'], 'bbbbbbbbbbb')

    def test_archive_review_preserves_generated_evidence_and_totals(self):
        run = self.begin()
        self.s.transition(run, CID, VID, 'arm')
        self.s.transition(run, CID, VID, 'attach', CHAT, 'video-match')
        self.s.transition(run, CID, VID, 'summary', '主题', 'youtube-content-complete')
        self.assertFalse(self.s.report(channel_id=CID)['complete'])
        self.s.transition(run, CID, VID, 'defer', 'archive_unverified')
        report = self.s.report(channel_id=CID)
        self.assertEqual((report['generated'], report['verified'], report['not_generated']), (1, 0, 0))
        self.assertEqual(report['manual_review'][0]['chat'], CHAT)
        self.assertIn('expected_title', report['manual_review'][0])

    def test_cooldown_and_probe_stop_survive_cleanup_and_new_run(self):
        run = self.begin()
        for i in range(5):
            vid = VID if i == 0 else str(i) * 11
            if i:
                self.add(vid)
                self.s.reserve(run)
            self.s.transition(run, CID, vid, 'arm')
            self.s.transition(run, CID, vid, 'defer', 'generation_timeout')
        self.add('bbbbbbbbbbb')
        self.assertEqual(self.s.reserve(run)['stop'], 'health_cooldown')
        self.s.cleanup(run)
        run = self.s.begin('history', channel_id=CID, policy='collect-first')['id']
        self.assertEqual(self.s.reserve(run)['stop'], 'health_cooldown')
        self.s.db.execute('UPDATE runs SET health_after=? WHERE id=?', (time.time()-1, run))
        self.assertEqual(self.s.reserve(run)['id'], 'bbbbbbbbbbb')
        self.s.transition(run, CID, 'bbbbbbbbbbb', 'arm')
        self.s.transition(run, CID, 'bbbbbbbbbbb', 'defer', 'generation_timeout')
        self.assertEqual(self.s.pause_for(CID), 'source_unavailable')
        self.s.cleanup(run)
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM lease').fetchone()[0], 0)

    def test_probe_success_resets_failures(self):
        run = self.begin()
        self.s.db.execute('UPDATE runs SET health_probe=1,failure_streak=5 WHERE id=?', (run,))
        self.s.transition(run, CID, VID, 'arm')
        self.s.transition(run, CID, VID, 'attach', CHAT, 'video-match')
        self.s.transition(run, CID, VID, 'summary', '主题', 'youtube-content-complete')
        self.assertEqual(self.s.db.execute('SELECT failure_streak FROM runs WHERE id=?', (run,)).fetchone()[0], 0)

    def test_cleanup_keeps_pause_and_other_worker_state(self):
        run = self.begin()
        self.add('bbbbbbbbbbb', OTHER)
        self.s.db.execute("UPDATE videos SET state='reserved',owner='other' WHERE channel_id=?", (OTHER,))
        self.s.transition(run, CID, VID, 'arm')
        self.s.set('pause', 'captcha')
        self.s.cleanup(run)
        self.assertEqual(self.s.pause_for(CID), 'captcha')
        self.assertEqual(self.s.video(CID, VID)['state'], 'uncertain')
        self.assertEqual(self.s.video(OTHER, 'bbbbbbbbbbb')['owner'], 'other')
        with self.assertRaises(ytg.Stop):
            self.s.begin('history', channel_id=CID, policy='collect-first')

    def test_scope_live_lease_and_scoped_migration(self):
        run = self.begin()
        self.add('bbbbbbbbbbb', OTHER)
        with self.assertRaises(ytg.Stop):
            self.s.transition(run, OTHER, 'bbbbbbbbbbb', 'defer', 'metadata_unavailable')
        with self.assertRaises(ytg.Stop):
            self.s.adopt_unknown(CID, 'notebook-identity-checked')
        with self.assertRaises(ytg.Stop):
            self.s.cleanup('other')
        self.s.transition(run, CID, VID, 'arm')
        self.s.set('pause', 'unknown_submit')
        self.s.cleanup(run)
        self.s.db.execute("UPDATE videos SET state='uncertain',reason='unknown_submit' WHERE channel_id=?", (OTHER,))
        self.assertEqual(self.s.adopt_unknown(CID, 'notebook-identity-checked')['adopted'], 1)
        self.assertEqual(self.s.pause_for(CID), 'unknown_submit')
        self.assertEqual(self.s.video(OTHER, 'bbbbbbbbbbb')['state'], 'uncertain')

    def test_reopened_run_retains_policy(self):
        run = self.begin()
        self.s.cleanup(run)
        reopened = self.s.begin('history', channel_id=CID, run_id=run)
        self.assertEqual(reopened['policy'], 'collect-first')

    def test_live_deferral_not_requeued_by_later_scan(self):
        run = self.begin()
        self.s.transition(run, CID, VID, 'defer', 'live_not_ready')
        self.add(VID)
        self.assertEqual(self.s.video(CID, VID)['state'], 'deferred')

    def test_complete_old_cache_reused_and_force_fetch_works(self):
        self.s.db.execute('UPDATE videos SET metadata_checked=?', (time.time()-86400*100,))
        run = self.begin()
        def fetch(*args):
            raise AssertionError('Unexpected metadata request')
        self.assertEqual(ytg.prepare(self.s, run, fetch)['state'], 'reserved')
        with self.assertRaises(AssertionError):
            ytg.metadata(self.s, run, CID, VID, fetch, force=True)

    def test_timing_and_blocker_precedence(self):
        self.assertEqual(ytg.generation_decision(119), 'wait')
        self.assertEqual(ytg.generation_decision(120), 'defer')
        self.assertEqual(ytg.generation_decision(120, substantive=True, growing=True), 'wait')
        self.assertEqual(ytg.generation_decision(300, substantive=True, growing=True), 'defer')
        self.assertEqual(ytg.generation_decision(80, substantive=True, complete=True), 'summary')
        for blocker in ('captcha', 'login', 'quota', 'network'):
            self.assertEqual(ytg.generation_decision(300, complete=True, substantive=True, blocker=blocker), 'stop')


if __name__ == '__main__':
    unittest.main()
