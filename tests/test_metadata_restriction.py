from yt_dlp.utils import DownloadError
from unittest.mock import patch
import unittest
import test_efficiency as fixtures
from test_efficiency import CID, VID
import ytg


class MetadataRestrictionTests(unittest.TestCase):
    setUp = fixtures.EfficiencyTests.setUp
    tearDown = fixtures.EfficiencyTests.tearDown

    def video(self):
        self.s.insert(CID, {'id': VID, 'title': 'Members video'}, 'scan', True)
    def test_members_only_is_deferred(self):
        self.video()
        run = self.s.begin('history')['id']
        def restricted(url, flat):
            raise DownloadError('Join this channel to get access to members-only content like this video')
        item = ytg.prepare(self.s, run, restricted)
        self.assertEqual(item['state'], 'deferred')
        self.assertEqual(self.s.video(CID, VID)['reason'], 'metadata_unavailable')
        self.assertIsNone(self.s.video(CID, VID)['owner'])

    def test_network_failure_keeps_reservation(self):
        self.video()
        run = self.s.begin('history')['id']
        def unavailable(url, flat):
            raise DownloadError('Connection timed out')
        with self.assertRaises(ytg.Stop):
            ytg.prepare(self.s, run, unavailable)
        self.assertEqual(self.s.video(CID, VID)['state'], 'reserved')

    def test_service_errors_keep_item_and_do_not_fetch_next(self):
        for message, category in [
            ('HTTP Error 429: Too Many Requests', 'youtube_rate_limited'),
            ("Sign in to confirm you are not a bot", 'youtube_auth_required'),
            ('Connection timed out', 'metadata_network_error'),
            ('Unknown extractor problem', 'metadata_error'),
            ('Video unavailable; HTTP Error 429', 'youtube_rate_limited'),
        ]:
            with self.subTest(category=category):
                self.s.db.execute('DELETE FROM videos')
                self.video()
                run = self.s.begin('history')['id']
                calls = []
                def fail(url, flat):
                    calls.append(url)
                    raise DownloadError(message)
                with self.assertRaisesRegex(ytg.MetadataFailure, category):
                    ytg.prepare(self.s, run, fail)
                row = self.s.video(CID, VID)
                self.assertEqual(row['state'], 'reserved')
                self.assertIsNone(row['reason'])
                self.assertEqual(len(calls), 1)
                self.s.cleanup(run)

    def test_warning_aborts_extractor_before_fallback_requests(self):
        calls = []
        class FakeDL:
            def __init__(self, opts):
                self.opts = opts
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def extract_info(self, url, download=False, process=True):
                self.opts['logger'].warning('Unable to download webpage: HTTP Error 429')
                calls.append('fallback')
        with patch('yt_dlp.YoutubeDL', FakeDL):
            with self.assertRaisesRegex(ytg.MetadataFailure, 'youtube_rate_limited'):
                ytg.extract('https://www.youtube.com/watch?v='+VID)
        self.assertEqual(calls, [])

    def test_scan_stops_before_remaining_tabs_on_rate_limit(self):
        calls = []
        def fail(url, flat, limit=None):
            calls.append(url)
            raise DownloadError('HTTP Error 429')
        with self.assertRaisesRegex(ytg.MetadataFailure, 'youtube_rate_limited'):
            ytg.scan(self.s, CID, fetch=fail)
        self.assertEqual(len(calls), 1)
        self.assertIsNone(self.s.channel(CID)['last_full'])

    def test_incremental_scan_cache_survives_time_and_repairs_missing_date(self):
        self.s.insert(CID, {'id':VID,'title':'Original'}, 'first', False)
        self.s.insert(CID, {'id':VID,'title':'Original','upload_date':'20261026'}, 'second', False)
        self.s.db.execute('UPDATE videos SET metadata_checked=1')
        run = self.s.begin('incremental')['id']
        def unexpected(*args): self.fail('Complete scan cache must not fetch')
        row = ytg.prepare(self.s, run, unexpected)
        self.assertEqual(row['published'], '20261026')
        self.s.transition(run,CID,VID,'arm')

    def test_incomplete_scan_preserves_date_but_changed_title_needs_lookup(self):
        self.s.insert(CID, {'id':VID,'title':'Original','upload_date':'20261026'}, 'first', True)
        self.s.insert(CID, {'id':VID,'title':'Original'}, 'second', True)
        self.assertTrue(ytg.metadata_fresh(self.s.video(CID,VID)))
        self.s.insert(CID, {'id':VID,'title':'Changed'}, 'third', True)
        self.assertFalse(ytg.metadata_fresh(self.s.video(CID,VID)))
        self.assertEqual(self.s.video(CID,VID)['published'], '20261026')

    def test_scan_updates_do_not_modify_submitted_metadata(self):
        self.s.insert(CID, {'id':VID,'title':'Original','upload_date':'20261026'}, 'first', True)
        run = self.s.begin('history')['id']
        self.s.reserve(run)
        self.s.transition(run,CID,VID,'arm')
        self.s.insert(CID, {'id':VID,'title':'Changed','upload_date':'20261027'}, 'second', True)
        row = self.s.video(CID,VID)
        self.assertEqual((row['title'],row['published']), ('Original','20261026'))
