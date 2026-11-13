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

    def test_unknown_membership_cannot_confirm_channel_setup(self):
        result = self.store.configure_channel(
            CID,
            rename_strategy='serial',
            title_format='original',
            membership_status='unknown',
        )
        self.assertEqual(result['config_confirmed'], 0)
        self.assertFalse(self.store.channel_config(CID)['complete'])

    def test_workbuddy_preflight_is_read_only_and_explains_browser_gate(self):
        result = ytg.preflight(self.store, CID)
        self.assertFalse(result['ready'])
        self.assertEqual(result['channel']['id'], CID)
        self.assertIn('browser', result['requirements'])
        self.assertTrue(result['requirements']['browser']['required'])
        self.assertFalse(result['database']['write_performed'])

    def test_preflight_requires_browser_creation_before_notebook_registration(self):
        self.store.db.execute('UPDATE channels SET notebook=NULL WHERE id=?', (CID,))
        result = ytg.preflight(self.store, CID)
        self.assertFalse(result['requirements']['notebook']['satisfied'])
        self.assertFalse(result['notebook_setup']['registered'])
        self.assertIn('创建并核验频道 Notebook', result['notebook_setup']['next_action'])

    def test_date_topic_history_is_blocked_until_date_backfill_is_settled(self):
        self.store.configure_channel(
            CID,
            rename_strategy='serial',
            title_format='date-topic',
            membership_status='no',
        )
        self.store.db.execute('UPDATE videos SET published=NULL WHERE channel_id=?', (CID,))
        status = self.store.date_backfill_status(CID)
        self.assertTrue(status['required'])
        self.assertEqual(status['missing'], 1)
        self.assertTrue(status['blocked'])
        with self.assertRaisesRegex(ytg.Stop, 'date_backfill_required'):
            self.store.begin('history', channel_id=CID)

    def test_completed_backfill_with_unavailable_dates_does_not_block_other_items(self):
        import backfill

        self.store.configure_channel(
            CID,
            rename_strategy='serial',
            title_format='date-topic',
            membership_status='no',
        )
        self.store.db.execute('UPDATE videos SET published=NULL WHERE channel_id=?', (CID,))
        job = backfill.start(self.store, CID)['id']
        self.store.db.execute(
            "UPDATE metadata_items SET state='unavailable', reason='date_not_public' WHERE job=?",
            (job,),
        )
        self.store.db.execute("UPDATE metadata_jobs SET status='complete' WHERE id=?", (job,))
        status = self.store.date_backfill_status(CID)
        self.assertFalse(status['blocked'])
        self.assertFalse(status['all_dates_complete'])
        self.store.begin('history', channel_id=CID)

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

    def test_cli_does_not_start_processing_without_notebook(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / 'tasks.sqlite3'
            store = ytg.Store(db)
            store.db.execute(
                'INSERT INTO channels(id,url,name,initialized,rename_strategy,title_format,membership_status,config_confirmed) '
                'VALUES(?,?,?,?,?,?,?,1)',
                (CID, 'https://www.youtube.com/channel/' + CID, 'fixture', 1,
                 'serial', 'original', 'no'),
            )
            store.db.close()
            with mock.patch('sys.stdout') as stdout:
                code = ytg.main(['--db', str(db), 'begin', '--mode', 'history', '--channel', CID])
            payload = json.loads(''.join(call.args[0] for call in stdout.write.call_args_list))
            self.assertEqual(code, 2)
            self.assertIn('Notebook setup is incomplete', payload['stopped'])

    def test_notebook_registration_enables_tracking_before_history_scan(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / 'tasks.sqlite3'
            store = ytg.Store(db)
            store.db.execute(
                'INSERT INTO channels(id,url,name,tracking) VALUES(?,?,?,0)',
                (CID, 'https://www.youtube.com/channel/' + CID, 'fixture'),
            )
            store.db.close()
            with mock.patch('sys.stdout') as stdout:
                code = ytg.main(['--db', str(db), 'notebook', CID, BOOK])
            self.assertEqual(code, 0)
            result = ytg.Store(db)
            row = result.db.execute(
                'SELECT notebook,tracking FROM channels WHERE id=?', (CID,)
            ).fetchone()
            self.assertEqual(row['notebook'], BOOK)
            self.assertEqual(row['tracking'], 1)
            result.db.close()


if __name__ == '__main__':
    unittest.main()
