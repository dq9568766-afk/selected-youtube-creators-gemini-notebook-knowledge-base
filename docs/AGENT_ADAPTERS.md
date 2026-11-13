# 智能体适配说明

本文将项目流程与智能体工具能力分开。技能文件描述共同规则；每个平台只负责提供文件、命令行和 Gemini 网页会话的入口。平台具备某项工具，不代表当前用户环境已经配置或登录。

平台规则：Codex 历史队列和 Codex 05:00 每日增量均使用 Codex 内置浏览器插件；WorkBuddy 的 Gemini 生成、发送、重试、恢复、普通核验和历史改名扩展继续使用其指定外部 Chrome。扩展仅改 Gemini 聊天，详细入口、租约及安装确认见 [HISTORY_RENAME.md](HISTORY_RENAME.md)。

WorkBuddy 适配：生成、发送、重试、账号/Notebook 核验、普通页面核验和历史扩展统一使用 WorkBuddy 指定的外部 Chrome 执行浏览器。扩展归档期间不得与 Gemini 生成并行。Codex 历史队列和05:00每日增量均使用内置浏览器插件。

适配信息核对日期：2026-10-28。平台的安装入口和工具能力可能变化，分发前应复核对应官方文档。

## 通用能力契约

开始队列操作前，当前智能体必须能完成以下检查：

1. 读取项目根目录中的 `AGENTS.md`、`ytg.py` 和 `docs/WORKER.md`。
2. 在项目根目录运行 `python ytg.py status`，并读取结构化结果。
3. 在不复制数据库的前提下访问项目配置的唯一任务数据库；本项目当前 Windows 部署使用 `%LOCALAPPDATA%\youtube-gemini-channel-summarizer\tasks.sqlite3`。
4. 通过用户已有的登录会话打开和操作 Gemini 网页，查看实际消息、生成结果、聊天 URL 和 Notebook 归属。
5. 在不确定消息是否提交时保留证据并遵守运行策略：strict及历史暂停；collect-first增量将单条未知提交登记为 `submission_unconfirmed` 延期，不重发，并在同一登记 Notebook 的新空白聊天继续下一条。仅已确认提交且页面未跳转或中断时，才可按WORKER在20秒无正文并确认停止后重试一次：切换到另一个指定模型，再点击已发送消息上的“修改提示”，修改“总结/请总结”措辞后重新提交；不得在输入框原样重发。重试前不要求聊天地址。
6. Gemini 非提交 UI 动作前运行 `python ytg.py pace access --run RUN`；只在返回 ready=true 后立即执行一个动作。协调权只覆盖当前页面动作窗口，其他频道可以并行等待生成，但页面动作和提交必须按账号顺序执行；arm/retry 负责原子检查并登记提交时间；ready=false 时按返回的秒数等待，不做网页操作。
7. 新频道的 Notebook 建立也是强制前置流程：全量扫描和日期补齐完成后，读取 `preflight` 的 `notebook_setup`。未登记时，当前智能体必须在自己获准的 Gemini 内置浏览器中创建频道 Notebook，核对账号、Notebook 名称和实际 URL，再执行 `python ytg.py notebook CHANNEL_ID NOTEBOOK_URL`。创建失败、账号不符或 URL 无法核验时停止，不进入 Gemini 总结。该流程不使用 Gemini API、私有 RPC 或浏览器凭据。

恢复约定：collect-first的临时连接/页面故障按docs/WORKER.md执行最多3次有限恢复；strict自动重连一次。已知账号或服务全局暂停后，用户明确继续/恢复才进行页面检查，不追加确认。恢复时读取详细租约状态，其他有效租约不得抢占。有限恢复失败时保留准确全局暂停、cleanup并结束，不重发或循环探测。

CLI 中 `summary`、`verify` 不是独立命令，而是 `step` 的 action。真实调用分别是 `python ytg.py step RUN CHANNEL VIDEO summary ...` 和 `python ytg.py step RUN CHANNEL VIDEO verify ...`；准备任务用 `python ytg.py prepare RUN`。`preflight.ready=false` 是因为 CLI 不代替浏览器认证；完成同一获准浏览器中的页面可读、无副作用交互和账号/Notebook核验后，按实际证据继续，不要把该字段误当成永久阻塞。

