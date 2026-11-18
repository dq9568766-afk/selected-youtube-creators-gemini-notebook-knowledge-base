# 历史视频无模型批量改名

## 已延期人工核对项的重新处理

已延期并已转人工核对的历史会话，只有用户明确要求重新处理时才重新进入生成流程；默认选择“3.5 Flash-Lite极速回答”，思考等级为高。该规则不改变普通历史首轮、每日增量或自动重试的模型设置。

仅历史采集使用。Codex 的总结生成、内容判断和扩展归档均在 Codex 内置浏览器完成；WorkBuddy 的总结生成、内容判断和扩展归档均使用指定外部 Chrome 执行浏览器。扩展归档期间暂停本频道其他 Gemini 操作。每日增量继续每条生成、改名、核验。

本工具修改 Gemini 笔记本中的聊天标题。Codex 启用扩展归档时在 Codex 内置浏览器中运行本工具；WorkBuddy 在指定外部 Chrome 中运行本工具。扩展只做改名、重开和网页核验，不发送总结、不重试生成、不恢复未知提交。2026-11-03 页面实查：NotebookLM 的 Gemini 对话来源禁用了更多菜单，不能独立改名；普通 YouTube 来源的重命名菜单不代表对话来源也可改。NotebookLM 是否同步新名称尚未实测，本工具不修改、不核验它，也不把来源显示当作 Gemini 聊天证据。

## 新频道首次确认模板

首次处理新频道前，一次性向用户确认以下四项；未确认前不得登记、扫描或启动 Gemini。改名方案、标题格式和会员资格按频道记录，后续运行沿用；每日增量始终使用方案1。

1. 改名方案

   - 方案1：每条总结后由 Codex 逐条改名并核验。无需安装扩展，流程直接；但每条改名都需要模型操作，历史任务较慢、模型用量较高。
   - 方案2：历史总结后由扩展批量改名和核验，增量仍使用方案1。可减少历史改名和核验阶段的模型用量，适合批量整理；首次需安装一次 Windows Native Messaging 本机组件，之后可在扩展内启动服务；仍受网页操作和节流影响。扩展每条异常最多自动重开两次，仍无法确认才保留 pending。

2. 历史归档标题格式

   - 原标题：格式最简单，不增加标题信息。
   - 主题分类+原标题：便于按主题浏览，但需要生成并核对主题分类。
   - 发布日期+主题分类+原标题：检索和排序最方便，但日期必须来自官方 YouTube 元数据；缺少可靠日期的视频会延期，不能猜测。

3. 该频道 YouTube 会员资格：有 / 无 / 不确定

   无会员资格时，会员限制视频和 Shorts 完全排除，不扫描、不入队、不计入历史完成率。无法确认访问权限时不猜测，按来源限制规则处理。

4. 任务范围：首次全量历史扫描 / 只做每日增量

   首次全量扫描会建立该频道的历史处理记录；每日增量只处理新增视频，不扫描历史内容。

## 安装与首次检查

方案2的历史生成阶段先完成扫描和 Gemini 总结；只有总结阶段完成，或用户明确要求立即批量改名时，才提醒加载并验证扩展。每日增量始终逐条总结、改名和核验，不使用本扩展。

1. 扩展文件位于绝对路径 `C:\Users\Administrator\.codex\skills\DQ-youtube-creator-gemini-notebook-knowledge-base\extensions\history-renamer`。加载时必须选择整个 `history-renamer` 文件夹，不是 `panel.html` 或单个脚本。Codex 按内置浏览器支持的扩展加载流程加载；WorkBuddy 在指定外部 Chrome 地址栏打开 `chrome://extensions`，开启“开发者模式”，点“加载已解压的扩展程序”，选择该文件夹。扩展是本地项目文件，不是商店扩展；智能体代为安装时须取得安装当时的明确确认。用户自行安装时，按对应浏览器提示完成。Codex 不使用外部 Chrome，WorkBuddy 不使用 IAB 加载扩展。
2. 首次使用在上述扩展目录中双击 `install_native_host.cmd`。安装脚本会在当前机器探测 Python，构建并注册 Windows Native Messaging 主机，并把本机路径只写入用户目录的 `host-config.json`；该文件不在仓库中，其他机器必须重新运行安装脚本，不能复制本机配置。安装完成后重新加载扩展。此后服务启动、停止和配对都在扩展界面完成。浏览器不会允许扩展静默写注册表或安装本机组件。
3. 权限包括 Gemini 页面、本机回环服务、脚本执行和 Native Messaging。不申请 Cookie、调试器、浏览历史或所有网站权限。浏览器自行保留登录会话，工具不搬运登录数据。
4. 在该浏览器登录登记的 Google 账号。点击扩展图标打开“历史视频批量改名”面板，先点“读取可用任务”，选择目标频道和 run，再点“启动/恢复本机服务”；面板显示“本机服务：已启动并已连接”后，核对账号及 Gemini Notebook，勾选确认。首次先点“只检查一条”，成功后点“改名一条”并核对网页结果，再启动批量。需要提前退出时点击“停止本机服务”，它会释放本频道租约并终止本机子服务；批次完成或批量过程中的“停止”也会自动释放服务。没有候选任务时，先恢复并显式启用目标历史 run，不得直接点启动。

