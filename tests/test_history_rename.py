"""History batching and local bridge gates use temporary databases only."""
import tempfile
import argparse
import json
import socket
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

import history_rename as hr
import ytg

CID='UCX8KQ5xQlm0MnZkmHO7CBDw'
BOOK='https://gemini.google.com/notebook/'+'1'*8+'-'+'1'*4+'-'+'1'*4+'-'+'1'*4+'-'+'1'*12
NBL=BOOK.replace('gemini.google.com','notebooklm.google.com')
ACCOUNT='fixture@example.test'


class HistoryRenameTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.s=ytg.Store(Path(self.temp.name)/'tasks.sqlite3')
        self.s.db.execute('INSERT INTO channels(id,url,name,notebook,initialized) VALUES(?,?,?,?,1)',(CID,'https://www.youtube.com/channel/'+CID,'fixture',BOOK))
        hr.configure(self.s,CID,ACCOUNT)
        for n in range(3):
            self.s.insert(CID,{'id':f'{n:011d}','title':'original '+str(n),'upload_date':'20261026'},'scan',True)
        self.run=self.s.begin('history',channel_id=CID,policy='collect-first')['id']
        self.gate=patch.object(self.s,'_claim_submit_slot',return_value=0)
        self.gate.start()
        self.due=patch.object(self.s,'daily_due',return_value=False)
        self.due.start()

    def tearDown(self):
        self.due.stop(); self.gate.stop(); self.s.db.close(); self.temp.cleanup()

    def generate(self):
        item=ytg.prepare(self.s,self.run)
        self.s.transition(self.run,CID,item['id'],'arm')
        self.s.transition(self.run,CID,item['id'],'attach','https://gemini.google.com/app/'+f"{int(item['id'])+1:016x}",'video-match')
        return self.s.transition(self.run,CID,item['id'],'summary','主题','youtube-content-complete')

    def test_history_extension_batches_checked_summaries_counts_once_and_keeps_report_incomplete(self):
        hr.enable(self.s,self.run)
        first=self.generate(); second=self.generate()
        self.assertNotEqual(first['id'],second['id'])
        self.assertEqual(self.s.db.execute("SELECT processed FROM pacing WHERE scope='gemini-account'").fetchone()[0],2)
        self.assertFalse(self.s.report('history',CID)['complete'])
        self.assertEqual(self.s.report('history',CID)['rename_pending'],2)
        bridge=hr.Bridge(self.s,self.run)
        job=bridge.next()
        self.submit_chat(bridge,job)
        self.assertEqual(self.s.db.execute("SELECT processed FROM pacing WHERE scope='gemini-account'").fetchone()[0],2)
        self.assertEqual(self.s.video(CID,job['video_id'])['state'],'verified')
        self.assertEqual(hr.pending_report(self.s,CID)['chat_verified'],1)
        audit=self.s.db.execute('SELECT recovery_attempts,verification_phase,verification_mode FROM history_rename_items WHERE channel_id=? AND video_id=?',(CID,job['video_id'])).fetchone()
        self.assertEqual(tuple(audit),(0,'readback','extension-reopen-v1'))

    def test_include_verified_can_be_limited_to_one_selected_video(self):
        hr.enable(self.s,self.run)
        first=self.generate(); second=self.generate()
        self.s.db.execute("UPDATE videos SET state='verified',owner=NULL WHERE channel_id=? AND id IN (?,?)",(CID,first['id'],second['id']))
        bridge=hr.Bridge(self.s,self.run,include_verified=True,video_id=first['id'])
        job=bridge.next()
        self.assertEqual(job['video_id'],first['id'])
        self.submit_chat(bridge,job)
        self.assertTrue(bridge.next()['done'])
        row=self.s.db.execute('SELECT chat_status FROM history_rename_items WHERE channel_id=? AND video_id=?',(CID,second['id'])).fetchone()
        self.assertEqual(row['chat_status'],'pending')

    def submit_chat(self,bridge,job,**overrides):
        data={'kind':'chat','status':'verified','video_id':job['video_id'],'title':job['title'],'account':ACCOUNT,'notebook':BOOK,'chat':job['chat'],'video_checked':True,'summary_present':True,'membership_checked':True,'old_chat_title':'old','verification_phase':'readback'}
        data.update(overrides)
        return bridge.result(data)

    def test_serial_and_incremental_cannot_batch(self):
        first=self.generate()
        self.assertEqual(ytg.prepare(self.s,self.run)['id'],first['id'])
        self.s.cleanup(self.run)
        inc=self.s.begin('incremental',channel_id=CID,policy='collect-first')['id']
        with self.assertRaises(ytg.Stop): hr.enable(self.s,inc)

    def test_extension_preflight_does_not_create_a_run_or_lease_on_unknown_submission(self):
        self.s.db.execute("UPDATE videos SET state='uncertain' WHERE channel_id=? AND id=?",(CID,'00000000000'))
        run_count=self.s.db.execute('SELECT count(*) FROM runs').fetchone()[0]
        leases=list(self.s.db.execute('SELECT scope,run_id FROM lease'))
        with self.assertRaisesRegex(ytg.Stop,'Resolve the current submission'):
            hr.begin_extension(self.s,CID,'collect-first')
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM runs').fetchone()[0],run_count)
        self.assertEqual([tuple(row) for row in self.s.db.execute('SELECT scope,run_id FROM lease')],[tuple(row) for row in leases])

    def test_bridge_result_rejects_wrong_account_title_missing_evidence(self):
        hr.enable(self.s,self.run); self.generate()
        bridge=hr.Bridge(self.s,self.run); job=bridge.next()
        for overrides in ({'account':'other@example.test'},{'title':'wrong'},{'membership_checked':False},{'summary_present':False},{'video_checked':False}):
            with self.subTest(overrides=overrides),self.assertRaises(ytg.Stop):
                self.submit_chat(bridge,job,**overrides)
        self.assertEqual(self.s.video(CID,job['video_id'])['state'],'summarized')
        self.submit_chat(bridge,job)
        self.assertEqual(hr.pending_report(self.s,CID)['pending'],0)

    def test_pause_and_lease_gate_stops_bridge_and_maintenance_blocks_generation(self):
        hr.enable(self.s,self.run); self.generate()
        bridge=hr.Bridge(self.s,self.run); bridge.next()
        item=ytg.prepare(self.s,self.run)
        with self.assertRaises(ytg.Stop): self.s.transition(self.run,CID,item['id'],'arm')
        self.s.set_pause('quota')
        with self.assertRaises(ytg.Stop): bridge.next()
        self.s.clear_pause()
        self.s.db.execute('UPDATE lease SET expires=0 WHERE run_id=?',(self.run,))
        with self.assertRaises(ytg.Stop): bridge.next()

    def test_checkpoint_and_failure_survive_database_reopen(self):
        hr.enable(self.s,self.run); self.generate()
        bridge=hr.Bridge(self.s,self.run); job=bridge.next()
        bridge.checkpoint({'video_id':job['video_id'],'old_chat_title':'original default chat'})
        bridge.result({'video_id':job['video_id'],'title':job['title'],'kind':'chat','status':'pending','reason':'save_unverified','recovery_attempts':2,'verification_phase':'readback'})
        bridge.close()
        path=self.s.db.execute('PRAGMA database_list').fetchone()[2]
        self.s.db.close(); self.s=ytg.Store(path)
        row=self.s.db.execute('SELECT * FROM history_rename_items').fetchone()
        self.assertEqual(row['old_chat_title'],'original default chat')
        self.assertEqual(row['chat_status'],'pending')
        self.assertEqual(row['reason'],'save_unverified')
        self.assertEqual(row['recovery_attempts'],2)
        self.assertEqual(row['verification_phase'],'readback')
        self.assertEqual(self.s.video(CID,job['video_id'])['reopen_required'],'page_interrupted')

    def test_daily_handoff_can_park_batch_backlog(self):
        hr.enable(self.s,self.run); item=self.generate()
        self.s.park(self.run)
        self.assertEqual(self.s.video(CID,item['id'])['state'],'summarized')
        self.assertEqual(hr.pending_report(self.s,CID)['pending'],1)
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM lease').fetchone()[0],0)

    def test_incremental_summary_still_blocks_next_video(self):
        self.s.cleanup(self.run)
        self.s.db.execute('UPDATE videos SET history=0')
        self.run=self.s.begin('incremental',channel_id=CID,policy='collect-first')['id']
        first=self.generate()
        self.assertEqual(ytg.prepare(self.s,self.run)['id'],first['id'])
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM history_rename_items').fetchone()[0],0)

    def test_configure_requires_exact_account(self):
        with self.assertRaises(ytg.Stop): hr.configure(self.s,CID,'not an email')

    def test_source_cannot_verify_chat_and_backlog_requires_explicit_review(self):
        hr.enable(self.s,self.run)
        for _ in range(3): self.generate()
        self.assertEqual(ytg.prepare(self.s,self.run)['stop'],'archive_pending')
        bridge=hr.Bridge(self.s,self.run); job=bridge.next()
        with self.assertRaises(ytg.Stop):
            bridge.result({'kind':'source','status':'verified','video_id':job['video_id'],'title':job['title'],'account':ACCOUNT,'notebook':NBL,'source_id':'fixture','identity_checked':True})
        with self.assertRaises(ytg.Stop): self.s.end(self.run)
        for row in self.s.db.execute('SELECT video_id FROM history_rename_items').fetchall():
            hr.dispatch(self.s,argparse.Namespace(operation='review',run=self.run,channel=CID,video=row[0],reason='operator_source_review'))
        report=self.s.report('history',CID)
        self.assertTrue(report['complete'])
        self.assertEqual(report['verified'],0)
        self.assertEqual(len(report['rename_manual_review']),3)

    def test_bridge_enforces_persisted_account_break(self):
        hr.enable(self.s,self.run); self.generate()
        bridge=hr.Bridge(self.s,self.run)
        self.s.db.execute("UPDATE pacing SET processed=20,break_started=? WHERE scope='gemini-account'",(time.time(),))
        result=bridge.pace()
        self.assertFalse(result['ready'])
        self.assertEqual(result['kind'],'break')
        self.assertGreater(result['seconds_remaining'],85)

    def test_local_http_pairing_origin_and_release(self):
        hr.enable(self.s,self.run); self.generate()
        path=Path(self.s.db.execute('PRAGMA database_list').fetchone()[2])
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
        proc=subprocess.Popen([sys.executable,'-u',str(Path(ytg.__file__).resolve()),'--db',str(path),'history-rename','serve',self.run,'--port',str(port)],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            pairing=path.parent/f'history-rename-{self.run}.local.json'
            deadline=time.time()+8
            while not pairing.exists() and time.time()<deadline and proc.poll() is None: time.sleep(.03)
            self.assertTrue(pairing.exists())
            info=json.loads(pairing.read_text(encoding='utf-8'))
            def request(route,key,origin='chrome-extension://'+'a'*32):
                req=urllib.request.Request(info['bridge']+route,data=b'{}',headers={'Content-Type':'application/json','Origin':origin,'X-YTG-Pairing':key},method='POST')
                with urllib.request.urlopen(req,timeout=3) as response: return json.load(response)
            for key,origin in (('wrong','chrome-extension://'+'a'*32),(info['pairing'],'https://evil.example')):
                with self.assertRaises(urllib.error.HTTPError) as ctx: request('/next',key,origin)
                self.assertEqual(ctx.exception.code,403)
                ctx.exception.close()
            job=request('/next',info['pairing'])
            self.assertIn('video_id',job)
            self.assertNotIn('summary',job)
            self.assertNotIn('prompt',job)
            self.assertTrue(request('/release',info['pairing'])['released'])
            proc.communicate(timeout=5)
            self.assertEqual(proc.returncode,0)
            self.assertFalse(pairing.exists())
            self.assertEqual(self.s.db.execute('SELECT count(*) FROM lease').fetchone()[0],0)
        finally:
            if proc.poll() is None: proc.terminate(); proc.communicate(timeout=5)


if __name__=='__main__': unittest.main()