缺少任一必要能力时，不开始或恢复队列。说明具体缺少的能力，并等待用户配置或改用已具备这些能力的智能体。不得改用 Gemini API、私有 RPC、Cookie/Token、浏览器配置文件、远程调试或其他绕过方式。

## Codex

- Codex 05:00 每日增量自动化使用 Codex 内置浏览器插件；启动后确认登记账号，并按频道导航登记 Notebook URL。插件不可用、账号不匹配或页面无法打开时，按每日增量规程退出，不运行队列命令。

- 每次开始或恢复队列处理，先按任务类型选用浏览器：Codex 历史队列和每日增量用内置浏览器插件，WorkBuddy用指定外部 Chrome。每日增量确认账号后打开登记的 Gemini Notebook URL，再运行候选、begin或scan；不要求详细页面核验和无副作用交互。
- Codex 内置 Gemini 页面已验证可以读取无障碍/DOM 结构，能识别文本输入区、Google 应用按钮和当前账号。Codex 操作 Gemini 时优先使用 DOM/可访问性树和语义定位：role、accessible name、label、placeholder、稳定文本或 URL。每次操作后重新读取 DOM 验证结果，不以截图变化或点击调用返回作为成功证据。
- Gemini 部分控件位于 Shadow DOM，低层数字索引点击可能返回无法检查 Shadow Root。遇到此情况，若当前浏览器工具提供 Playwright/语义 locator，改用它定位；只有 DOM/语义接口无法操作时才有限度使用视觉或坐标，并在操作后回到 DOM 核验。该限制不证明 Gemini 或账号不可用。
- Codex 历史队列、每日增量及显式启用的 `history-renamer` 历史改名归档使用 Codex 自带的浏览器插件/内置浏览器。不得在同一运行中切换浏览器绑定。WorkBuddy 使用指定外部 Chrome。
- Codex 内置浏览器有扩展入口。Codex 扩展加载引导必须先给出绝对路径 `C:\Users\Administrator\.codex\skills\DQ-youtube-creator-gemini-notebook-knowledge-base\extensions\history-renamer`，提示选择整个目录并首次运行其中的 `install_native_host.cmd`；加载完成后再打开扩展面板并启动本机服务。
- Codex 内置浏览器的扩展消息不能假定始终提供标准 `sender.id`；扩展后台只按 `native-*` 消息前缀分流，并为一次性任务读取设置超时。Native Messaging 安装脚本同时注册 Chrome、Chromium 和 Edge 用户级路径；其他机器必须重新运行安装脚本生成本机配置。
- 新频道若 `preflight` 返回 `notebook_setup.registered=false`，先在 Codex 内置浏览器创建并核验该频道的 Gemini Notebook，再登记 URL；登记成功并重新通过页面核验前，不得开始扫描后的总结。
- 将本项目目录作为 canonical 技能源；Codex 个人技能入口可指向项目根目录，避免维护第二份内容。目录移动后需更新该入口。
- 使用 Codex 当前可用且已登录的浏览器会话。实际会话是否能访问 Gemini，以当前 UI 和工具检查为准。
- 按仓库 `AGENTS.md` 和 `docs/WORKER.md` 执行状态机；状态门槛适用于所有智能体。

## WorkBuddy

