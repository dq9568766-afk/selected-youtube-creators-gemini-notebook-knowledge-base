# 历史视频无模型批量改名

仅历史采集使用。总结生成及内容判断仍在 Codex 内置浏览器；扩展已安装在内置浏览器时，历史改名也使用该浏览器并暂停本频道其他 Gemini 操作。每日增量继续每条生成、改名、核验。

本工具修改 Gemini 笔记本中的聊天标题。2026-11-03 页面实查：NotebookLM 的 Gemini 对话来源禁用了更多菜单，不能独立改名；普通 YouTube 来源的重命名菜单不代表对话来源也可改。NotebookLM 是否同步新名称尚未实测，本工具不修改、不核验它，也不把来源显示当作 Gemini 聊天证据。

## 新频道首次确认模板

首次处理新频道前，一次性向用户确认以下四项；未确认前不得登记、扫描或启动 Gemini。改名方案、标题格式和会员资格按频道记录，后续运行沿用；每日增量始终使用方案1。

1. 改名方案

   - 方案1：每条总结后由 Codex 逐条改名并核验。无需安装扩展，流程直接；但每条改名都需要模型操作，历史任务较慢、模型用量较高。
   - 方案2：历史总结后由扩展批量改名和核验，增量仍使用方案1。可减少历史改名和核验阶段的模型用量，适合批量整理；但需要加载扩展、启动本机服务并保持浏览器页面运行，仍受网页操作和节流影响。扩展每条异常最多自动重开两次，仍无法确认才保留 pending。

2. 历史归档标题格式

   - 原标题：格式最简单，不增加标题信息。
   - 主题分类+原标题：便于按主题浏览，但需要生成并核对主题分类。
   - 发布日期+主题分类+原标题：检索和排序最方便，但日期必须来自官方 YouTube 元数据；缺少可靠日期的视频会延期，不能猜测。

3. 该频道 YouTube 会员资格：有 / 无 / 不确定

   无会员资格时，会员限制视频和 Shorts 完全排除，不扫描、不入队、不计入历史完成率。无法确认访问权限时不猜测，按来源限制规则处理。

4. 任务范围：首次全量历史扫描 / 只做每日增量

   首次全量扫描会建立该频道的历史处理记录；每日增量只处理新增视频，不扫描历史内容。

## 安装与首次检查

1. 扩展文件已随项目放在 `extensions/history-renamer`。在准备使用的浏览器地址栏打开 `chrome://extensions`，开启“开发者模式”，点“加载已解压的扩展程序”，选择该项目文件夹。扩展是本地项目文件，不是商店扩展；智能体代为安装时须取得安装当时的明确确认。用户自行安装时，按浏览器提示完成。
2. 权限仅包括 Gemini 页面、本机回环服务及脚本执行。不申请 Cookie、调试器、浏览历史或所有网站权限。浏览器自行保留登录会话，工具不搬运登录数据。
3. 在该浏览器登录登记的 Google 账号。点击扩展图标打开“历史视频批量改名”面板；启动本机服务后，点击“选择文件”并选服务输出的 `history-rename-<RUN>.local.json` 配对文件。核对面板显示的账号及 Gemini Notebook，勾选确认。首次先点“只检查一条”，成功后点“改名一条”并核对网页结果，再启动批量。隔离测试不能代替真实页面检查。批量期间保持扩展面板打开，不关闭或切换它创建的工作标签。

若当前自动化浏览器不允许打开扩展管理页或选择本地目录，引导用户按上面步骤手动加载；不要改用其他自动化表面绕过限制。安装加载完成前不启动真实改名服务，也不声称已安装或已通过真实页面检查。

## 生成历史视频

先按 WORKER 执行内置浏览器门槛和频道 status/租约检查。根据实际可见账号配置对应频道，不在命令中猜账号：

```powershell
python ytg.py history-rename configure --channel CHANNEL_ID --account GOOGLE_EMAIL
python ytg.py begin --mode history --channel CHANNEL_ID --policy collect-first --archive extension
```

