from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import publish_check

class PublishTests(unittest.TestCase):
    def test_sensitive_paths_are_rejected(self):
        paths=['runtime/tasks.sqlite3','tests/cookies.txt','docs/output.log','work/profile.json','.env']
        self.assertEqual(len(publish_check.check(paths)),len(paths))

    def test_private_mapping_and_credentials_are_rejected_without_echoing_values(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            key='gh'+'p_'+'x'*36
            private='https://gemini.google.com/'+'app/'+'1234567890abcdef'
            (root/'ytg.py').write_text(key+'\n'+private,encoding='utf-8')
            with patch.object(publish_check,'ROOT',root):
                result=publish_check.check(['ytg.py'])
            self.assertEqual(len(result),2)
            self.assertNotIn(key,str(result))

    def test_binary_files_are_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); (root/'ytg.py').write_bytes(b'\x00')
            with patch.object(publish_check,'ROOT',root):
                self.assertEqual(len(publish_check.check(['ytg.py'])),1)

if __name__=='__main__': unittest.main()