- WorkBuddy 文档支持以 `SKILL.md` 为入口、附带 `references/` 和 `scripts/` 的技能目录；平台市场创建与安装流程由 WorkBuddy 自身管理。
- 使用项目根目录的打包器生成 WorkBuddy 包：`python workbuddy_package.py --output PATH`。它只复制公开规则、脚本和扩展，不复制任务数据库、浏览器资料、凭据或摘要。
- 上传或安装生成目录中的 `SKILL.md` 所在目录，不要把 `%LOCALAPPDATA%/youtube-gemini-channel-summarizer/tasks.sqlite3` 打包进去。包内脚本位于 `scripts/`，执行时保持该包的项目根目录或明确使用 `python scripts/ytg.py`。
- 频道首次确认完成后，用 `python scripts/ytg.py channel-config CHANNEL_ID --rename-strategy RENAME_STRATEGY --title-format TITLE_FORMAT --membership MEMBERSHIP` 持久化选择；可选值分别是 `serial`/`extension`、`original`/`topic`/`date-topic`、`yes`/`no`/`unknown`。该命令不启动扫描、不发送 Gemini。
- 仅在当前 WorkBuddy 会话确实提供可操作 Gemini 页面、项目文件访问和 shell 时执行队列工作。启动前必须由指定外部 Chrome 执行浏览器完成：打开登记 Notebook、读回完整页面、执行一次无副作用交互并读回结果，同时核对账号和 Notebook。仅有标签标题、URL、9222/CDP 端口或浏览器进程不算通过。
- WorkBuddy 直接使用指定外部 Chrome 执行浏览器；若该执行浏览器未安装、未授权、未登录 Gemini，或无法核验 Notebook/聊天证据，则停止队列操作。不得使用远程调试、Cookie、Token、浏览器配置文件、Gemini API 或私有 RPC。
- 历史扩展仍在同一指定外部 Chrome 执行浏览器中按 `history-rename serve RUN` 使用；不得与 Gemini 生成提交并行。该 WorkBuddy 规则不改变 Codex 历史队列和每日增量使用内置浏览器插件的规则。
- 可先运行只读自检：`python scripts/ytg.py preflight --channel CHANNEL_ID`。该命令永远不会替 WorkBuddy 伪造浏览器通过；返回 `ready:false` 时必须完成页面能力检查后再继续。
- 若频道标题格式为 `date-topic`，WorkBuddy 必须先读取 `preflight`/`scan` 返回的 `date_backfill`。出现 `blocked=true` 时，不得发送 Gemini；若当前历史 run 持有租约，先 cleanup，再执行 `metadata-backfill start/run/report`，直到 `complete=true`。只有明确 `unavailable` 的日期可以保留 `all_dates_complete=false` 并继续其他视频。
- 新频道日期补齐完成后，WorkBuddy 必须读取 `preflight.notebook_setup`。若 `registered=false`，直接在指定外部 Chrome 执行浏览器中创建并核验频道 Gemini Notebook，再运行 `python scripts/ytg.py notebook CHANNEL_ID NOTEBOOK_URL` 登记；不得在未核验时发送总结。
- WorkBuddy 的 Gemini 操作同样采用 DOM-first：如果指定外部 Chrome 执行浏览器提供 DOM/无障碍树或 Playwright/语义 locator，优先用 role、accessible name、label、placeholder、稳定文本和 URL 定位；每次操作后重新读取页面核验。若当前会话只提供截图，或低层操作无法跨越 Gemini 的 Shadow DOM，才允许有限视觉/坐标降级；降级后必须回到页面/DOM证据，不能以点击返回成功代替核验。
- 标题格式为 `original` 时跳过主题词和日期流水线；`topic`/`date-topic` 使用用户明确启动的 `history-pipeline` 在全量扫描结束后批量生成本地主题词和 `final_title`。`date-topic` 的日期可由同一流程运行 metadata-backfill；独立日期补齐仍需用户明确要求。扩展只读取数据库最新标题，不负责主题词提取，不自动启动改名。
- WorkBuddy 默认直接使用指定的外部 Chrome 执行浏览器完成 Gemini 打开、账号/Notebook核验、生成、发送、重试和普通核验，不再要求用户对当前运行另行授权，也不先在 IAB 中启动任务。`Playwright Browser Automation` 或 `mcp__browser__*` 只有在绑定该执行浏览器时才可使用；独立未登录的 `agent-browser`/Chromium 不得替代它。不得使用 CDP、私有 RPC、Cookie、Token、浏览器配置文件或远程调试。
- 使用外部 Chrome 后仍先核对同一账号和登记 Notebook，再用一条 `attach → summary → 改名 → verify` 完整链路验证页面证据；验证失败按当前频道故障暂停，不批量发送。该规则适用于 WorkBuddy；Codex 每日增量使用内置浏览器插件的轻量账号与URL门槛，视频处理仍按 WORKER 完整核验。
- 先列出指定外部 Chrome 执行浏览器标签并认领当前用户已置前、且 URL 与登记 Notebook 匹配的真实标签；已有可用标签时不得新建后台标签。只有没有可用匹配标签，或原标签已确认失效时，才在同一执行浏览器创建新工作标签。
- 低层输入调用返回成功，不代表文字已落地。必须重新读取 DOM，确认输入框包含完整视频 URL 和“总结”、发送按钮已出现；若输入仍为空，使用同一外部 Chrome 执行浏览器的键盘焦点/输入通道重试一次，再回读 DOM。提交后确认输入框清空，并出现原消息或“停止回答/生成中”状态后才登记发送成功。
- 如果 Gemini 已生成完整回复但明确拒答、只说无法理解或没有实际视频内容，归类为 `content_failed`，不归类为浏览器故障；执行 `defer --value content_failed --next`。遇到程序能力拒绝或无正文时，下一次获准尝试不得原样重发：切换到另一个指定模型，再点击已发送消息上的“修改提示”，在“总结”前加“请”或去掉“请”，再提交修改后的提示；仍须遵守原有 `arm`/`retry`、证据和次数限制。验证无效后仍按 `content_failed` 处理。同频道达到项目规定的连续来源失败阈值后，由状态机暂停为 `source_unavailable`，不继续盲目发送。
- WorkBuddy 技能安装成功不等于已具备浏览器、终端、同一 Windows 用户的 canonical 任务库或 Gemini 登录权限；这些必须分别验证。

