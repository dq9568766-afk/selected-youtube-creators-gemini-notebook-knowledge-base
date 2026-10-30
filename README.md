# 收藏的视频，终于能整理起来了 📚

### YouTube 创作者视频批量总结 · Gemini 笔记本知识库 · AI Skill

**给一个频道链接，让 AI 帮你逐条总结、命名、归档。**

[中文](#中文) · [English](#english) · [下载版本](https://github.com/dq9568766-afk/selected-youtube-creators-gemini-notebook-knowledge-base/releases)

## 中文

### 视频越收藏越多，真正回看的有几个？

想补完一个博主的旧视频，却没时间一条条看。

想留下重点，又得反复复制链接、发给 AI、改标题、存笔记。

这个 **AI Skill** 把这些重复操作串起来：读取频道的公开历史清单，在已登录的 Gemini 网页逐条生成总结，再按频道归档。你拿到的是一组有日期、有主题、能重新打开的总结聊天。

> **一个频道，一本笔记；一条视频，一份总结。**

`频道链接 → 历史清单 → 逐条总结 → 自动命名 → 核验归档`

### ✨ 值得装它的 5 个理由

- **旧视频，按清单慢慢补齐。** 覆盖公开视频、Shorts 和直播清单，按视频 ID 去重，历史处理不设总条数上限。
- **总结完就整理好。** 每条生成后立即改名、核验，再处理下一条。标题统一为 `YYYYMMDD-主题词-完整原视频标题`，回头找内容更方便。
- **中断了，留着进度接着做。** 记录任务状态和已有聊天，恢复时优先接续；提交不明时按规则保留现场，避免重复发送。
- **哪条没做成，清清楚楚。** 授权失败后置后，首轮把单条异常列入待复查清单，继续后面的内容；生成数、核验数、延期项分开汇报。
- **旧内容整理完，新内容也能跟。** 可另配每日增量任务，发现新视频；安装 Skill 本身不会自动开启定时任务。

### 🧠 整理完，可以怎么用？

回顾某个时期的内容，按日期找；回找一个话题，先看主题词和原标题。想了解某位创作者，也可以先读总结，再挑值得细看的原视频。

总结保存在 **Gemini 笔记本（可作为后续整理 NotebookLM 笔记的材料）**。另行导入 NotebookLM 并核验来源后，还可用于跨视频提问、比较观点、整理学习材料。


### 🛠️ 怎么安装？让 AI 帮你创建技能

以 Codex 为例，把下面这段发给它：

```text
使用 $skill-creator，根据这个仓库创建或接入本地技能：
https://github.com/dq9568766-afk/selected-youtube-creators-gemini-notebook-knowledge-base

保留完整项目，以根目录 SKILL.md 为技能入口，并遵守 AGENTS.md 和 docs/WORKER.md。
检查并协助配置 Python 3.11+、yt-dlp 和已登录的 Gemini 网页操作能力。
已有安装和任务进度请复用，不覆盖用户修改，不复制或重建任务数据库。
完成后告诉我如何调用。本次只安装，不开始采集，也不创建定时任务。
```

**准备好这些就能开始：** Windows、Python 3.11+、yt-dlp，以及能读文件、运行命令、操作 Gemini 网页的 AI 智能体。Gemini 账号需要实际可用的 Notebooks 和 YouTube 内容读取能力。

仓库已带 [SKILL.md](SKILL.md)，创建技能时接入完整项目即可，不必重新写一套流程。Codex 的 `$skill-creator` 用法见 [官方说明](https://developers.openai.com/plugins/build/skills)；其他智能体的能力要求见 [适配指南](docs/AGENT_ADAPTERS.md)。

### ▶️ 装好后，给它一个频道

把示例地址替换成你想整理的频道，再发给智能体：

```text
使用 $youtube-creator-gemini-notebook-knowledge-base，整理这个频道：
https://www.youtube.com/@channel

采集公开历史视频，允许单条失败后置，使用 collect-first。
先核对现有进度、账号和对应笔记本，每条总结后立即改名核验。
结束时告诉我生成了多少、核验了多少，以及哪些还没完成。
```

想每天追更，再单独让智能体配置每日增量任务。具体执行和恢复规则见 [工作器规程](docs/WORKER.md)。

### 📌 使用前，知道这几件事

- **需要智能体持续执行。** 电脑关机、应用退出、登录失效或额度耗尽时，不能保证继续处理；暂停不代表后台还在跑。
- **有节奏，也有边界。** 单工作标签串行处理，每20条结清后休息至少2分钟；遇到登录、验证码或配额阻塞会停下，不能保证所有视频都能生成总结。
- **少留一份数据。** 只采集公开元数据，不下载视频、音频或字幕，不调用 Gemini API；摘要留在 Gemini，本地只记最小任务状态。
- **总结仍需判断。** 92项隔离自动测试已通过；网页实测范围见 [验证记录](docs/VALIDATION.md)。模型可能遗漏或误读，重要内容请回看原视频。

<details>
<summary>维护与进阶：依赖、测试、任务数据库</summary>

在仓库根目录安装 Python 依赖、运行隔离测试和发布检查：

```powershell
python -m pip install -e .
python -m unittest discover -s tests -v
python publish_check.py
```

唯一任务数据库位于 `%LOCALAPPDATA%\youtube-gemini-channel-summarizer\tasks.sqlite3`，不要复制、重建已有库或提交到 Git。摘要、凭据、浏览器资料和私人 Notebook 地址也不要上传。

详细规则：[SKILL.md](SKILL.md) · [项目约定](AGENTS.md) · [工作器规程](docs/WORKER.md) · [调度提示](docs/AUTOMATION_PROMPT.md) · [优化记录](docs/OPTIMIZATION.md)

</details>

## English

### 📚 Saved another video. When will you actually watch it?

Catching up with a creator means more than watching. There are links to copy, summaries to request, titles to fix, and notes to organize.

**This AI Skill connects those steps.** Give your agent a YouTube channel URL. It inventories public videos, summarizes them through your signed-in Gemini web session, and organizes one chat per video in the channel's registered Notebook.

> **One creator. One notebook. One summary per video.**

`Channel → Video inventory → Summarize → Rename → Verify & archive`

### ✨ Why use it?

- **Work through the archive.** Public videos, Shorts, and streams are deduplicated by video ID. Historical processing has no overall item cap.
- **Keep notes organized as you go.** Each summary is renamed and verified before the next video. Titles follow `YYYYMMDD-topic-full original title`.
- **Resume with your progress intact.** Saved task state and chat links help the agent recover existing work and avoid duplicate submissions.
- **See what actually finished.** With an authorized collect-first run, individual failures are deferred for review. Generated, verified, and deferred counts stay separate.
- **Keep up with new uploads.** Configure a separate daily incremental task when you want one. Installing the Skill does not enable scheduling automatically.

Summaries stay in **Gemini Notebooks**. You can separately import verified material into NotebookLM for cross-video questions and study resources; automatic NotebookLM import or sync verification is outside this workflow.

### 🛠️ Install by asking your agent to create the Skill

In Codex, paste:

```text
Use $skill-creator to create or connect a local skill from:
https://github.com/dq9568766-afk/selected-youtube-creators-gemini-notebook-knowledge-base

Keep the full project, use its root SKILL.md as the entry point,
and follow AGENTS.md and docs/WORKER.md.
Help check Python 3.11+, yt-dlp, and signed-in Gemini browser access.
Reuse existing installations and progress; preserve user changes.
Do not recreate or copy an existing task database.
Report how to invoke it. Install only; do not collect videos or schedule tasks.
```

You need Windows, Python 3.11+, yt-dlp, and an agent with file, shell, and browser access. Your Gemini account must support Notebooks and YouTube content access. See the [skill creation guide](https://developers.openai.com/plugins/build/skills) and [agent adapters](docs/AGENT_ADAPTERS.md).

### ▶️ Start with a channel you follow

Replace the example URL, then ask:

```text
Use $youtube-creator-gemini-notebook-knowledge-base for:
https://www.youtube.com/@channel

Collect public historical videos using collect-first, with item failures deferred.
Check existing progress, account, and Notebook identity before starting.
Rename and verify each summary immediately.
Report generated and verified counts, plus every unfinished item.
```

### 📌 Before you start

The agent needs to remain available to execute the workflow. Login, quota, and session blockers can stop progress. Processing uses one working tab, with a break of at least 2 minutes after every 20 settled items.

Only public metadata is collected: no media or subtitle downloads, Gemini API calls, or local summary storage. **92 isolated tests pass**, but this does not establish long-term web reliability or summary accuracy. Check important claims against the original videos. See [validation records](docs/VALIDATION.md) and the [worker procedure](docs/WORKER.md).

---

参考 / Inspired by [soumikdr/youtube-summarizer-chrome](https://github.com/soumikdr/youtube-summarizer-chrome)。保留 [上游源码与 MIT 许可 / upstream source and MIT license](vendor/youtube-summarizer-chrome/) · [来源说明 / Attribution](NOTICE.md)。
