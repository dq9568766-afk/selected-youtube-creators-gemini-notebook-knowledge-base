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
            self.assertIn('Gemini 生成、发送、恢复、普通核验和历史改名扩展统一使用该外部 Chrome', skill)
            self.assertNotIn('不能替代内置浏览器发送或恢复 Gemini 任务', skill)
            self.assertIn('summary` and `verify` are actions of `step`', skill)
            self.assertIn('Never invent `summary RUN` or `verify RUN` commands', skill)
            self.assertIn('Codex DOM-first browser actions', skill)
            self.assertIn('WorkBuddy DOM-first browser actions', skill)
            self.assertIn('WorkBuddy default browser adapter', skill)
            self.assertIn('mcp__browser__*', skill)
            self.assertIn('WorkBuddy input fallback', skill)
            self.assertIn('WorkBuddy uses its designated external Chrome execution browser', skill)
            self.assertIn('Do not switch to external Chrome for Gemini generation', skill)
            self.assertIn('Codex default browser plugin', skill)
            self.assertTrue((output / 'references' / 'WORKER.md').is_file())
            self.assertTrue((output / 'references' / 'AGENT_ADAPTERS.md').is_file())
            self.assertTrue((output / 'scripts' / 'ytg.py').is_file())


if __name__ == '__main__':
    unittest.main()
