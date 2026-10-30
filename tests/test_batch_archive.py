"""Per-video archive, anomaly evidence, pacing and migrations use isolated databases."""
import contextlib
from datetime import datetime
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import ytg

CID = 'UCX8KQ5xQlm0MnZkmHO7CBDw'
OTHER = 'UCYO_jab_esuFRV4b17AJtAw'
BOOK = 'https://gemini.google.com/notebook/11111111-1111-1111-1111-111111111111'
TOPIC = '\u4e3b\u9898'


class PerVideoArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'tasks.sqlite3'
        self.clock = patch('ytg.time.time', return_value=10000.)
        self.tick = self.clock.start()
        self.local = patch('ytg.now', return_value=datetime(2026, 10, 27, 4, 0))
        self.local.start()
        self.s = ytg.Store(self.path)
        for cid, book in ((CID,BOOK),(OTHER,BOOK.replace('1','2'))):
            self.s.db.execute('INSERT INTO channels(id,url,name,notebook,initialized) VALUES(?,?,?,?,1)',
                              (cid,'https://www.youtube.com/channel/'+cid,'fixture',book))
        for i in range(23):
            self.s.insert(CID, {'id':f'{i:011d}','title':'fixture','upload_date':'20261026'},'scan',True)
        self.run = self.s.begin('history',channel_id=CID,policy='collect-first')['id']

    def tearDown(self):
        self.s.db.close()
        self.local.stop()
        self.clock.stop()
        self.tmp.cleanup()

    def generate(self):
        item = ytg.prepare(self.s,self.run)
        self.assertEqual(item['state'],'reserved')
        self.s.transition(self.run,CID,item['id'],'arm')
        chat = 'https://gemini.google.com/app/'+f"{int(item['id']):016x}"
        self.s.transition(self.run,CID,item['id'],'attach',chat,'video-match')
        return self.s.transition(self.run,CID,item['id'],'summary',TOPIC,'youtube-content-complete')

    def archive(self,item,evidence=ytg.CURRENT_PAGE_EVIDENCE):
        if self.s.video(CID,item['id'])['state']=='summarized':
            self.s.transition(self.run,CID,item['id'],'renamed',item['expected_title'])
        return self.s.transition(self.run,CID,item['id'],'verify',item['expected_title'],evidence)

    def finish(self):
        return self.archive(self.generate())

    def count(self):
        return self.s.db.execute('SELECT processed FROM pacing WHERE scope=?',(CID,)).fetchone()[0]

    def test_summary_next_returns_same_chat_then_verify_next_advances(self):
        item = self.generate()
        self.assertEqual(self.count(),0)
        self.assertEqual(ytg.prepare(self.s,self.run)['id'],item['id'])
        self.assertEqual(self.s.finalize(self.run)['id'],item['id'])
        self.s.transition(self.run,CID,item['id'],'renamed',item['expected_title'])
        result=ytg.advance(self.s,self.run,CID,item['id'],'verify',item['expected_title'],ytg.CURRENT_PAGE_EVIDENCE)
        self.assertEqual(result['completed']['state'],'verified')
        self.assertNotEqual(result['next']['id'],item['id'])
        self.assertEqual(self.count(),1)

    def test_all_four_anomalies_require_reopen_and_preserve_flag_after_rename(self):
        for reason in sorted(ytg.REOPEN_REASONS):
            with self.subTest(reason=reason):
                item=self.generate()
                self.s.transition(self.run,CID,item['id'],'require-reopen',reason)
                self.s.transition(self.run,CID,item['id'],'renamed',item['expected_title'])
                self.assertEqual(self.s.finalize(self.run)['reopen_required'],reason)
                before=self.count()
                with self.assertRaises(ytg.Stop):
                    self.archive(item)
                self.assertEqual(self.count(),before)
                self.assertEqual(self.s.video(CID,item['id'])['state'],'renamed')
                self.archive(item,'reopen-video-summary')
                self.assertIsNone(self.s.video(CID,item['id'])['reopen_required'])

    def test_invalid_evidence_or_title_cannot_complete_or_count(self):
        item=self.generate()
        self.s.transition(self.run,CID,item['id'],'renamed',item['expected_title'])
        for evidence,title in ((None,item['expected_title']),(ytg.CURRENT_PAGE_EVIDENCE,'wrong'),('unknown',item['expected_title'])):
            with self.assertRaises(ytg.Stop):
                self.s.transition(self.run,CID,item['id'],'verify',title,evidence)
        self.assertEqual(self.count(),0)
        self.archive(item)
        with self.assertRaises(ytg.Stop):
            self.archive(item)
        self.assertEqual(self.count(),1)

    def test_nineteenth_twentieth_twentyfirst_and_break_starts_at_verification(self):
        for _ in range(19):
            self.finish()
        self.assertEqual(self.count(),19)
        item=self.generate()
        self.tick.return_value=10050.
        self.s.transition(self.run,CID,item['id'],'renamed',item['expected_title'])
        self.assertIsNone(self.s.db.execute('SELECT break_started FROM pacing WHERE scope=?',(CID,)).fetchone()[0])
        self.tick.return_value=10060.
        result=ytg.advance(self.s,self.run,CID,item['id'],'verify',item['expected_title'],ytg.CURRENT_PAGE_EVIDENCE)
        self.assertEqual(result['next'],{'stop':'break_required','seconds_remaining':120})
        self.assertEqual(self.count(),20)
        self.tick.return_value=10179.
        self.assertEqual(ytg.prepare(self.s,self.run)['seconds_remaining'],1)
        self.tick.return_value=10180.
        self.assertEqual(ytg.prepare(self.s,self.run)['state'],'reserved')
        self.assertEqual(self.count(),0)

    def test_success_and_deferrals_count_once_including_archive_failure(self):
        for _ in range(18):
            item=ytg.prepare(self.s,self.run)
            self.s.transition(self.run,CID,item['id'],'defer','metadata_unavailable')
        item=self.generate()
        self.assertEqual(self.count(),18)
        self.archive(item)
        item=self.generate()
        self.s.transition(self.run,CID,item['id'],'require-reopen','operation_error')
        self.s.transition(self.run,CID,item['id'],'defer','archive_unverified')
        self.assertEqual(self.count(),20)
        with self.assertRaises(ytg.Stop):
            self.s.transition(self.run,CID,item['id'],'defer','archive_unverified')
        self.assertEqual(ytg.prepare(self.s,self.run)['stop'],'break_required')
        report=self.s.report(channel_id=CID)
        self.assertEqual((report['generated'],report['verified']),(2,1))

    def test_short_tail_needs_no_break_and_all_done_needs_no_wait(self):
        self.s.db.execute("DELETE FROM videos WHERE id<>'00000000000'")
        self.finish()
        self.assertEqual(ytg.prepare(self.s,self.run),{'stop':'empty'})
        self.assertTrue(self.s.report(channel_id=CID)['complete'])
        self.s.end(self.run)

    def test_break_persists_on_reopen_and_history_incremental_handoff(self):
        for _ in range(20):
            self.finish()
        self.s.cleanup(self.run)
        self.s.db.close()
        self.s=ytg.Store(self.path)
        self.run=self.s.begin('history',channel_id=CID,run_id=self.run)['id']
        self.assertEqual(ytg.prepare(self.s,self.run)['seconds_remaining'],120)
        self.s.cleanup(self.run)
        self.s.insert(CID,{'id':'99999999999','title':'fixture','upload_date':'20261026'},'scan',False)
        self.run=self.s.begin('incremental',channel_id=CID)['id']
        self.assertEqual(ytg.prepare(self.s,self.run)['seconds_remaining'],120)
        self.tick.return_value=10120.
        self.assertEqual(ytg.prepare(self.s,self.run,lambda *a: {'id':'99999999999','channel_id':CID,'title':'fixture','upload_date':'20261026'})['state'],'reserved')

    def test_cleanup_and_same_run_reopen_require_reopened_evidence(self):
        item=self.generate()
        self.s.transition(self.run,CID,item['id'],'renamed',item['expected_title'])
        self.s.cleanup(self.run)
        self.s.db.close()
        self.s=ytg.Store(self.path)
        self.run=self.s.begin('history',channel_id=CID,run_id=self.run)['id']
        resumed=self.s.finalize(self.run)
        self.assertEqual(resumed['reopen_required'],'page_interrupted')
        with self.assertRaises(ytg.Stop):
            self.archive(resumed)
        self.archive(resumed,'reopen-video-summary')
        self.assertEqual(self.count(),1)

    def test_explicit_anomaly_reason_survives_reopen_and_new_run(self):
        item=self.generate()
        self.s.transition(self.run,CID,item['id'],'require-reopen','membership_unclear')
        self.s.cleanup(self.run)
        self.s.db.close()
        self.s=ytg.Store(self.path)
        self.run=self.s.begin('history',channel_id=CID,policy='collect-first')['id']
        resumed=self.s.finalize(self.run)
        self.assertEqual(resumed['reopen_required'],'membership_unclear')
        with self.assertRaises(ytg.Stop):
            self.archive(resumed)
        self.archive(resumed,'reload-notebook-reopen-video-summary')

    def test_invalid_reopen_reason_and_pending_item_cannot_be_flagged(self):
        item=self.s.reserve(self.run)
        with self.assertRaises(ytg.Stop):
            self.s.transition(self.run,CID,item['id'],'require-reopen','operation_error')
        item=self.generate()
        with self.assertRaises(ytg.Stop):
            self.s.transition(self.run,CID,item['id'],'require-reopen','arbitrary page text')
        self.assertIsNone(self.s.video(CID,item['id'])['reopen_required'])

    def test_direct_arm_cannot_bypass_existing_archive(self):
        item=self.generate()
        other=self.s.db.execute("SELECT id FROM videos WHERE state='pending' LIMIT 1").fetchone()[0]
        # Simulates a reservation left by the former batch worker.
        self.s.db.execute("UPDATE videos SET state='reserved',owner=? WHERE id=?",(self.run,other))
        with self.assertRaises(ytg.Stop):
            self.s.transition(self.run,CID,other,'arm')
        self.assertEqual(self.s.reserve(self.run)['id'],item['id'])
        self.archive(item)
        self.s.reserve(self.run)
        self.assertEqual(self.s.transition(self.run,CID,other,'arm')['state'],'submitting')

    def test_legacy_upgrade_preserves_counts_and_generated_chat(self):
        item=self.generate()
        # Construct the prior schema: legacy collect-first counted at summary time.
        self.s.db.execute('UPDATE runs SET batch_count=9')
        self.s.db.execute('DROP TABLE pacing')
        for column in ('processed_counted','reopen_required'):
            self.s.db.execute('ALTER TABLE videos DROP COLUMN '+column)
        self.s.db.close()
        self.s=ytg.Store(self.path)
        self.assertEqual(self.count(),9)
        recovered=self.s.finalize(self.run)
        self.assertEqual(recovered['chat'],item['chat'])
        self.archive(recovered,'reopen-video-summary')
        self.assertEqual(self.count(),9)
        self.finish()
        self.assertEqual(self.count(),10)
        self.assertEqual(ytg.prepare(self.s,self.run)['state'],'reserved')

    def test_unknown_legacy_counter_requires_120_seconds_without_resetting_work(self):
        item=self.generate()
        self.s.db.execute('DROP TABLE pacing')
        self.s.db.execute('ALTER TABLE runs DROP COLUMN batch_count')
        for column in ('processed_counted','reopen_required'):
            self.s.db.execute('ALTER TABLE videos DROP COLUMN '+column)
        self.s.db.close()
        self.s=ytg.Store(self.path)
        self.archive(self.s.finalize(self.run),'reopen-video-summary')
        self.assertEqual(ytg.prepare(self.s,self.run)['seconds_remaining'],120)
        self.tick.return_value=10120.
        self.assertEqual(ytg.prepare(self.s,self.run)['state'],'reserved')
        self.assertEqual(self.s.video(CID,item['id'])['chat'],item['chat'])

    def test_cli_reports_reopen_flag_and_rejects_current_page_evidence(self):
        item=self.generate()
        output=io.StringIO()
        with contextlib.redirect_stdout(output):
            code=ytg.main(['--db',str(self.path),'step',self.run,CID,item['id'],'require-reopen','--value','title_not_updated'])
        self.assertEqual(code,0)
        self.assertEqual(json.loads(output.getvalue())['reopen_required'],'title_not_updated')
        self.s.transition(self.run,CID,item['id'],'renamed',item['expected_title'])
        output=io.StringIO()
        with contextlib.redirect_stdout(output):
            code=ytg.main(['--db',str(self.path),'step',self.run,CID,item['id'],'verify','--value',item['expected_title'],'--evidence',ytg.CURRENT_PAGE_EVIDENCE])
        self.assertEqual(code,2)
        self.assertIn('requires reopened',json.loads(output.getvalue())['stopped'])

    def test_strict_and_incremental_accept_normal_current_page_and_archive_failure(self):
        self.s.cleanup(self.run)
        self.s.db.execute('UPDATE videos SET history=0')
        self.run=self.s.begin('incremental',channel_id=CID)['id']
        item=self.s.reserve(self.run)
        self.s.db.execute('UPDATE videos SET metadata_checked=?',(10000.,))
        self.s.transition(self.run,CID,item['id'],'arm')
        self.s.transition(self.run,CID,item['id'],'attach','https://gemini.google.com/app/aaaaaaaaaaaaaaaa','video-match')
        result=ytg.advance(self.s,self.run,CID,item['id'],'summary',TOPIC,'youtube-content-complete')
        self.assertEqual(result['next']['id'],item['id'])
        self.archive(result['next'])
        self.assertEqual(self.count(),1)
        item=self.s.reserve(self.run)
        self.s.transition(self.run,CID,item['id'],'arm')
        self.s.transition(self.run,CID,item['id'],'attach','https://gemini.google.com/app/bbbbbbbbbbbbbbbb','video-match')
        self.s.transition(self.run,CID,item['id'],'summary',TOPIC,'youtube-content-complete')
        self.s.transition(self.run,CID,item['id'],'defer','archive_unverified')
        self.assertEqual(self.count(),2)

    def test_channel_pause_and_pacing_are_isolated(self):
        for _ in range(20):
            self.finish()
        self.s.set_pause('source_unavailable',CID)
        self.s.cleanup(self.run)
        run=self.s.begin('history',channel_id=OTHER,policy='collect-first')['id']
        self.assertEqual(self.s.reserve(run),{'stop':'empty'})
        self.assertEqual(self.s.setting('pause'),'')
        self.assertEqual(self.count(),20)

    def test_generation_and_health_timeouts_remain_separate(self):
        self.assertEqual(ytg.generation_decision(30,substantive=True,complete=True),'summary')
        self.assertEqual(ytg.generation_decision(120),'defer')
        self.assertEqual(ytg.generation_decision(300,substantive=True,growing=True),'defer')
        for _ in range(5):
            item=self.s.reserve(self.run)
            self.s.transition(self.run,CID,item['id'],'defer','generation_timeout')
        result=self.s.reserve(self.run)
        self.assertEqual(result,{'stop':'health_cooldown','seconds_remaining':300})
        self.tick.return_value=10120.
        self.assertEqual(self.s.reserve(self.run)['seconds_remaining'],180)


if __name__=='__main__':
    unittest.main()
