from yt_dlp.utils import DownloadError
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
