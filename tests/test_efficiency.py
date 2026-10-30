import contextlib
from datetime import datetime
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

import ytg

CID = 'UCX8KQ5xQlm0MnZkmHO7CBDw'
VID = 'aaaaaaaaaaa'
OTHER = 'bbbbbbbbbbb'
CHAT = 'https://gemini.google.com/app/aaaaaaaaaaaaaaaa'
BOOK = 'https://gemini.google.com/notebook/11111111-1111-1111-1111-111111111111'


class EfficiencyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)/'tasks.sqlite3'
        self.s = ytg.Store(self.path)
        self.s.db.execute('INSERT INTO channels(id,url,name,notebook,initialized) VALUES(?,?,?,?,1)',
                          (CID,'https://www.youtube.com/channel/'+CID,'测试频道',BOOK))
        self.clock = patch('ytg.now',return_value=datetime(2026,10,27,4,0))
        self.clock.start()
        self.fetches = 0

    def tearDown(self):
        self.clock.stop()
        self.s.db.close()
        self.tmp.cleanup()

    def video(self,vid=VID,history=True):
        self.s.insert(CID,{'id':vid,'title':'测试原标题','upload_date':'20261026'},'scan',history)
        # These lookup tests deliberately start without a fresh scan cache.
        self.s.db.execute('UPDATE videos SET metadata_checked=NULL WHERE id=?',(vid,))

    def fetch(self,url,flat):
        self.fetches += 1
        return {'id':url.rsplit('=',1)[-1],'channel_id':CID,'title':'完整原标题',
                'upload_date':'20261026','live_status':'not_live'}

    def submitted(self):
        self.video()
        run = self.s.begin('history')['id']
        ytg.prepare(self.s,run,self.fetch)
        self.s.transition(run,CID,VID,'arm')
        self.s.transition(run,CID,VID,'attach',CHAT,'video-match')
        return run

    def test_prepare_reuses_valid_metadata_and_never_resubmits(self):
        self.video()
        run = self.s.begin('history')['id']
        for _ in range(3):
            self.assertEqual(ytg.prepare(self.s,run,self.fetch)['state'],'reserved')
        self.assertEqual(self.fetches,1)
        self.s.transition(run,CID,VID,'arm')
        self.assertEqual(ytg.prepare(self.s,run,self.fetch)['state'],'submitting')
        self.assertEqual(self.fetches,1)
        self.assertEqual(self.s.db.execute('SELECT armed FROM runs').fetchone()[0],1)

    def test_parallel_is_rejected_even_after_chat_attachment(self):
        run=self.submitted()
        self.video(OTHER)
        with self.assertRaises(ytg.Stop):
            self.s.reserve(run,parallel=True)
        self.assertEqual(self.s.reserve(run)['state'],'submitted')
        self.assertEqual(self.s.video(CID,OTHER)['state'],'pending')

    def test_flat_inventory_date_does_not_allow_arm(self):
        self.video()
        run = self.s.begin('history')['id']
        self.s.reserve(run)
        with self.assertRaises(ytg.Stop): self.s.transition(run,CID,VID,'arm')
        self.assertEqual(self.s.video(CID,VID)['state'],'reserved')

    def test_complete_history_metadata_survives_ttl(self):
        self.video()
        run = self.s.begin('history')['id']
        ytg.prepare(self.s,run,self.fetch)
        self.s.db.execute('UPDATE videos SET metadata_checked=?',(time.time()-ytg.METADATA_TTL-1,))
        ytg.prepare(self.s,run,self.fetch)
        self.assertEqual(self.fetches,1)
        self.s.transition(run,CID,VID,'arm')

    def test_live_deferral_clears_previous_metadata_cache(self):
        self.video()
        run = self.s.begin('history')['id']
        ytg.prepare(self.s,run,self.fetch)
        def live(url,flat):
            return {'id':VID,'channel_id':CID,'live_status':'is_live'}
        item = ytg.metadata(self.s,run,CID,VID,live,force=True)
        self.assertEqual(item['state'],'deferred')
        self.assertIsNone(item['metadata_checked'])

    def test_scan_title_change_invalidates_metadata_cache(self):
        self.video()
        run = self.s.begin('history')['id']
        ytg.prepare(self.s,run,self.fetch)
        self.s.park(run)
        self.s.insert(CID,{'id':VID,'title':'修改后的标题'},'later',True)
        self.assertIsNone(self.s.video(CID,VID)['metadata_checked'])

    def test_advance_commits_verification_when_next_lookup_fails(self):
        run=self.submitted()
        self.video(OTHER)
        self.s.transition(run,CID,VID,'summary','\u4e3b\u9898','youtube-content-complete')
        title=ytg.title_for(self.s.video(CID,VID))
        self.s.transition(run,CID,VID,'renamed',title)
        def fail(url,flat): raise RuntimeError('private extractor failure details')
        result=ytg.advance(self.s,run,CID,VID,'verify',title,ytg.CURRENT_PAGE_EVIDENCE,fail)
        self.assertEqual(result['completed']['state'],'verified')
        self.assertIn('stopped',result['next'])
        self.assertNotIn('private extractor',json.dumps(result))
        self.assertEqual(self.s.video(CID,VID)['chat'],CHAT)
        self.assertEqual(self.s.video(CID,OTHER)['state'],'reserved')
        self.assertEqual(ytg.prepare(self.s,run,self.fetch)['id'],OTHER)

    def test_summary_next_prepares_current_archive_without_metadata_fetch(self):
        run=self.submitted()
        self.video(OTHER)
        result=ytg.advance(self.s,run,CID,VID,'summary','\u4e3b\u9898','youtube-content-complete',self.fetch)
        self.assertEqual(result['next']['state'],'summarized')
        self.assertEqual(result['next']['id'],VID)
        self.assertIn('expected_title',result['next'])
        self.assertEqual(self.s.video(CID,OTHER)['state'],'pending')
        self.assertEqual(self.fetches,1)
        self.assertEqual(self.s.db.execute('SELECT armed FROM runs').fetchone()[0],1)

    def test_break_finalization_keeps_prepared_generation_and_orders_backlog(self):
        run = self.submitted()
        self.s.transition(run,CID,VID,'summary','主题','youtube-content-complete')
        self.video(OTHER)
        next_item = ytg.prepare(self.s,run,self.fetch)
        self.assertEqual(next_item['id'],VID)
        item = self.s.finalize(run)
        self.assertEqual(item['id'],VID)
        self.assertEqual(item['phase'],'finalize')
        self.assertEqual(self.s.finalize(run)['id'],VID)
        self.assertEqual(self.s.video(CID,OTHER)['state'],'pending')
        title = item['expected_title']
        self.s.transition(run,CID,VID,'renamed',title)
        self.s.transition(run,CID,VID,'verify',title,'reopen-video-summary')
        self.assertEqual(self.s.finalize(run),{'stop':'no_finalize'})
        self.assertEqual(ytg.prepare(self.s,run,self.fetch)['id'],OTHER)
        self.assertEqual(self.s.db.execute('SELECT armed FROM runs').fetchone()[0],1)

    def test_break_finalization_never_bypasses_active_or_unknown_submission(self):
        run = self.submitted()
        with self.assertRaises(ytg.Stop): self.s.finalize(run)
        self.s.transition(run,CID,VID,'uncertain')
        with self.assertRaises(ytg.Stop): self.s.finalize(run)
        self.assertEqual(self.s.pause_for(CID),'unknown_submit')
        # Even without a global pause, an unresolved item cannot be bypassed.
        self.s.set('pause','')
        with self.assertRaises(ytg.Stop): self.s.finalize(run)
        self.s.db.execute("UPDATE videos SET state='submitting' WHERE id=?",(VID,))
        with self.assertRaises(ytg.Stop): self.s.finalize(run)

    def test_break_finalization_respects_scope_and_existing_backlog(self):
        run = self.submitted()
        self.s.transition(run,CID,VID,'summary','主题','youtube-content-complete')
        self.s.db.execute('UPDATE videos SET owner=NULL')
        other_cid = 'UCYO_jab_esuFRV4b17AJtAw'
        self.s.db.execute('INSERT INTO channels(id,url,name,notebook,initialized) VALUES(?,?,?,?,1)',
                          (other_cid,'https://www.youtube.com/channel/'+other_cid,'另一个频道',BOOK.replace('1','2')))
        self.s.insert(other_cid,{'id':OTHER,'title':'其他原标题','upload_date':'20261026'},'scan',True)
        self.s.db.execute("UPDATE videos SET state='uncertain' WHERE channel_id=?",(other_cid,))
        self.s.db.execute('UPDATE runs SET channel_id=? WHERE id=?',(CID,run))
        self.assertEqual(self.s.finalize(run)['id'],VID)
        self.assertIsNone(self.s.video(other_cid,OTHER)['owner'])

    def test_break_finalization_orders_oldest_and_finishes_renamed_first(self):
        run = self.submitted()
        self.s.transition(run,CID,VID,'summary','主题','youtube-content-complete')
        self.video(OTHER)
        self.s.db.execute("UPDATE videos SET state='summarized',topic='主题',chat=?,found=0 WHERE id=?",(CHAT.replace('aaaaaaaaaaaaaaaa','bbbbbbbbbbbbbbbb'),OTHER))
        self.assertEqual(self.s.finalize(run)['id'],OTHER)
        title = ytg.title_for(self.s.video(CID,VID))
        self.s.transition(run,CID,VID,'renamed',title)
        self.assertEqual(self.s.finalize(run)['id'],VID)

    def test_finalization_requires_live_lease_and_respects_incremental_mode(self):
        run = self.submitted()
        self.s.db.execute('UPDATE lease SET expires=0')
        with self.assertRaises(ytg.Stop): self.s.finalize(run)
        self.s.db.execute('UPDATE lease SET expires=?',(time.time()+900,))
        self.s.db.execute("UPDATE runs SET mode='incremental' WHERE id=?",(run,))
        self.assertEqual(self.s.finalize(run),{'stop':'no_finalize'})

    def test_break_finalization_cli_never_claims_pending_video(self):
        self.video()
        run = self.s.begin('history',channel_id=CID)['id']
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = ytg.main(['--db',str(self.path),'finalize',run])
        self.assertEqual(result,0)
        self.assertEqual(json.loads(output.getvalue()),{'stop':'no_finalize'})
        self.assertEqual(self.s.video(CID,VID)['state'],'pending')

    def test_advance_refuses_arm_before_any_mutation(self):
        self.video()
        run = self.s.begin('history')['id']
        ytg.prepare(self.s,run,self.fetch)
        with self.assertRaises(ytg.Stop): ytg.advance(self.s,run,CID,VID,'arm',None,None,self.fetch)
        self.assertEqual(self.s.video(CID,VID)['state'],'reserved')

    def test_daily_handoff_waits_for_current_archive_then_runs_incremental(self):
        run=self.submitted()
        self.s.transition(run,CID,VID,'summary','\u4e3b\u9898','youtube-content-complete')
        with patch('ytg.now',return_value=datetime(2026,10,27,5,0)):
            item=ytg.prepare(self.s,run,self.fetch)
            self.assertEqual(item['state'],'summarized')
            with self.assertRaises(ytg.Stop): self.s.park(run)
            self.s.transition(run,CID,VID,'renamed',item['expected_title'])
            self.s.transition(run,CID,VID,'verify',item['expected_title'],ytg.CURRENT_PAGE_EVIDENCE)
            self.assertEqual(ytg.prepare(self.s,run,self.fetch)['stop'],'daily_due')
            self.s.park(run)
            daily=self.s.begin('incremental',scheduled=True)['id']
            self.assertEqual(self.s.reserve(daily),{'stop':'empty'})
            self.s.end(daily)
            resumed=self.s.begin('history')['id']
            self.assertEqual(ytg.prepare(self.s,resumed,self.fetch),{'stop':'empty'})

    def test_daily_handoff_waits_for_active_generation(self):
        run = self.submitted()
        with patch('ytg.now',return_value=datetime(2026,10,27,5,0)):
            self.assertEqual(ytg.prepare(self.s,run,self.fetch)['state'],'submitted')
            with self.assertRaises(ytg.Stop): self.s.park(run)
        self.assertEqual(self.s.video(CID,VID)['state'],'submitted')

    def test_recent_scan_reads_windows_without_marking_older_videos_missing(self):
        for i in range(60): self.video(f'{i:011d}')
        calls = []
        def fetch(url,flat,limit=None):
            calls.append(limit)
            return {'channel_id':CID,'entries':[{'id':f'{i:011d}','title':'t'} for i in range(50)]}
        result = ytg.scan(self.s,CID,fetch=fetch)
        self.assertTrue(result['complete'])
        self.assertEqual(calls,[50,50,50])
        self.assertTrue(all(t['scope']=='recent' for t in result['tabs']))
        self.assertEqual(self.s.video(CID,f'{59:011d}')['missing'],0)
        self.assertIsNone(self.s.channel(CID)['last_full'])

    def test_recent_scan_falls_back_when_overlap_is_absent(self):
        calls = []
        def fetch(url,flat,limit=None):
            tab = url.rsplit('/',1)[-1]
            calls.append((tab,limit))
            count = (50 if limit else 70) if tab=='videos' else 0
            return {'channel_id':CID,'entries':[{'id':f'{i:011d}','title':'t'} for i in range(count)]}
        result = ytg.scan(self.s,CID,fetch=fetch)
        self.assertTrue(result['complete'])
        self.assertIn(('videos',None),calls)
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM videos').fetchone()[0],70)

    def test_full_scan_never_uses_window_and_detects_missing(self):
        self.video()
        calls = []
        def fetch(url,flat,limit=None):
            calls.append(limit)
            return {'channel_id':CID,'entries':[]}
        self.assertTrue(ytg.scan(self.s,CID,full=True,fetch=fetch)['complete'])
        self.assertEqual(calls,[None,None,None])
        self.assertEqual(self.s.video(CID,VID)['missing'],1)

    def test_scan_does_not_write_after_lease_expires_during_fetch(self):
        run = self.s.begin('incremental')['id']
        def fetch(url,flat,limit=None):
            self.s.db.execute('UPDATE lease SET expires=0')
            return {'channel_id':CID,'entries':[{'id':VID,'title':'t'}]}
        with self.assertRaises(ytg.Stop): ytg.scan(self.s,CID,fetch=fetch,run=run)
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM videos').fetchone()[0],0)

    def test_scheduled_resume_does_not_rescan_completed_inventory(self):
        with patch('ytg.now',return_value=datetime(2026,10,27,5,0)):
            run = self.s.begin('incremental',scheduled=True)['id']
            calls = []
            def fetch(url,flat,limit=None):
                calls.append(url)
                return {'channel_id':CID,'entries':[]}
            ytg.scan(self.s,CID,fetch=fetch,run=run)
            self.s.db.execute('UPDATE lease SET expires=0')
            resumed = self.s.begin('incremental',scheduled=True)['id']
            result = ytg.scan(self.s,CID,fetch=fetch,run=resumed)
            self.assertEqual(resumed,run)
            self.assertEqual(result['skipped'],'already_checked_today')
            self.assertEqual(len(calls),3)

    def test_missing_database_is_not_recreated_by_status(self):
        missing = Path(self.tmp.name)/'missing.sqlite3'
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(ytg.main(['--db',str(missing),'status']),2)
        self.assertFalse(missing.exists())

    def test_old_database_upgrade_preserves_task_state(self):
        path = Path(self.tmp.name)/'old.sqlite3'
        db = sqlite3.connect(path)
        db.executescript(ytg.SCHEMA.replace(', metadata_checked REAL',''))
        db.execute('INSERT INTO channels(id,url,name) VALUES(?,?,?)',(CID,'https://www.youtube.com/@fixture','fixture'))
        db.execute("INSERT INTO videos(channel_id,id,title,history,found,seen,state,chat,topic) VALUES(?,?,?,1,1,'scan','summarized',?,?)",
                   (CID,VID,'原标题',CHAT,'主题'))
        db.commit()
        db.close()
        upgraded = ytg.Store(path)
        try:
            self.assertEqual(upgraded.video(CID,VID)['state'],'summarized')
            self.assertEqual(upgraded.video(CID,VID)['chat'],CHAT)
            self.assertIsNone(upgraded.video(CID,VID)['metadata_checked'])
        finally:
            upgraded.db.close()

    def test_repeated_deferral_does_not_increment_failure_count(self):
        run = self.submitted()
        self.s.transition(run,CID,VID,'defer','source_unavailable')
        with self.assertRaises(ytg.Stop): self.s.transition(run,CID,VID,'defer','source_unavailable')
        self.assertEqual(self.s.setting('source_failures'),'1')

    def test_compact_cli_keeps_ready_fields_and_returns_small_ack(self):
        self.video()
        run = self.s.begin('history')['id']
        row = ytg.prepare(self.s,run,self.fetch)
        ready = ytg.compact(row)
        self.assertEqual(ready['prompt'],row['prompt'])
        self.assertEqual(ready['prompt'],'https://www.youtube.com/watch?v='+VID+'\n总结')
        self.assertEqual(ready['notebook'],BOOK)
        for key in ['owner','found','seen','marker','metadata_checked']:
            self.assertNotIn(key,ready)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(ytg.main(['--db',str(self.path),'step',run,CID,VID,'arm']),0)
        ack = json.loads(out.getvalue())
        self.assertEqual(ack,{'channel_id':CID,'id':VID,'state':'submitting'})


if __name__=='__main__':
    unittest.main()
