import unittest
from unittest.mock import patch
import test_efficiency as fixtures
from test_efficiency import CID, VID
import ytg
import backfill


class BackfillTests(unittest.TestCase):
    setUp=fixtures.EfficiencyTests.setUp
    tearDown=fixtures.EfficiencyTests.tearDown

    def job(self):
        self.s.insert(CID,{'id':VID,'title':'Original'},'scan',True)
        return backfill.start(self.s,CID)['id']

    def test_success_is_cached_without_changing_summary_state(self):
        job=self.job()
        data={'id':VID,'channel_id':CID,'title':'Original','upload_date':'20260101'}
        with patch('backfill.gate'):
            backfill.step(self.s,job,lambda *a:data)
            done=backfill.step(self.s,job,lambda *a:self.fail('must not refetch'))
        self.assertTrue(done['all_dates_complete'])
        self.assertEqual(self.s.video(CID,VID)['state'],'pending')
        self.assertEqual(self.s.video(CID,VID)['published'],'20260101')

    def test_start_requires_a_real_date_not_only_a_title(self):
        job=self.job()
        item=self.s.db.execute('SELECT state,reason FROM metadata_items WHERE job=? AND video=?',(job,VID)).fetchone()
        self.assertEqual(dict(item), {'state':'pending','reason':None})

    def test_rate_limit_blocks_service_without_consuming_video(self):
        job=self.job()
        def fail(*a): raise ytg.MetadataFailure('youtube_rate_limited')
        with self.assertRaises(ytg.MetadataFailure): backfill.step(self.s,job,fail)
        row=self.s.db.execute('SELECT * FROM metadata_items WHERE job=?',(job,)).fetchone()
        self.assertEqual(row['state'],'pending')
        self.assertEqual(backfill.report(self.s,job)['access']['blocked'],'youtube_rate_limited')
        self.assertFalse(self.s.db.execute('SELECT 1 FROM lease WHERE run_id=?',(job,)).fetchone())

    def test_deadline_persists_and_competing_lease_is_rejected(self):
        job=self.job()
        backfill.gate(self.s)
        with self.assertRaisesRegex(ytg.Stop,'metadata_wait'): backfill.gate(self.s)
        self.s.begin('history',channel_id=CID)
        with self.assertRaisesRegex(ytg.Stop,'Another worker'): backfill.step(self.s,job)

    def test_unavailable_is_not_all_dates_complete(self):
        job=self.job()
        with patch('backfill.gate'):
            backfill.step(self.s,job,lambda *a:{'id':VID,'channel_id':CID,'title':'Original'})
        r=backfill.report(self.s,job)
        self.assertTrue(r['complete'])
        self.assertFalse(r['all_dates_complete'])
