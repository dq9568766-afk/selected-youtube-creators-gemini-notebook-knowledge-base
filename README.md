# YouTube 频道 → Gemini 网页笔记本 | YouTube Channels → Gemini Notebooks

[中文](#中文) · [English](#english)

## 中文

Windows 本地元数据队列 + 已安装 Codex 浏览器自动化工作器。
不使用 Gemini API，不下载视频/字幕，本地不保存摘要。
每个频道对应 Gemini 原生笔记本，每条视频独立聊天；总结完成后改名为
`YYYYMMDD-不超过5个汉字主题词-完整原视频标题`，刷新笔记本并重新打开确认后才标为完成。
指定博主的公开内容按频道归档在 Gemini，便于逐条查找并沉淀个人知识库；摘要不额外导出到本地知识库。

### 特点

- **指定博主与批量总结：**添加频道链接，首次全量采集 `/videos`、`/shorts`、`/streams`，按频道和视频 ID 去重，连续分批总结历史视频。
- **增量自动更新：**Windows 本地时间每周一、周四 08:30 检查并总结新视频；周一校验完整清单。每轮所有频道合计最多 20 次新提交，新视频优先，多个频道轮转。
- **自动命名与笔记本归档：**总结完成后选 1–5 个汉字主题词，保留完整原标题；刷新笔记本并重新打开，核对成功才标为完成。
- **命令驱动、断点续跑：**本地 CLI 管理采集和队列，Codex 工作器操作 Gemini 网页。不确定的提交先核对已有聊天，无法确认便暂停，避免重复发送。

## 使用条件

- Windows、Python 3.11+、yt-dlp；当前环境已安装，无需再次安装。
- Codex 桌面应用、已连接的 Browser Automation / Computer Use、Chrome 及 Gemini 登录。
- Gemini 账号实际具备原生 Notebooks、YouTube读取、聊天自动保存能力。账号/地区/额度可能变化，以实际页面为准。
- 电脑保持开机、联网，Codex 和 Chrome 正常运行。不能承诺电脑关机或应用退出后继续处理。

这是复用 Codex 的本机工作器，不是脱离 Codex 单独执行的 Python 浏览器机器人。
无需 Gemini API key，不读取浏览器登录资料，也不安装上游扩展。

## 初始化

在本仓库目录执行：

```powershell
python -m unittest discover -s tests -v
python ytg.py add https://www.youtube.com/@wuyuesanren
python ytg.py notebook CHANNEL_ID https://gemini.google.com/notebook/NOTEBOOK_ID
python ytg.py scan --full
python ytg.py status
```

先由工作器实际创建笔记本、核对账号和保存能力，再登记返回的真实笔记本地址。
当前用户频道已在本机登记；不要重复初始化另一份数据库。私人笔记本映射和任务状态仅在本机 `%LOCALAPPDATA%\youtube-gemini-channel-summarizer\tasks.sqlite3` 中，不随百度同步盘或 GitHub 上传。可用 `python ytg.py --db <数据库路径> status` 显式指定其他非同步位置。

## 批次与调度

- 首次：先完整分页枚举 `/videos`、`/shorts`、`/streams`，存入 SQLite，再自动连续批次总结历史。每批20条，历史没有总量20条上限；受真实额度和故障暂停约束。
- 后续：Windows 本地时间每周一及周四08:30增量检查并总结，每次所有频道合计最多20次新提交；周一校验完整公开清单。
- 目前增量检查也枚举完整列表，保证不依赖容易漏直播转存的日期截止/前N条截断；已存在ID仅更新元数据，不新建任务。周一额外标记清单缺失，永不自动删除网页聊天。
- 新发现视频优先，频道轮转；每条领取后完成再领下一条。未完成保存验证的旧任务先恢复。
- 定时任务由当前聊天的本地 Codex 自动化唤醒，读取 [工作器规程](docs/WORKER.md) 执行 CLI 和网页操作。历史续跑在历史完成后停用，每周任务继续。
- 定时任务只改变运行状态，绝不因此自动提交或推送代码。具体已创建任务以应用自动化列表为准。

手动执行工作器：按照 [docs/WORKER.md](docs/WORKER.md) 的状态机逐步执行。
CLI `begin/next/metadata/step/end` 已实现；仅运行 `next` 不会向 Gemini 发消息。

## 不重复的边界

数据库唯一键是 `(channel_id,video_id)`，聊天地址也唯一。
点击发送前先持久化 submitting，提交计入本轮上限；点击后立即绑定实际聊天地址。
崩溃或超时后不回退为 pending。若无聊天地址，工作器查原标签、笔记本清单与聊天搜索，核对完整任务标识后才能绑定。
网页没有可用的事务/幂等键，因此不能声称数学意义的 exactly-once：找不到可信证据时选择暂停，绝不冒险重发。

查看/暂停：

```powershell
python ytg.py status
python ytg.py pause quota
```

恢复仅在用户确认故障/限额已解除后：`python ytg.py resume --confirmed`。
定时任务禁止自动恢复。私有/删除/无可读内容视频保留任务原因及已有聊天映射，不生成虚假摘要。

## GitHub 发布规则

目标为用户自己的 Private 仓库 `youtube-gemini-channel-summarizer`。
保留上游 MIT 许可和来源，不设置 upstream，不自动同步原作者更新。
功能单元测试通过后才提交推送；重大变更走 PR，等待用户审核，不自行合并。
初始许可证快照在 main，功能代码在 feature 分支。未来更新须重新测试后通过 PR。

推送前执行：

```powershell
python -m unittest discover -s tests -v
python publish_check.py
git diff --cached --stat
```

只显式添加代码/测试/文档路径。检查器拒绝数据库、日志、浏览器资料、Cookie、Token、真实私人聊天地址及疑似密钥。
不要上传数据库、截图或任何登录资料；不要把摘要复制到本地README、测试夹具或提交消息。

## 实测范围与来源

[验证记录](docs/VALIDATION.md) 区分网页实测、模拟测试与尚未测试的长期行为。
三条样本通过不代表全量历史已全部总结。

- [上游来源与许可](NOTICE.md)
- [yt-dlp 官方选项](https://github.com/yt-dlp/yt-dlp#usage-and-options)
- [Google：Gemini Notebooks 功能与保存条件](https://support.google.com/gemini/answer/16972047)
- [OpenAI：本机定时任务](https://learn.chatgpt.com/docs/automations?surface=app)

## English

Collect public videos from selected YouTube creators and summarize them into Gemini web Notebooks as a searchable personal knowledge base. Each channel gets one native Notebook and each video gets its own chat. A Windows local Python queue coordinates the installed Codex Browser Automation / Computer Use worker. The tool does not use the Gemini API, download video, audio, or subtitles, or store summaries locally.

### Features

- **Creator-specific batch summaries:** Add a channel URL. yt-dlp inventories public metadata from `/videos`, `/shorts`, and `/streams`, deduplicated by `(channel_id, video_id)`. The first run collects the full history and continues in batches. There is no overall 20-video history cap; actual progress depends on Gemini limits, source availability, and safe pauses.
- **Automatic incremental updates:** Check for and summarize new videos every Monday and Thursday at 08:30 Windows local time. Monday also verifies the full channel inventory. Each scheduled round allows at most 20 new submissions across all channels, prioritizes new videos, and rotates between channels.
- **Automatic naming and archiving:** Choose a one-to-five-Chinese-character topic only after a summary is complete. Rename the chat `YYYYMMDD-topic-full original video title`, refresh the Notebook, reopen the chat from its list, and verify it before marking the task complete. Gemini Notebooks are the archive; summaries are not exported to a local knowledge base.
- **Command-driven, resumable queue:** `ytg.py` manages channels, inventory, and minimal task state. The Codex worker follows the [worker procedure](docs/WORKER.md) in Gemini's web UI. It reconciles uncertain submissions against existing chats or safely pauses instead of resending.
- **Local state separation:** The SQLite task database defaults to `%LOCALAPPDATA%\youtube-gemini-channel-summarizer\tasks.sqlite3`, outside the synced repository. Summaries remain in Gemini; cookies, tokens, and browser profiles are neither read nor exported.

### Requirements and setup

Requires Windows, Python 3.11+, yt-dlp, a running Codex desktop app with Browser Automation / Computer Use, and a signed-in Gemini account with working Notebooks and YouTube content access. The computer, Codex, and browser must stay running and connected. Gemini features and limits may change. This is not a standalone Python browser bot, and the upstream extension is not installed for the production queue.

Run the following from the repository root. First have the worker create and check a real Gemini Notebook, then register its URL. The current user's channel is already registered locally; do not initialize a second task database.

```powershell
python -m unittest discover -s tests -v
python ytg.py add https://www.youtube.com/@wuyuesanren
python ytg.py notebook CHANNEL_ID https://gemini.google.com/notebook/NOTEBOOK_ID
python ytg.py scan --full
python ytg.py status
```

Use `python ytg.py --db <database-path> status` for another non-synced location. The `begin/next/metadata/step/end` commands manage batches; `next` alone does not send anything to Gemini. See the [worker procedure](docs/WORKER.md) for manual processing and recovery.

```powershell
python ytg.py status
python ytg.py pause quota
```

Resume only after the user confirms a fault or limit has cleared: `python ytg.py resume --confirmed`. Scheduled runs never resume on their own. The web UI has no transaction or idempotency key, so exactly-once delivery cannot be guaranteed. When a submission cannot be confirmed, processing pauses rather than risking a duplicate. Private, removed, or unreadable videos retain their task reason and any existing chat mapping; the worker does not invent summaries.

### Verification, attribution, and publishing

The [validation record](docs/VALIDATION.md) separates live Gemini end-to-end checks, automated tests, and longer-term behavior that has not yet been verified. Three successful sample videos do not mean the whole historical inventory is summarized. Sustained history processing and future scheduled triggers still need observation.

This project builds on the YouTube-link-to-Gemini workflow from [soumikdr/youtube-summarizer-chrome](https://github.com/soumikdr/youtube-summarizer-chrome). Its source and MIT license remain under [vendor/youtube-summarizer-chrome](vendor/youtube-summarizer-chrome/); see [attribution](NOTICE.md). The production queue uses the installed Codex web automation, not the upstream extension, and does not track upstream updates.

Code is pushed to the user's private repository only after tests pass. Important changes stay in a PR for user review; they are not merged automatically. Before pushing:

```powershell
python -m unittest discover -s tests -v
python publish_check.py
git diff --cached --stat
```

Stage only explicit code, test, and documentation paths. Never upload the task database, logs, screenshots, summaries, cookies, tokens, browser data, or private Notebook URLs. Scheduled video runs do not commit code.
