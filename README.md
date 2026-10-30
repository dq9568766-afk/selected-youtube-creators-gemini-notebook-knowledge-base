# YouTube 创作者视频总结 AI Skill｜Gemini 笔记本与 NotebookLM 笔记

**一个可安装到 AI 智能体中的 Skill：采集指定 YouTube 频道，逐条总结、命名并归档到 Gemini。**

[中文](#中文) · [English](#english) · [版本发布](https://github.com/dq9568766-afk/selected-youtube-creators-gemini-notebook-knowledge-base/releases)

`指定频道 → 公开元数据采集 → Gemini 逐条总结 → 改名核验 → 笔记本归档`

## 中文

### 这是什么

这是一个 **AI Skill（智能体技能）**，由技能说明、Python 队列程序和工作器规程组成。安装后，由具备文件访问、终端和浏览器操作能力的 AI 智能体执行；Python 管理任务状态，智能体操作已登录的 Gemini 网页。

每个频道对应一个登记的 **Gemini 笔记本（可用于后续整理 notebooklm笔记）**，每条视频对应一个独立总结聊天。标题统一为 `YYYYMMDD-主题词-完整原视频标题`，主题词从有效总结中提取。

**这里要区分保存位置与后续用途：**当前程序保存并核验的是 Gemini 网页 Notebooks 中的聊天，不会自动将聊天导出或转换为 NotebookLM 来源。本文中的 notebooklm笔记指后续整理用途，不表示项目已验证 NotebookLM 中的导入、同步或来源关联；是否存在账号侧联动也不能代替逐项核验。

### 可以做什么

| 功能 | 实际行为 |
| --- | --- |
| 整理频道历史 | 读取公开视频、Shorts 和直播清单；按频道和视频 ID 去重，复用完整历史元数据。 |
| 逐条总结归档 | 一个工作标签串行执行，每条生成后立即改名、核验，再进入下一条。 |
| 中断后续跑 | SQLite 记录最小任务状态、租约和聊天关联，优先恢复已有任务，避免重复提交。 |
| 失败后置 | 用户授权的一次性历史采集可用 collect-first：单条失败明确延期，首轮不搜索、不重发，最终列出全部延期项。默认策略仍为 strict。 |
| 定期追更 | 提供每日 05:00 增量规程和调度护栏，周一全量核对；需要另行配置智能体调度，安装 Skill 不会自动创建定时任务。 |
| 控制节奏 | 每20条核验成功或明确延期后休息至少120秒；计数和休息起点跨续跑保留。增量每轮最多20次提交，历史无总条数上限。 |

正常条目在当前页面核对准确标题、完整总结、原视频链接、账号与 Notebook 归属。操作报错、页面中断、标题未更新或归属不明时，先登记必须重开的标记，再在同一工作标签重新打开已有聊天核验。无法核验不能计为成功。

### 通过创建技能的方式安装

推荐让智能体根据本仓库创建或接入本地技能，保留原有脚本和规程，而不是只复制一段提示词。以 Codex 为例，可将下面的指令直接发给它：

```text
使用 $skill-creator，根据下面的仓库创建或接入本地 AI Skill：
https://github.com/dq9568766-afk/selected-youtube-creators-gemini-notebook-knowledge-base

技能名：youtube-creator-gemini-notebook-knowledge-base

先获取完整仓库并阅读根目录 SKILL.md、AGENTS.md、docs/WORKER.md 和 docs/AGENT_ADAPTERS.md。
以仓库根目录作为唯一技能源，按当前平台支持的方式建立技能入口；已有安装时复用，不覆盖用户修改。
保留 ytg.py、publish_check.py、docs/、tests/ 和其他依赖文件，不要仅复制 SKILL.md。
检查 Python 3.11+、yt-dlp，以及当前平台能否通过已登录会话操作 Gemini 网页。
完成后报告技能位置、调用方式和缺少的能力。
本次仅安装和检查，不采集频道、不提交视频、不创建定时任务；不重建或复制已有任务数据库。
```

Codex 的技能创建入口是 `$skill-creator`，参见 [OpenAI 官方技能创建说明](https://developers.openai.com/plugins/build/skills)。其他平台使用其自身的创建技能入口，保留同样的项目文件与执行边界；平台能力与安装入口可能变化，以当前文档为准。

仓库已经提供规范的 [SKILL.md](SKILL.md)，安装的重点是让平台正确识别并访问完整项目，无需重新编写队列逻辑。适配要求见 [智能体适配说明](docs/AGENT_ADAPTERS.md)。安装成功不代表终端、浏览器和 Gemini 登录权限已经就绪。

### 环境与首次使用

当前本地部署面向 Windows，需要 Python 3.11+、yt-dlp，以及能访问本地文件、运行命令并操作已登录 Gemini 网页的智能体。Gemini 账号须实际具备 Notebooks 与 YouTube 内容读取能力；功能可用性随账号和服务变化。

在包含 `ytg.py` 的仓库根目录安装 Python 依赖：

```powershell
python -m pip install -e .
```

安装完成后，向智能体明确指定频道和采集范围，例如：

```text
使用 $youtube-creator-gemini-notebook-knowledge-base，整理这个频道：
https://www.youtube.com/@channel
请一次性采集公开历史视频，允许单条异常后置，使用 collect-first。
先检查现有任务状态、租约和登记的 Notebook，核对网页账号与笔记本归属后再开始。
每条总结完成后立即改名核验；结束时列出真实生成数、核验数和全部延期项。
```

`@channel` 是占位符，请换成真实频道。已有任务数据库时先检查状态；没有数据库时，由智能体按规程登记频道。频道与 Notebook 的命令示例：

```powershell
python ytg.py add https://www.youtube.com/@channel
python ytg.py notebook CHANNEL_ID https://gemini.google.com/notebook/NOTEBOOK_ID
python ytg.py status
```

`CHANNEL_ID` 和 `NOTEBOOK_ID` 必须替换为实际核实的值。优先使用已登记 Notebook；缺失或换账号时按规程核验，不能任意覆盖映射。详细运行步骤以 [WORKER.md](docs/WORKER.md) 为准，不应跳过状态机直接发送。

### NotebookLM 笔记的后续用途

按 [SKILL.md](SKILL.md) 所述，将已核验总结另行整理为 NotebookLM 来源后，可以围绕创作者的多个视频提问、比较观点和时间变化，或生成学习与简报材料。使用前须确认来源确已导入，并检查引用与原视频；这些属于后续用途，不是当前队列自动完成的功能。

### 运行边界与隐私

- yt-dlp 只读公开元数据，不下载视频、音频或字幕；Gemini 总结通过已登录网页生成，不调用 Gemini API。
- 摘要留在 Gemini，不在本地保存正文、网页快照或日志。唯一任务数据库为 `%LOCALAPPDATA%\youtube-gemini-channel-summarizer\tasks.sqlite3`，不放进同步仓库，不复制或重建已有库。
- 登录、验证码、配额和确认的账号或浏览器会话故障会全局暂停；其他暂停按 WORKER 的频道范围规则处理。暂停与有效租约不代表后台正在运行。
- 历史任务由同一聊天中的连续执行推进，不使用周期 heartbeat。每日增量单独配置；电脑关机、应用退出或能力不可用时不能保证继续处理。
- 完成以频道报告 `complete=true` 为准，明确延期不等于成功。首轮失败项须用户另行指定后才补做。

### 验证与项目文件

2026-10-30 发布前的 **92项隔离自动测试通过**，并通过发布检查。已有 Gemini 网页实测见验证记录；这不代表当前流程已完成大规模长期网页验证，也不保证模型摘要准确。

| 文件 | 用途 |
| --- | --- |
| [SKILL.md](SKILL.md) | 技能入口、能力与数据边界。 |
| [AGENTS.md](AGENTS.md) | 项目约定。 |
| [docs/WORKER.md](docs/WORKER.md) | 队列行为的唯一详细规程。 |
| [docs/AGENT_ADAPTERS.md](docs/AGENT_ADAPTERS.md) | 不同智能体的能力与浏览器适配。 |
| [docs/AUTOMATION_PROMPT.md](docs/AUTOMATION_PROMPT.md) | 历史执行与每日增量提示。 |
| [docs/VALIDATION.md](docs/VALIDATION.md) | 按日期记录的测试与网页实测范围。 |
| [docs/OPTIMIZATION.md](docs/OPTIMIZATION.md) | 历史优化记录与性能限制。 |

修改后使用隔离测试，并在提交前检查实际发布路径：

```powershell
python -m unittest discover -s tests -v
python publish_check.py
```

发布检查读取 Git 暂存区中的已跟踪文件；不要提交任务数据库、摘要、凭据、浏览器资料或私人 Notebook 地址。

### 来源与许可

参考 [soumikdr/youtube-summarizer-chrome](https://github.com/soumikdr/youtube-summarizer-chrome) 的 YouTube 链接到 Gemini 工作流，保留[上游源码与 MIT 许可](vendor/youtube-summarizer-chrome/)及[来源说明](NOTICE.md)。实际批量队列由智能体网页操作驱动，无需安装上游扩展。

## English

### An AI Skill for creator archives

This repository is an **AI Skill**, with instructions, Python queue code, and worker procedures. An agent collects public YouTube channel metadata and operates an existing signed-in Gemini web session. Each video receives an independent chat in its registered Gemini Notebook, then is renamed and verified before the next video.

Gemini chats are the verified output of this workflow. **NotebookLM notes are a downstream use:** importing summaries as NotebookLM sources and verifying that association are separate tasks. The queue does not automatically export chats or verify a NotebookLM sync.

### Install through skill creation

In Codex, ask `$skill-creator` to create or connect the skill from this repository. Keep the repository root as the canonical source and retain its scripts, documentation, and tests. Reuse an existing installation and preserve user changes; copying only SKILL.md is insufficient. See [OpenAI's skill creation guide](https://developers.openai.com/plugins/build/skills) and [agent adapters](docs/AGENT_ADAPTERS.md).

```text
Use $skill-creator to create or connect the local skill
`youtube-creator-gemini-notebook-knowledge-base` from:
https://github.com/dq9568766-afk/selected-youtube-creators-gemini-notebook-knowledge-base
Read SKILL.md, AGENTS.md, docs/WORKER.md, and docs/AGENT_ADAPTERS.md.
Keep the complete repository as the canonical source. Verify Python 3.11+,
yt-dlp, file and shell access, and signed-in Gemini browser capabilities.
Only install and check capabilities; do not process videos, schedule tasks,
or recreate or copy an existing task database.
```

The current local deployment targets Windows. Install dependencies with `python -m pip install -e .` from the repository root, then explicitly request collection for a real channel. Skill installation alone does not configure browser access, sign-in, or daily scheduling.

### Workflow and limits

- Default policy is strict. Explicitly authorized one-pass historical collection may use channel-scoped collect-first, recording item failures as deferrals without first-pass retries.
- Use one working tab and process videos serially. Verify title, full summary, original video link, account, and Notebook membership. Anomalies require reopening the saved chat before verification.
- Rest at least 120 seconds after every 20 settled items; counts persist across restarts. Incremental rounds allow up to 20 submissions; history has no overall item cap. Daily 05:00 execution requires separately configured scheduling.
- yt-dlp reads public metadata only. No media or subtitle downloads, Gemini API calls, or local summary storage. Keep the canonical database outside the repository.
- Completion requires the channel report to return `complete=true`. Deferrals are reported separately from successes. Follow [WORKER.md](docs/WORKER.md) for pause, recovery, pacing, and cleanup rules.

The release validation on 2026-10-30 passed 92 isolated tests and the publish check. See [validation records](docs/VALIDATION.md) for the actual scope of live Gemini checks and remaining limits. Long-term stability and summary accuracy are not guaranteed.

Based on the upstream [YouTube-to-Gemini workflow](https://github.com/soumikdr/youtube-summarizer-chrome), with [source and MIT license retained](vendor/youtube-summarizer-chrome/) and [attribution](NOTICE.md).
