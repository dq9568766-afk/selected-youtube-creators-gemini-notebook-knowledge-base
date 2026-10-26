import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

import ytg

CID = 'UCX8KQ5xQlm0MnZkmHO7CBDw'
CID2 = 'UCYO_jab_esuFRV4b17AJtAw'
VID = 'IN28qWHF1s0'
CHAT = 'https://gemini.google.com/app/aaaaaaaaaaaaaaaa'
BOOK = 'https://gemini.google.com/notebook/11111111-1111-1111-1111-111111111111'

class QueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.s=ytg.Store(Path(self.tmp.name)/'tasks.sqlite3')
        self.channel(CID)

    def tearDown(self):
        self.s.db.close()
        self.tmp.cleanup()

    def channel(self,cid):
        self.s.db.execute('INSERT INTO channels(id,url,name,notebook,initialized) VALUES(?,?,?,?,1)',
                          (cid,'https://www.youtube.com/channel/'+cid,cid,BOOK if cid==CID else BOOK.replace('1111','2222')))

    def video(self,vid=VID,cid=CID,history=True):
        self.s.insert(cid,{'id':vid,'title':'原标题：保留标点','upload_date':'20261026'},'scan',history)

    def claimed(self,mode='history'):
        self.video()
        run=self.s.begin(mode)['id']
        self.s.reserve(run)
        return run

    def expire(self):
        self.s.db.execute('UPDATE lease SET expires=0')

    def test_inventory_duplicate_across_tabs(self):
        self.video(); self.video()
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM videos').fetchone()[0],1)

    def test_real_date_and_topic_validation(self):
        self.video()
        v=self.s.video(CID,VID)
        self.assertEqual(ytg.title_for(v,'公德观'),'20261026-公德观-原标题：保留标点')
        for topic in ['','六个汉字主题词','abc','词 词','公德-观']:
            with self.assertRaises(ytg.Stop): ytg.title_for(v,topic)
        for d in ['00000000','20260230','2026-10-26',None]:
            with self.assertRaises(ytg.Stop): ytg.date_value(d)

    def test_new_video_before_history(self):
        self.video('aaaaaaaaaaa'); self.video(VID,history=False)
        run=self.s.begin('history')['id']
        self.assertEqual(self.s.reserve(run)['id'],VID)

    def test_channel_round_robin(self):
        self.channel(CID2)
        self.video(); self.video('aaaaaaaaaaa'); self.video('bbbbbbbbbbb',CID2)
        run=self.s.begin('history')['id']
        first=self.s.reserve(run)
        self.s.transition(run,CID,first['id'],'defer','metadata_unavailable')
        self.assertEqual(self.s.reserve(run)['channel_id'],CID2)

    def test_reservation_is_idempotent(self):
        run=self.claimed()
        self.assertEqual(self.s.reserve(run)['id'],VID)
        self.assertEqual(self.s.db.execute('SELECT armed FROM runs').fetchone()[0],0)

    def test_expired_reservation_can_be_retried(self):
        run=self.claimed(); self.expire()
        new=self.s.begin('history')['id']
        self.assertEqual(self.s.reserve(new)['state'],'reserved')

    def test_crash_after_arm_never_requeues_submission(self):
        run=self.claimed(); self.s.transition(run,CID,VID,'arm'); self.expire()
        new=self.s.begin('history')['id']
        item=self.s.reserve(new)
        self.assertEqual(item['state'],'uncertain')
        with self.assertRaises(ytg.Stop): self.s.transition(new,CID,VID,'arm')

    def test_unknown_results_require_pause(self):
        run=self.claimed(); self.s.transition(run,CID,VID,'arm')
        self.s.transition(run,CID,VID,'uncertain')
        with self.assertRaises(ytg.Stop): self.s.reserve(run)
        self.assertEqual(self.s.setting('pause'),'unknown_submit')

    def test_attach_requires_exact_marker_evidence(self):
        run=self.claimed(); self.s.transition(run,CID,VID,'arm')
        with self.assertRaises(ytg.Stop): self.s.transition(run,CID,VID,'attach',CHAT)
        self.s.transition(run,CID,VID,'attach',CHAT,'marker-and-video-match')
        with self.assertRaises(ytg.Stop): self.s.transition(run,CID,VID,'attach',CHAT.replace('aaaa','bbbb'),'marker-and-video-match')

    def test_completed_flow_and_verification_gate(self):
        run=self.claimed()
        with self.assertRaises(ytg.Stop): self.s.transition(run,CID,VID,'summary','公德观','youtube-content-complete')
        self.s.transition(run,CID,VID,'arm')
        self.s.transition(run,CID,VID,'attach',CHAT,'marker-and-video-match')
        self.s.transition(run,CID,VID,'summary','公德观','youtube-content-complete')
        title=ytg.title_for(self.s.video(CID,VID))
        with self.assertRaises(ytg.Stop): self.s.transition(run,CID,VID,'renamed','wrong')
        self.s.transition(run,CID,VID,'renamed',title)
        with self.assertRaises(ytg.Stop): self.s.transition(run,CID,VID,'verify',title)
        self.s.transition(run,CID,VID,'verify',title,'reload-notebook-reopen-marker-summary')
        self.assertEqual(self.s.reserve(run),{'stop':'empty'})

    def test_only_one_worker(self):
        self.s.begin('history')
        with self.assertRaises(ytg.Stop): self.s.begin('incremental')

    def test_stale_worker_cannot_mutate(self):
        old=self.claimed(); self.expire(); new=self.s.begin('history')['id']
        with self.assertRaises(ytg.Stop): self.s.transition(old,CID,VID,'arm')
        self.assertEqual(self.s.reserve(new)['state'],'reserved')

    def test_incremental_cap_is_global_even_after_recovery(self):
        self.channel(CID2)
        for i in range(21): self.video(f'{i:011d}',CID if i%2 else CID2)
        run=self.s.begin('incremental')['id']
        for _ in range(20):
            v=self.s.reserve(run)
            self.s.transition(run,v['channel_id'],v['id'],'arm')
            self.s.transition(run,v['channel_id'],v['id'],'defer','source_unavailable')
        self.assertEqual(self.s.reserve(run)['stop'],'round_cap')
        self.assertEqual(self.s.db.execute('SELECT armed FROM runs').fetchone()[0],20)

    def test_history_has_no_twenty_total_limit(self):
        run=self.claimed()
        self.s.db.execute('UPDATE runs SET armed=30')
        self.s.transition(run,CID,VID,'arm')
        self.assertEqual(self.s.db.execute('SELECT armed FROM runs').fetchone()[0],31)

    def test_partial_inventory_does_not_mark_missing_or_initialized(self):
        self.video()
        self.s.db.execute('UPDATE channels SET initialized=0')
        def fetch(url,flat):
            if url.endswith('shorts'): raise RuntimeError('network failed')
            return {'channel_id':CID,'entries':[]}
        result=ytg.scan(self.s,CID,True,fetch)
        self.assertFalse(result['complete'])
        self.assertEqual(self.s.channel(CID)['initialized'],0)
        self.assertEqual(self.s.video(CID,VID)['missing'],0)

    def test_absent_tab_is_not_network_failure(self):
        def fetch(url,flat):
            tab=url.rsplit('/',1)[-1]
            if tab!='videos': raise RuntimeError('This channel does not have a '+tab+' tab')
            return {'channel_id':CID,'entries':[{'id':VID,'title':'test'}]}
        self.assertTrue(ytg.scan(self.s,CID,True,fetch)['complete'])

    def test_missing_is_only_marked_after_all_tabs_complete(self):
        self.video()
        def fetch(url,flat): return {'channel_id':CID,'entries':[]}
        self.assertTrue(ytg.scan(self.s,CID,True,fetch)['complete'])
        self.assertEqual(self.s.video(CID,VID)['missing'],1)

    def test_metadata_checks_identity_and_live_status(self):
        run=self.claimed()
        def wrong(url,flat): return {'id':VID,'channel_id':CID2,'title':'wrong','upload_date':'20261026'}
        with self.assertRaises(ytg.Stop): ytg.metadata(self.s,run,CID,VID,wrong)
        def live(url,flat): return {'id':VID,'channel_id':CID,'title':'live','upload_date':'20261026','live_status':'is_live'}
        self.assertEqual(ytg.metadata(self.s,run,CID,VID,live)['state'],'deferred')
        self.video()
        self.assertEqual(self.s.video(CID,VID)['state'],'pending')

    def test_schedule_cannot_start_history_bypass(self):
        with self.assertRaises(ytg.Stop): self.s.begin('history',scheduled=True)

    def test_scheduled_round_resumes_same_counter(self):
        from datetime import datetime, timezone
        monday=datetime(2026,10,26,9,0,tzinfo=timezone.utc)
        with patch('ytg.now',return_value=monday):
            first=self.s.begin('incremental',scheduled=True)
            self.s.db.execute('UPDATE runs SET armed=19')
            self.expire()
            second=self.s.begin('incremental',scheduled=True)
            self.assertEqual(first['id'],second['id'])
            self.assertEqual(second['armed'],19)
            self.s.end(second['id'])
            with self.assertRaises(ytg.Stop): self.s.begin('incremental',scheduled=True)

    def test_scheduled_clock_uses_local_monday_thursday(self):
        from datetime import datetime
        for t in [datetime(2026,10,26,8,29),datetime(2026,10,27,9,0)]:
            with patch('ytg.now',return_value=t):
                with self.assertRaises(ytg.Stop): self.s.begin('incremental',scheduled=True)

    def test_no_summary_or_auth_columns(self):
        names=[]
        for table in ['channels','videos','runs','settings','scans','lease']:
            names.extend(r['name'] for r in self.s.db.execute('PRAGMA table_info('+table+')'))
        self.assertFalse(set(names)&{'summary','transcript','cookie','token','browser_data'})

    def test_no_cross_channel_chat_url_reuse(self):
        run=self.claimed(); self.s.transition(run,CID,VID,'arm')
        self.s.transition(run,CID,VID,'attach',CHAT,'marker-and-video-match')
        self.s.transition(run,CID,VID,'defer','source_unavailable')
        self.video('aaaaaaaaaaa')
        self.s.reserve(run); self.s.transition(run,CID,'aaaaaaaaaaa','arm')
        with self.assertRaises(Exception): self.s.transition(run,CID,'aaaaaaaaaaa','attach',CHAT,'marker-and-video-match')

    def test_url_validation(self):
        for url in ['http://www.youtube.com/@test','https://evil.test/@test','https://www.youtube.com/watch?v=abcdefghijk']:
            with self.assertRaises(ytg.Stop): ytg.channel_url(url)
        with self.assertRaises(ytg.Stop): ytg.gemini_url(CHAT+'?token=no','chat')

if __name__=='__main__': unittest.main()
