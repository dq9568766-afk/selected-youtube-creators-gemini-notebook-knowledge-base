import json
import tempfile
from pathlib import Path
import unittest
from unittest import mock

import ytg


CID = 'UCX8KQ5xQlm0MnZkmHO7CBDw'
VID = 'IN28qWHF1s0'
BOOK = 'https://gemini.google.com/notebook/11111111-1111-1111-1111-111111111111'


class ChannelConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / 'tasks.sqlite3'
        self.store = ytg.Store(self.db)
        self.store.db.execute(
            'INSERT INTO channels(id,url,name,notebook,initialized) VALUES(?,?,?,?,1)',
            (CID, 'https://www.youtube.com/channel/' + CID, 'fixture', BOOK),
        )
        self.store.insert(
            CID,
            {'id': VID, 'title': '原标题', 'upload_date': '20261026'},
            'scan',
            True,
        )
        self.store.db.execute(
            'UPDATE videos SET metadata_checked=1 WHERE channel_id=? AND id=?',
            (CID, VID),
        )

    def tearDown(self):
        self.store.db.close()
        self.tmp.cleanup()

    def test_channel_choices_are_persisted_and_reported(self):
        result = self.store.configure_channel(
            CID,
            rename_strategy='extension',
            title_format='original',
            membership_status='no',
        )
        self.assertEqual(result['rename_strategy'], 'extension')
        self.assertEqual(result['title_format'], 'original')
        self.assertEqual(result['membership_status'], 'no')
        self.assertEqual(result['config_confirmed'], 1)
        self.assertEqual(self.store.channel_config(CID)['complete'], True)

    def test_title_formats_follow_channel_configuration(self):
        video = self.store.video(CID, VID)
        self.store.configure_channel(CID, title_format='original')
        video = self.store.video(CID, VID)
        self.assertEqual(ytg.title_for(video), '原标题')

        self.store.configure_channel(CID, title_format='topic')
        video = self.store.video(CID, VID)
        self.assertEqual(ytg.title_for(video, '主题'), '主题-原标题')

        self.store.configure_channel(CID, title_format='date-topic')
        video = self.store.video(CID, VID)
        self.assertEqual(ytg.title_for(video, '主题'), '20261026-主题-原标题')

    def test_channel_config_rejects_unknown_values(self):
        with self.assertRaises(ytg.Stop):
            self.store.configure_channel(CID, title_format='date')
        with self.assertRaises(ytg.Stop):
            self.store.configure_channel(CID, membership_status='maybe')

    def test_workbuddy_preflight_is_read_only_and_explains_browser_gate(self):
        result = ytg.preflight(self.store, CID)
        self.assertFalse(result['ready'])
        self.assertEqual(result['channel']['id'], CID)
        self.assertIn('browser', result['requirements'])
        self.assertTrue(result['requirements']['browser']['required'])
        self.assertFalse(result['database']['write_performed'])

    def test_channel_config_cli_can_complete_setup(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / 'tasks.sqlite3'
            store = ytg.Store(db)
            store.db.execute(
                'INSERT INTO channels(id,url,name,notebook,initialized) VALUES(?,?,?,?,1)',
                (CID, 'https://www.youtube.com/channel/' + CID, 'fixture', BOOK),
            )
            store.db.close()
            with mock.patch('sys.stdout') as stdout:
                ytg.main([
                    '--db', str(db), 'channel-config', CID,
                    '--rename-strategy', 'serial',
                    '--title-format', 'topic',
                    '--membership', 'yes',
                ])
            payload = json.loads(''.join(call.args[0] for call in stdout.write.call_args_list))
            self.assertEqual(payload['config_confirmed'], 1)

    def test_cli_does_not_start_a_run_before_setup_is_confirmed(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / 'tasks.sqlite3'
            store = ytg.Store(db)
            store.db.execute(
                'INSERT INTO channels(id,url,name,notebook,initialized) VALUES(?,?,?,?,1)',
                (CID, 'https://www.youtube.com/channel/' + CID, 'fixture', BOOK),
            )
            store.db.close()
            with mock.patch('sys.stdout') as stdout:
                code = ytg.main(['--db', str(db), 'begin', '--mode', 'history', '--channel', CID])
            payload = json.loads(''.join(call.args[0] for call in stdout.write.call_args_list))
            self.assertEqual(code, 2)
            self.assertIn('setup is incomplete', payload['stopped'])


if __name__ == '__main__':
    unittest.main()