## Claude Code

- Claude Code 支持目录型自定义技能，入口为 `SKILL.md`；官方文档列出的个人和项目技能目录分别为 `~/.claude/skills/` 与 `.claude/skills/`。将本项目技能目录放在受支持的位置，或通过项目已有的分发机制引用 canonical 源。
- Claude Code 的运行环境可能能读仓库和执行 shell，但 Gemini 网页操作能力取决于用户实际配置的浏览器/MCP 工具及登录会话。不要假定 Claude Code 自带已登录 Gemini 浏览器。
- 只有当已配置工具能查看并操作网页、读回用户原消息与回复、确认聊天 URL 和 Notebook 归属时，才允许执行生成/核验步骤。否则可做只读项目说明或文档工作，队列保持不动。

## 其他智能体

先检查通用能力契约，再建立该平台的具体映射。只记录实测可用的工具名称、权限和限制；未经实测的行为标记为“未验证”。不要把一个平台的浏览器或文件系统假设复制到另一个平台。

## 来源

- WorkBuddy 技能说明：<https://open.workbuddy.cn/en/docs/skill>
- Claude Agent Skills 概览及 Claude Code 安装位置：<https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview>

### Codex 内置浏览器连接恢复（2026-10-28 实测）

- 实测跨回合保留的标签仍在 `browser.tabs.list()`，但 `browser.tabs.get(id)` 后的 `tab.playwright.domSnapshot()` 报 `browser.user is unavailable`；另一次故障后列表为空。同一浏览器中新建标签后，笔记本页面可读取，搜索按钮可点击并读回搜索弹窗。未标记 handoff 的工作标签在下一回合的列表中消失，符合工具文档所述的自动清理。由此可确认标签存在不等于可操作，`markHandoff()` 也不保证下回合可操作。错误发生在哪一层、为何旧标签失效，尚未确定；不能据此判断 Gemini 限流或网络故障。
- 先读当前工具返回的浏览器文档与 `browser-troubleshooting`。区分“标签失效”和“浏览器连接缺失”：标签列表为空、旧标签打不开或旧句柄不可读，但所选内置浏览器仍可用时，保持**同一浏览器绑定**，用已公布的 `browser.tabs.new()` 建工作标签，导航到数据库登记的 Gemini Notebook。不要因为标签为空就切换外部 Chrome、循环重置会话、反射内部属性或调用私有接口。若工具明确报告内置浏览器本身不可用，或浏览器清单中没有该集成，则无法由新建标签修复；按通用能力契约停止队列操作，不运行后续状态/提交命令，并等待集成恢复。仅当接口明确报告浏览器连接断开时，才按工具文档在同一获准集成内重连一次。
- 首次 `domSnapshot()` 只有 `- main` 时按 WORKER 的观察间隔等加载。实际恢复判据是完整页面内容可读，并且一次无副作用的语义操作（例如打开对话搜索）成功，且操作后的状态可读；标题、标签列表或单次快照不足以证明交互恢复。后续每次动作都以当前 DOM 为依据，不复用旧元素索引。
- 如果现有聊天的发送结果未知，按当前策略保留证据：strict核对原消息与归属，collect-first可按WORKER登记延期。连接恢复本身不清全局pause，也不授权重发。跨回合确有待保留页面才使用markHandoff，下一回合仍须实测。旧标签不可操作时在同一浏览器建新工作标签；旧标签存在不代表可用。
- 新标签加载后，先按 WORKER 的观察间隔等待；确认完整 Notebook 内容可读，再执行一次无副作用的页面交互并确认操作后状态可读。标题、URL、标签清单或单次 `- main` 快照都不足以证明可用。此流程能恢复已观测到的旧标签句柄失效，但不能修复浏览器集成底层，也不能保证问题不再出现。新标签仍不能完成读与交互时保留暂停并停止本次尝试，记录可见错误类别供后续排查。

