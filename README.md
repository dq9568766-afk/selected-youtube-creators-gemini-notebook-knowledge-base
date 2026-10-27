# 把 YouTube 频道，整理成会持续更新的 Gemini 知识库 📚

**YouTube Channel → Gemini Notebook** · [中文](#中文) · [English](#english)

> 关注的博主更新太快，收藏的视频越来越多？给它一个频道链接：先整理公开历史视频，再定期发现新内容。每条视频在 Gemini 留下一篇独立总结，按频道归进笔记本，想找时能重新找到。

`指定频道 → 全量采集 → 分批总结 → 自动命名 → 笔记本归档 → 定期追更`

## 中文

### 它能帮你做什么 ✨

| 你想要 | 这个项目怎么做 |
| --- | --- |
| 一次整理喜欢的博主 | 采集频道的公开视频、Shorts 和直播回放；首次先连续总结保存，再统一改名核验，不设历史条数上限。 |
| 不再手动盯更新 | Windows 本地时间每天 05:00 检查新视频；日常先读最新清单，周一全量核对。每轮所有频道合计最多 20 次新提交。 |
| 让总结变成能检索的资料 | 一个频道对应一个 Gemini 笔记本，一条视频对应一个独立聊天。总结后命名为 `YYYYMMDD-主题词-完整原视频标题`，重新打开核对保存。 |
| 视频多也能接着做 | 按频道和视频 ID 去重，新视频优先、频道轮转；历史任务连续处理，已确认的单视频错误会跳过并留入人工核验清单，全部结束后汇报结果。历史处理不受每轮 20 条的总量限制，生成期间不逐条改名。 |
| 少留一份敏感数据 | 不用 Gemini API，不下载视频、音频、字幕，也不在本地存摘要。SQLite 只保存最小任务状态。 |

适合想持续跟进创作者、把视频内容按频道归档的人。摘要留在 **Gemini 网页笔记本**，这里的“知识库”指可重新打开的笔记本聊天；项目不会替你导出 Obsidian 文件或保证模型总结绝对准确。

### 怎么运行 🛠️

这套工具由 **Python + yt-dlp** 采集公开元数据、管理队列，由 **Codex 内置浏览器**操作 Gemini 网页。需要 Windows、Python 3.11+、yt-dlp、正在运行的 Codex，以及具备 Notebooks 与 YouTube 内容读取能力的 Gemini 账号。电脑关机、应用退出或额度耗尽时不会继续处理。

从仓库根目录开始。先在 Gemini 网页实际创建频道笔记本，再登记真实地址；已有本机任务库时直接查看状态，不要重复初始化。

```powershell
python -m unittest discover -s tests -v
python ytg.py add https://www.youtube.com/@channel
python ytg.py notebook CHANNEL_ID https://gemini.google.com/notebook/NOTEBOOK_ID
python ytg.py scan --full
python ytg.py status
```

网页工作器的实际步骤、每日巡检和恢复命令见 [工作器规程](docs/WORKER.md)。`python ytg.py next` 只领取任务，不会单独向 Gemini 发送消息。本机数据库默认在 `%LOCALAPPDATA%\youtube-gemini-channel-summarizer\tasks.sqlite3`，不在同步仓库中。

### 更少的重复操作

工作器使用 `prepare RUN` 合并领取任务和元数据检查，用 `step ... summary --next` / `verify --next` 完成当前项并准备下一项。状态更新默认只返回确认信息，排错时才用 `--verbose`。普通状态命令已自动续租，不逐条额外 renew。

日常扫描各类最新50条，有10条已知重叠时停止，无重叠则全量回退；周一仍全量核对。窗口外延迟公开的视频可能到周一才被发现。历史工作器在安全边界让出队列补做当天增量，再继续历史任务。

实测范围与性能样本见 [优化记录](docs/OPTIMIZATION.md)。

### 可靠性与验证 🔎

发送前记录任务状态。已确认的单视频错误会跳过并列入最终人工核验清单；网页提交结果不确定、额度、登录、验证码或浏览器故障仍会安全暂停，不重复发送或绕过限制。所有历史任务处理完后生成汇总报告。

已完成 **3 条视频的 Gemini 网页端到端验证**，并通过 **52 项自动测试**。历史视频长期连续处理、未来定时触发和多频道生产运行仍需观察；详见 [验证记录](docs/VALIDATION.md)。

### 来源与隐私

基于 [soumikdr/youtube-summarizer-chrome](https://github.com/soumikdr/youtube-summarizer-chrome) 的 YouTube 链接到 Gemini 工作流。保留[上游源码与 MIT 许可](vendor/youtube-summarizer-chrome/)及[来源说明](NOTICE.md)。实际批量队列使用 Codex 网页自动化，不安装上游扩展；也不跟踪上游更新。

不要把任务数据库、摘要、日志、截图、Cookie、Token、浏览器资料或私人笔记本地址提交到 Git。提交前运行 `python publish_check.py`。

## English

### Your favorite creator, organized ✨

Give the tool a YouTube channel URL. It inventories public videos, Shorts, and streams, then generates all available historical summaries before renaming and verifying the chats. Each video gets its own summary chat inside that channel's Gemini Notebook. New uploads are checked every day at 05:00 Windows local time; Monday also verifies the full channel list. A scheduled round permits up to 20 new submissions across all channels.

Chats are named `YYYYMMDD-topic-full original video title` after summarization, then reopened using their saved chat URLs to verify the title, content, and Notebook membership. Tasks are deduplicated by channel and video ID, prioritize new videos, and rotate between channels. Confirmed per-video errors are skipped and listed for manual review in the final report. Historical processing has no overall 20-video cap; account-wide quota, login, CAPTCHA, browser failures, and unknown submissions still pause safely.

**The result:** a channel-by-channel, searchable collection of Gemini chats. This project does not export Obsidian notes or guarantee that model summaries are factually correct.

### How it works 🛠️

Python and yt-dlp collect public metadata and manage the queue. The Codex built-in browser performs the Gemini web steps using its existing signed-in session. You need Windows, Python 3.11+, yt-dlp, a running Codex app, and a Gemini account with working Notebooks and YouTube access. It does not use the Gemini API, download video, audio, or subtitles, or store summaries locally.

From the repository root, create a real channel Notebook in Gemini, register its URL, and scan the channel. If a local task database already exists, check its status instead of initializing another copy.

```powershell
python -m unittest discover -s tests -v
python ytg.py add https://www.youtube.com/@channel
python ytg.py notebook CHANNEL_ID https://gemini.google.com/notebook/NOTEBOOK_ID
python ytg.py scan --full
python ytg.py status
```

See the [worker procedure](docs/WORKER.md) for web actions, scheduling, and recovery. `python ytg.py next` only reserves a task; it does not send a Gemini message by itself. Minimal SQLite task state defaults to `%LOCALAPPDATA%\youtube-gemini-channel-summarizer\tasks.sqlite3`, outside the synced repository.

### Safety and evidence 🔎

The worker records state before sending. Confirmed per-video failures are skipped for a final manual-review report. Unknown submissions, quota, login, CAPTCHA, and browser-wide failures still cause a safe pause; it does not risk duplicate chats or bypass limits.

**Three videos have passed live Gemini end-to-end checks**, and **52 automated tests pass**. Sustained historical processing, future scheduled triggers, and production use across multiple channels still need observation. See the [validation record](docs/VALIDATION.md).

This project builds on [soumikdr/youtube-summarizer-chrome](https://github.com/soumikdr/youtube-summarizer-chrome). The [upstream source and MIT license](vendor/youtube-summarizer-chrome/) are retained, with [attribution](NOTICE.md). The production queue uses Codex web automation rather than installing the original extension. Never commit task databases, summaries, logs, screenshots, cookies, tokens, browser data, or private Notebook URLs. Run `python publish_check.py` before publishing.