若当前自动化浏览器不允许打开扩展管理页或选择本地目录，引导用户按上面步骤手动加载；不要改用其他自动化表面绕过限制。安装加载完成前不启动真实改名服务，也不声称已安装或已通过真实页面检查。

### 扩展页面无法被当前智能体读取时

如果当前平台已经装好扩展，但智能体不能读取扩展面板或扩展协议页面，提示用户在当前平台的获准浏览器中人工完成以下顺序：

扩展目录：`C:\Users\Administrator\.codex\skills\DQ-youtube-creator-gemini-notebook-knowledge-base\extensions\history-renamer`。若尚未加载，在 Codex 内置浏览器的扩展管理入口选择“加载已解压的扩展”，选择整个目录；首次使用再运行目录中的 `install_native_host.cmd`。加载完成后打开扩展面板，才能点击“启动本机服务”。

1. 打开“历史视频批量改名”扩展面板。
2. 点击“启动本机服务”，确认显示已启动并已连接。
3. 核对登记 Google 账号和 Gemini Notebook，勾选确认。
4. 点击“只检查一条”；通过后点击“改名一条”，在网页中核对实际标题、视频、账号和 Notebook。
5. 点击“开始批量改名”，保持扩展面板和工作标签打开，直到批次结束或明确停止。

人工操作期间，智能体不切换其他浏览器、不重复启动服务、不发送 Gemini 消息。用户完成后由智能体重新读取 `history-rename report` 和频道历史报告；只有数据库显示改名积压清零、所有归档项已核验或明确转人工复核，且频道报告 `complete=true`，才能报告完成。扩展窗口标题、按钮已点击或用户口头确认都不能单独证明成功。

## 生成历史视频

先按 WORKER 执行当前平台获准浏览器门槛和频道 status/租约检查。根据实际可见账号配置对应频道，不在命令中猜账号：

```powershell
python ytg.py history-rename configure --channel CHANNEL_ID --account GOOGLE_EMAIL
python ytg.py begin --mode history --channel CHANNEL_ID --policy collect-first --archive extension
```

若已有运行，在当前提交结清后用 `python ytg.py history-rename enable RUN` 切换；仅频道历史可启用，不修改增量或其他频道。未启用的旧运行保持 serial。恢复同一运行保持已保存的 archive_mode；每日增量交接后回到历史时也继承它。

生成仍逐视频串行。`summary --evidence youtube-content-complete-notebook --next` 表示当前平台获准浏览器已经在登记 Notebook 上核对完整实质总结、原视频、账号和笔记本；普通 Gemini 对话中的同名总结不满足此证据。登记主题后将视频留在 summarized，并记录待改名。此时可以生成下一条，不能声称 verified。仅在此模式于 checked summary 结清时计一次20条休息进度，后续改名核验不重复计数。

历史生成每20条休息时或生成阶段结束后可处理积压。`archive_pending` 表示生成已无可领取项、仍有待改名；不是任务完成。只有历史首轮 `report complete=true` 且 `tracking=1` 后，`daily_due` 到期才可交接增量；不能由独立每日任务或历史 handoff 抢占未完成历史。增量继续原方案。未知提交与服务暂停仍按原规则处理。

## 切换到扩展改名

结清当前提交后，在项目目录启动本机服务（保持该进程运行）：

```powershell
python ytg.py history-rename serve RUN
```

Native Messaging 模式下服务由扩展启动和释放，不需要打开 PowerShell 或选择配对文件。发生故障时仍可在项目目录运行 `extensions/history-renamer/start_service.cmd` 作为备用入口；它会自动寻找唯一有效的历史扩展任务。

Native Messaging 安装完成后，简化操作为：打开扩展，点击“启动本机服务”，再点击“只检查一条”或“开始批量改名”。`extensions/history-renamer/start_service.cmd` 仍保留作为故障排查备用入口。

服务只监听 `127.0.0.1`，沿用当前频道租约。配对码只在扩展与本机组件之间传递，不写入仓库，不包含 Google 凭据或总结。其他频道仍使用共享账号动作闸门；本频道改名服务存活时拒绝生成新视频。

