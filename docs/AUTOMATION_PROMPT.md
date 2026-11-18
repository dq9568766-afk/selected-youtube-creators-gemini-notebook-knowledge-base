# 自动化入口

启动顺序（优先于通用浏览器门槛）：第一条队列命令必须是 python ytg.py daily-candidates；空列表正常结束。有候选才进入 [@浏览器](plugin://browser@openai-bundled)。使用该插件当前安装版本 scripts/browser-client.mjs 的 file URL 在 node_repl 中导入 setupBrowserRuntime，保存其返回值 runtime=await setupBrowserRuntime({globals:globalThis})；用 await runtime.browsers.get('iab') 绑定插件自己的内置浏览器，再用 browser.tabs.list() 选择 Gemini 工作标签；没有合适标签时 browser.tabs.new() 后打开 https://gemini.google.com/app。首次页面仍在加载时等待目标账号可见。不要用 cua.getState() 的清单判断本插件是否可用。绑定失败按插件 bootstrap-troubleshooting 复用同一 runtime 检查一次；仍不可用则记录失败阶段。插件内 iab 是指定后端；禁止的 IAB 替代路径指绕过该插件的其他接口。

WorkBuddy 连续处理时使用 [WORKBUDDY_RUNNER.md](WORKBUDDY_RUNNER.md) 作为持续运行入口；本文件补充频道策略和每日增量规则。Codex 每日增量使用内置浏览器插件，WorkBuddy继续使用其指定外部 Chrome。

## 历史首轮

标题格式为 `original` 时直接使用原标题。标题格式为 `topic` 或 `date-topic` 时，完整全量扫描后批量按原标题生成主题词；独立日期资料只在用户明确要求时运行 `metadata-backfill`，用户明确启动 `history-pipeline` 时可由该流程完成 `date-topic` 的日期前置。日期补齐报告 `complete=true` 后，必须报告覆盖率与异常数量，并提示用户现可开始历史总结；不可静默结束或自动启动 Gemini。用户明确开始总结后，先创建/继续本频道 Codex Goal，再核对浏览器、Notebook、账号及队列状态，然后启动 history run。用户自行启动改名扩展，扩展读取数据库最新标题，不要求用户逐条确认主题词。

第一次全量历史扫描前，先询问用户是否拥有该频道的 YouTube 会员资格，按频道记住回答；未确认前不得扫描。无会员时会员限制视频像 Shorts 一样完全排除，不入队、不计入完成率；有会员时纳入正常处理范围。后续每日扫描沿用该选择，并在报告中说明会员范围及排除项。不得用未授权方式获取会员视频。

王志安频道的聊天标题使用“发布日期-1–5个汉字中文主题-原视频标题”；原视频标题完整保留，不使用英文主题。发布日期必须来自已登记元数据，缺日期的任务延期，不猜测日期。

仅使用用户指定频道及任务库已登记的Gemini Notebook。历史总结仍按本文件的Goal和浏览器门槛；每日增量不创建Goal，不要求历史首轮完成，可独立抢占同频道历史租约。每日增量按频道顺序调度；collect-first 增量遇到单条未知提交时可延期并继续，但下一条必须从同一 Notebook 的新空白聊天开始，不得重发未知项。

交接时旧聊天先停止领取新视频，结清当前条目或保留未确认提交证据，执行cleanup/park释放本频道租约并停止运行。新聊天只在旧租约释放后接手，核对频道、账号、登记Notebook、run和未结清任务；确认实际接手后归档旧聊天。接手失败保留旧聊天，禁止两个聊天同时处理同一频道。新建或接手的频道聊天使用当前 Gemini 网页实际可见、已选定的常规模型，推理强度为低度；每日增量沿用该配置。不得把 GPT 系列模型作为 Gemini 网页的选择前置条件。

每条prepare、arm、在Notebook独立聊天发送URL换行加“总结”、等待、核实并登记；王志安标题必须是“发布日期-中文主题-完整原视频标题”，总结完成立即改名，再核验后连续处理下一条。Gemini非提交UI动作前运行 `python ytg.py pace access --run RUN`，ready=true后立即执行一个动作；arm/retry负责提交时间门控，ready=false时按剩余秒数等待并重试原命令。账号协调权只覆盖当前页面动作窗口，其他频道可以并行等待生成，页面动作和提交按账号顺序执行。普通动作及不同视频提交的最小间隔均为15秒；恢复期较长间隔按WORKER执行。默认不因单条完成暂停，不因单条完成cleanup；只有完成、明确阻塞、用户停止、规定冷却或交接时才释放租约。正常条目在当前页面一次核对准确标题、完整总结、原视频链接、账号与Notebook，使用current-page-video-summary；操作报错、页面中断、标题未更新或归属不明先按WORKER登记require-reopen，再在同一工作标签重开已有聊天核验。首轮不搜索；每次提交后20秒检查一次，首次无正文且原请求已确认、停止已确认、页面未跳转或中断时，仅在规则允许的同聊天再尝试中先在“3.5 Flash-Lite极速回答 推理中”与“3.8 Flash全方位帮助”之间切换到另一个模型，再点击已发送消息的“修改提示”，在“总结”前加“请”或去掉“请”，通过retry登记后提交修改后的提示；不得在输入框原样重发。再等20秒，仍未完成则停止并延期。完成后仍无可登记聊天地址记chat_unidentified；首次已有正文但未完成则停止并延期，不重发；未知提交和内容失败不重复发送。再次尝试及预算按WORKER执行，其他异常按策略延期，归档未核验登记archive_unverified。先确认停止当前生成再进入下一条。元数据复用扫描标题、日期和视频身份；缺日期的王志安任务延期，不猜日期。每20条结清后至少休息90秒，成功和明确延期各计一次，续跑不重置；不增加抽查，不例行重开。连续失败健康冷却仍为300秒，有限浏览器恢复以WORKER为准。

账号登录、验证码、/sorry、配额或确认的账号/浏览器会话故障立即保留全局pause并cleanup；strict/历史单频道未知提交、来源不可用和健康检查失败只保留该频道的pause，不阻断其他频道；collect-first 增量单条未知提交直接延期，不暂停频道，但下一条必须使用新空白 Notebook 聊天。只通知一次。已知阻塞后的自动续行不调用受影响范围的队列或浏览器，不重复提醒，等待用户明确恢复。正常处理只汇总批次进度，单条失败不询问。

首轮结束清空归档积压并运行频道report；complete=true才报告首轮完成，包含真实生成/核验数、成功率及所有延期清单。等待用户选择个别补做。不得把租约或CLI描述成无人值守后台工作。

## 每日05:00增量（Asia/Shanghai）

每天只触发一次。先运行 `python ytg.py daily-candidates`，读取全部 `initialized=1` 且 `tracking=1`、已过本地05:00且当天未完成的频道；预检不排除暂停、历史未完成或有效租约。频道按任务库顺序串行处理，不建立并行任务。不得清除 pause；同频道历史租约由 scheduled incremental 原子接管，全局租约仍阻止本轮。

每次每日增量自动化运行由 Codex 内置浏览器插件控制 Gemini。插件启动后只确认登录账号为 `dq9568767@gmail.com`；每个频道仅导航到任务库已登记的 Notebook URL，不读取 Notebook 详细内容，不执行无副作用交互，不运行独立 `status`。插件不可用、账号不匹配或页面无法打开时安静退出，不运行队列命令。若数据库恢复后已登记频道的 `tracking` 与已确认配置不一致，先停在人工复核；只有已有明确项目记录证明该频道应跟踪时，才恢复为 `tracking=1`，不得对未知频道猜测。

候选频道使用 `begin --mode incremental --scheduled --channel CHANNEL_ID --policy collect-first`，随后 `scan --channel CHANNEL_ID --run RUN`，扫描成功才处理该 run 的 `run_candidates`。每个候选必须逐条完成 Gemini 总结、按频道标题格式改名并在同一页面核验；报告按频道列出视频 ID、原标题、最终归档标题和状态。20次提交预算按频道/本地自然日持久化，超额pending留次日；collect-first 增量的单条 unknown submit 由状态机直接记为 `submission_unconfirmed` 延期，打开新空白 Notebook 聊天后继续下一条，不自动重发；其他账号及服务阻塞仍保留暂停，通知一次，不反复唤醒。暂停频道跳过，全局暂停结束本轮；独立每日轮次用end结束，不自动启动历史。

正在执行历史的同一Goal遇到daily_due时，结清当前视频并handoff HISTORY_RUN，扫描并处理返回的同频道增量run。增量empty/daily_cap/round_cap后handoff DAILY_RUN，使用返回的原policy历史run立即继续，不结束Goal、不要求用户再次继续。扫描失败不能end或当作增量完成，按WORKER有限恢复/暂停。每日轮次扫描成功及预算结果与历史完成状态分别报告。

## Gemini消息

```text
{url}
总结
```


## 历史日期补齐（可选）

王志安当前任务要求发布日期进入聊天标题；缺日期时延期，不阻塞其他视频，但必须进入最终补齐/人工复核清单。

单独要求日期资料时才运行 `metadata-backfill`；它不发送 Gemini，也不自动重新入队延期总结。

元数据请求使用共享数据库节流，默认初始间隔1秒，可由 YOUTUBE_METADATA_INTERVAL_SECONDS 配置，跨进程和重启保留；不再按请求计数强制等待300秒。官方 Data API 元数据请求按每批最多50个视频ID合并。429自动指数退避，连续成功后逐步恢复到配置间隔；认证挑战和网络故障仍立即暂停。配额耗尽记录官方太平洋时间下一个午夜，在重置前不清除。此暂停不表示Gemini登录失效。用户明确恢复后使用 metadata-backfill resume --confirmed 清除元数据服务暂停；不清除其他范围暂停，不自动唤醒。普通扫描和单条查询也遵守此访问门槛。

API 配置排查：Windows 任务可能早于环境变量更新而启动。若当前进程中的 `YOUTUBE_DATA_API_KEY` 已存在但官方接口提示密钥无效或过期，不能直接结束；应单独读取 Windows 当前用户 `HKCU\Environment`，在新子进程中用该候选值验证官方 API。验证成功后，日期补齐命令必须显式使用该 Windows 用户变量运行，避免旧进程环境遮蔽新值。密钥只显示 `present|missing`，不得输出或写入日志、数据库、仓库或聊天。

补齐状态与总结状态分离：pending/fetching/complete/unavailable/review；每条事务保存，失败保留待处理。明确限制或确实没有公开日期记unavailable，未知异常或身份冲突停止复核。保留release_date、upload_date、采用来源和检查时间，不推算日期。补齐不发送Gemini、不重新入队延期总结，不改已有聊天名称。频道租约互斥，退出释放本轮占用。

报告区分补齐首轮结束与全部日期齐全：complete表示无pending/fetching/review；all_dates_complete及date_coverage说明实际覆盖率。不可用项目不算日期成功。此专用模式实现共享节流与持久暂停，取代旧文档中“暂未实现共享限速”的描述；普通总结流程仍不批量预取。
# 加载恢复补充（2026-11-18）

加载判定与恢复：导航返回不代表页面已就绪。初读只有 AXWebArea、输入框骨架或笔记本设置时，在同一页面按15秒间隔观察，最多60秒；等待期间不得刷新或反复导航。仍未加载时，在同一IAB工作标签打开 https://gemini.google.com/app，以相同观察窗口等待账号和侧栏出现，确认 dq9568767@gmail.com 后从可见侧栏点击与登记URL一致的Notebook链接；未列出时使用页面的所有笔记本入口定位，不猜URL。点击后再按同样窗口观察。成功立即继续；明确登录/验证码/配额错误立即按规程停止；窗口耗尽只报告页面加载超时，不能推断账号失效或IAB不可用。每日启动不增加完整历史对话核验或无副作用交互要求，逐视频证据要求不变。
