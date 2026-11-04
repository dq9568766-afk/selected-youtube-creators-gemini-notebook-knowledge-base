import tempfile
from pathlib import Path
import unittest

import workbuddy_package


class WorkBuddyPackageTests(unittest.TestCase):
    def test_package_contains_required_frontmatter_and_references(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'skill'
            workbuddy_package.build(output)
            skill = (output / 'SKILL.md').read_text(encoding='utf-8')
            for field in (
                'display_name:', 'display_name_en:', 'description_zh:',
                'description_en:', 'version:', 'author:',
            ):
                self.assertIn(field, skill)
            self.assertIn('@references/WORKER.md', skill)
            self.assertNotIn('docs/WORKER.md', skill)
            self.assertNotIn('Codex built-in in-app browser', skill)
            self.assertIn('外部 Windows 浏览器只在', skill)
            self.assertIn('不能替代内置浏览器发送或恢复 Gemini 任务', skill)
            self.assertTrue((output / 'references' / 'WORKER.md').is_file())
            self.assertTrue((output / 'references' / 'AGENT_ADAPTERS.md').is_file())
            self.assertTrue((output / 'scripts' / 'ytg.py').is_file())


if __name__ == '__main__':
    unittest.main()