同一台 Windows 机器只需安装一次 Native Messaging 组件；不同频道不重复安装。每次切换频道前，必须先对目标历史 run 显式执行 `history-rename enable RUN`，该步骤会更新本机目标绑定。扩展的自动启动只接受这个明确绑定，不根据旧 `target.json`、最近任务或多个候选 run 猜测频道；发现绑定缺失或频道不一致时必须停止并提示先明确启用目标 run，不能自动接管旧频道。

在已安装本项目扩展且登录登记账号的获准浏览器打开扩展面板，点击“启动本机服务”，核对页面显示的账号和登记 Gemini 笔记本，勾选确认后再操作。WorkBuddy 使用指定外部 Chrome；Codex 按其内置浏览器规则执行。测试期间暂停本频道其他 Gemini 操作。扩展只使用单个新建工作标签；保持工具页打开，不手动切换其工作标签。

工具只接收已由队列登记 `youtube-content-complete-notebook` 的聊天；旧数据库中没有 Notebook 核验标记的 summarized/renamed/verified 项必须先回到对应频道登记 Notebook 重新处理，扩展不会把脱离该 Notebook 的 Gemini 对话直接改名为成功。之后逐条打开已有聊天，从网页返回按钮核对实际笔记本归属，再读回原视频与已完成正文；通过网页菜单修改标题。失败、拒答、空正文等无效聊天不进入改名队列，先保留为 `cleanup_required`。仅在历史项全部结清或用户明确停止时，由人工确认一次后在对应 Notebook 批量删除，再用 `cleanup-invalid --evidence chat-deleted-in-notebook` 记录删除证据。保存前登记旧名，保存后重新打开聊天、读回实际标题，再登记 verified。不发送提示词、不调用模型或 Google API，不采集完整页面或保存正文。页面访问和改名通过本机共享节流，已写回的映射不会重复处理；重建映射不设总条数上限，每20条冷却30秒。页面中断、结构变化、账号或归属暂时未加载时，工具从登记 Notebook 自动重开并重做整套核验，最多两次；每次会记录失败阶段，仍无法确认才保留 pending。

扩展批处理期间每30秒向本机服务发送一次心跳，续租服务会话，避免长时间节流或页面等待导致配对过期。心跳失败时停止当前批次并保留未完成项目；它不会把断线或失联记为成功。服务仍只在当前批次运行，完成、停止或租约失效后会按规程退出并清理配对文件。

名字已经正确也要核对实际聊天与归属，不能只改本地数据库。保存后断线保留 pending，重启可从已登记旧名和当前名称恢复。自动恢复耗尽后才停止，不猜匹配、不重新发送视频、不自动延期为成功。队列 verified 表示前期内容核验加后期网页结构核对，扩展不重新作语义质量判断。

`--include-verified` 仅用于用户明确要求检查或给已经 verified 的历史聊天回改标题。单条测试同时传 `--video VIDEO_ID`，只纳入并处理指定历史聊天；不传 `--video` 才会把所有已核验历史聊天加入待处理名单。默认只处理已登记待改名项，不纳入延期失败视频或增量。待改名名单与来源正文不导出到文件。

```powershell
python ytg.py history-rename serve RUN --include-verified --video VIDEO_ID
```

## 停止、恢复和报告

点击扩展停止，待当前操作返回后释放本机服务；或者在服务终端按 Ctrl+C。退出执行 cleanup，释放本频道占用，保留未核验与重开标记。只关闭扩展页不会立即停止服务，应停止终端进程；租约失效后服务会拒绝后续操作。完成批次时服务自动退出并清理配对文件。

恢复须先按 WORKER 用当前平台获准浏览器检查页面、status 和租约，再 `begin --mode history --channel CHANNEL_ID --run RUN` 续接未完成运行，重新启动 serve 并选择新的配对文件。不得同时启动两个本频道服务。

```powershell
python ytg.py history-rename report --channel CHANNEL_ID
python ytg.py report --mode history --channel CHANNEL_ID
```

存在 pending 改名项时 `complete=false`。确实无法核验的条目仅在明确决定转人工复核后执行：

```powershell
python ytg.py history-rename review RUN --channel CHANNEL_ID --video VIDEO_ID --reason REASON
```

它保留已生成聊天，将未核验项登记 archive_unverified，不增加 verified 数，不自动重试。频道 report 列出 rename_manual_review；普通延期仍使用原来的 manual_review。不存在待处理积压且所有历史条目均 verified 或明确 deferred 才能报告首轮完成。

## 验证

```powershell
python -m unittest discover -s tests -p test_history_rename.py -v
node --test tests/history_rename_page.test.mjs
python publish_check.py
```

另对实际变更及新增路径运行 `publish_check.check(paths)`；默认脚本读取 Git 索引，不能替代对尚未暂存文件的检查。隔离测试不访问真实任务库。Chrome 脚本执行权限依据官方文档：<https://developer.chrome.com/docs/extensions/reference/api/scripting>。