若已有运行，在当前提交结清后用 `python ytg.py history-rename enable RUN` 切换；仅频道历史可启用，不修改增量或其他频道。未启用的旧运行保持 serial。恢复同一运行保持已保存的 archive_mode；每日增量交接后回到历史时也继承它。

生成仍逐视频串行。`summary --evidence youtube-content-complete --next` 表示内置浏览器已经核对完整实质总结、原视频、账号和笔记本；登记主题后将视频留在 summarized，并记录待改名。此时可以生成下一条，不能声称 verified。仅在此模式于 checked summary 结清时计一次20条休息进度，后续改名核验不重复计数。

历史生成每20条休息时或生成阶段结束后可处理积压。`archive_pending` 表示生成已无可领取项、仍有待改名；不是任务完成。每天增量仍优先，可以保留批量改名积压交接增量；增量继续原方案。未知提交与服务暂停仍按原规则处理。

## 切换到扩展改名

结清当前提交后，在项目目录启动本机服务（保持该进程运行）：

```powershell
python ytg.py history-rename serve RUN
```

服务只监听 `127.0.0.1`，沿用当前频道租约。其输出给出本机配对文件路径，该文件与真实任务库同目录，只含本次随机配对码及本机地址，没有 Google 凭据或总结。它不写入仓库，退出即删除。其他频道仍使用共享账号动作闸门；本频道改名服务存活时拒绝生成新视频。

在已安装本项目扩展且登录登记账号的浏览器打开扩展面板，选择配对文件，核对页面显示的账号和登记 Gemini 笔记本，勾选确认后再操作。Codex 内置浏览器已安装扩展时，可在该浏览器检查；测试期间暂停本频道其他 Gemini 操作。扩展只使用单个新建工作标签；保持工具页打开，不手动切换其工作标签。

工具逐条打开已有聊天，从网页返回按钮核对实际笔记本归属，再读回原视频与已完成正文；通过网页菜单修改标题。保存前登记旧名，保存后重新打开聊天、读回实际标题，再登记 verified。不发送提示词、不调用模型或 Google API，不采集完整页面或保存正文。页面访问和改名通过本机共享节流，已有20条休息的剩余时间由数据库返回，重启不会重置。页面中断、结构变化、账号或归属暂时未加载时，工具从登记 Notebook 自动重开并重做整套核验，最多两次；每次会记录失败阶段，仍无法确认才保留 pending。

名字已经正确也要核对实际聊天与归属，不能只改本地数据库。保存后断线保留 pending，重启可从已登记旧名和当前名称恢复。自动恢复耗尽后才停止，不猜匹配、不重新发送视频、不自动延期为成功。队列 verified 表示前期内容核验加后期网页结构核对，扩展不重新作语义质量判断。

`--include-verified` 仅用于用户明确要求检查或给已经 verified 的历史聊天回改标题。单条测试同时传 `--video VIDEO_ID`，只纳入并处理指定历史聊天；不传 `--video` 才会把所有已核验历史聊天加入待处理名单。默认只处理已登记待改名项，不纳入延期失败视频或增量。待改名名单与来源正文不导出到文件。

```powershell
python ytg.py history-rename serve RUN --include-verified --video VIDEO_ID
```

## 停止、恢复和报告

点击扩展停止，待当前操作返回后释放本机服务；或者在服务终端按 Ctrl+C。退出执行 cleanup，释放本频道占用，保留未核验与重开标记。只关闭扩展页不会立即停止服务，应停止终端进程；租约失效后服务会拒绝后续操作。完成批次时服务自动退出并清理配对文件。

恢复须先按 WORKER 用内置浏览器检查页面、status 和租约，再 `begin --mode history --channel CHANNEL_ID --run RUN` 续接未完成运行，重新启动 serve 并选择新的配对文件。不得同时启动两个本频道服务。

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
