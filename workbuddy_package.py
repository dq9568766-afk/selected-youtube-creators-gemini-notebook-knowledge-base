"""Build a WorkBuddy-compatible skill directory from the canonical project."""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil


ROOT = Path(__file__).resolve().parent
FRONTMATTER = """---
name: dq-youtube-creator-gemini-notebook-knowledge-base
display_name: YouTube创作者 Gemini 笔记本知识库
display_name_en: YouTube Creator Gemini Notebook Knowledge Base
description: Collect public YouTube metadata and organize one verified Gemini summary chat per video.
description_zh: 读取 YouTube 创作者公开视频元数据，在已登录 Gemini 网页中逐条总结、命名并核验归档。
description_en: Collect public YouTube metadata and organize one verified Gemini summary chat per video.
version: 0.2.0
author: DQ
examples_zh:
  - 使用该技能整理一个频道的公开历史视频，并允许单条失败后置。
  - 查看指定频道的历史处理进度和未完成项目。
examples_en:
  - Collect a creator's public history and defer item-level failures.
  - Check one channel's progress and unfinished items.
---
"""

REFERENCE_FILES = {
    'AGENTS.md': 'PROJECT_RULES.md',
    'docs/WORKER.md': 'WORKER.md',
    'docs/AGENT_ADAPTERS.md': 'AGENT_ADAPTERS.md',
    'docs/HISTORY_RENAME.md': 'HISTORY_RENAME.md',
    'docs/AUTOMATION_PROMPT.md': 'AUTOMATION_PROMPT.md',
}
SCRIPT_FILES = ('ytg.py', 'backfill.py', 'history_rename.py')


def build(output: Path) -> Path:
    """Create or update a package without touching the task database."""
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    (output / 'references').mkdir(exist_ok=True)
    (output / 'scripts').mkdir(exist_ok=True)

    source_skill = (ROOT / 'SKILL.md').read_text(encoding='utf-8')
    body = source_skill.split('---', 2)[-1].lstrip()
    for source, target in {
        'AGENTS.md': '@references/PROJECT_RULES.md',
        'docs/WORKER.md': '@references/WORKER.md',
        'docs/AGENT_ADAPTERS.md': '@references/AGENT_ADAPTERS.md',
        'docs/HISTORY_RENAME.md': '@references/HISTORY_RENAME.md',
        'Browser-first gate (Codex)': 'Browser-first gate (WorkBuddy)',
        'Codex built-in in-app browser': 'approved WorkBuddy browser',
        'In Codex, read': 'In WorkBuddy, read',
        'Codex Goal capability': 'persistent task objective',
    }.items():
        body = body.replace(source, target)
    adapter_notice = """# WorkBuddy 运行入口

这是从项目 canonical 源生成的 WorkBuddy 包。先读取 `@references/PROJECT_RULES.md`、
`@references/WORKER.md` 和 `@references/AGENT_ADAPTERS.md`。只有 WorkBuddy 当前会话
确实提供可操作、可读回的 Gemini 内置浏览器页面时，才允许执行生成队列命令；9222/CDP、
Cookie、Token、浏览器配置文件和私有接口都不构成合规能力。外部 Windows 浏览器只在
显式启用历史改名扩展时使用，不能替代内置浏览器发送或恢复 Gemini 任务。

参考资料：`@references/PROJECT_RULES.md`、`@references/WORKER.md`、
`@references/AGENT_ADAPTERS.md`、`@references/HISTORY_RENAME.md`。

"""
    (output / 'SKILL.md').write_text(FRONTMATTER + adapter_notice + body, encoding='utf-8')

    for source, target in REFERENCE_FILES.items():
        shutil.copy2(ROOT / source, output / 'references' / target)
    for name in SCRIPT_FILES:
        shutil.copy2(ROOT / name, output / 'scripts' / name)

    extension_source = ROOT / 'extensions' / 'history-renamer'
    extension_target = output / 'extensions' / 'history-renamer'
    shutil.copytree(extension_source, extension_target, dirs_exist_ok=True)
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(argv)
    print(build(args.output))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