- 历史总结、延期重试、人工复核后的重新总结和用户明确要求的重做，都只能从登记 Notebook 的空白入口创建；普通 Gemini 对话入口不是本频道历史总结的合规生成入口。每条提交后必须在同一 Notebook 内核验账号、原视频、完整正文和聊天归属，普通入口生成的聊天不能直接写入 Notebook 证据。
- Gemini 原生 Notebook 的稳定入口：新建或恢复工作标签后，先打开 `https://gemini.google.com/app`，等待完整页面和侧栏加载，再从侧栏的“笔记本”列表点击数据库登记的 Notebook 链接。不要把直接导航登记 URL 后只出现“笔记本设置”视为已核验；必须读回 Notebook 标题、账号和输入区，并完成一次无副作用交互。该入口顺序适用于 Codex 内置 Browser，不能因此改用外部 Chrome。

### Codex IAB 空标签与 Notebook 页面恢复（2026-11-09 实测）

- Codex 内置 IAB 的标签列表为空不代表浏览器故障。保持同一 IAB 绑定，在该 IAB 新建 Gemini 工作标签并打开 `https://gemini.google.com/app`；不得因空列表切换外部 Chrome。
- Gemini 主页面可能先只显示输入区和收起的侧栏。先展开侧栏并等待菜单；当前界面可能把 Notebook 入口放在“学生”页面的“你的学习笔记本”区域，而不是直接显示“笔记本”文字。只有完成主页面→侧栏入口→Notebook 区域的导航后，才可打开任务库已登记 URL；不得猜测或替换 URL。
- 直接打开登记 URL 后，AX/DOM 可能短暂只有 `AXWebArea`，或页面只显示通用的“Gemini Notebook/笔记本设置”。这不是成功证据，也不应立即改用外部 Chrome；重新读取页面，直到能读回登记 Notebook 标题、账号、输入区和已有对话。标题、URL、标签存在或截图变化单独都不足以通过门槛。
- 本次实测可通过的证据组合是：Notebook 标题 `thevalley101`、账号 `dq9568767@gmail.com`、输入区、历史对话均可读，且 URL 与数据库登记值一致；随后执行一次不发送消息的 `Tab` 交互并再次读回页面。交互失败或页面仍为空时，不运行 `status`、`begin`、`scan`，不改暂停、租约或任务库。
- `markHandoff()` 只能降低标签被自动清理的概率，不能证明下一回合标签仍可操作。每次自动化仍须重新选择/绑定 IAB 并现场读取；标签为空时重复上述建标签和入口核验流程。

给浏览器集成维护者复现时，可报告：同一 Codex 内置浏览器中创建标签并打开 Gemini，确认 DOM 可读且搜索按钮可点击，标记 handoff 结束回合；下一回合 `tabs.list()` 仍含该标签，`tabs.get(id)` 后读取 DOM 报 `browser.user is unavailable`；在同一浏览器创建新标签后读取和点击恢复。当前仅有这些现象，不能归因到 Gemini 网页、账号或网络。此复现用于定位集成问题，不构成队列恢复或解除暂停的证据。
