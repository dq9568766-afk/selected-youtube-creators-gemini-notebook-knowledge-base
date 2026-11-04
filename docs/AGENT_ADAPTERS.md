# 智能体适配说明

本文将项目流程与智能体工具能力分开。技能文件描述共同规则；每个平台只负责提供文件、命令行和 Gemini 网页会话的入口。平台具备某项工具，不代表当前用户环境已经配置或登录。

2026-11-03 用户已授权历史批量改名例外：生成继续使用 Codex 内置浏览器，改名阶段可切到本项目外部 Chrome 扩展；增量不变。此例外不是浏览器恢复的替代路径，不得在内置浏览器生成故障时用外部浏览器继续发送。扩展仅改 Gemini 聊天，详细入口、租约及安装确认见 [HISTORY_RENAME.md](HISTORY_RENAME.md)。

WorkBuddy 适配：生成、发送、重试、账号/Notebook 核验和普通页面核验优先使用 WorkBuddy 内置浏览器。由于 WorkBuddy 内置浏览器无法加载扩展，只有显式选择历史扩展归档时，才在外部 Windows 浏览器加载并运行 `extensions/history-renamer`。外部浏览器不承担 Gemini 生成、重试、恢复或替代内置浏览器的故障处理。

适配信息核对日期：2026-10-28。平台的安装入口和工具能力可能变化，分发前应复核对应官方文档。

## 通用能力契约

开始队列操作前，当前智能体必须能完成以下检查：

1. 读取项目根目录中的 `AGENTS.md`、`ytg.py` 和 `docs/WORKER.md`。
2. 在项目根目录运行 `python ytg.py status`，并读取结构化结果。
3. 在不复制数据库的前提下访问项目配置的唯一任务数据库；本项目当前 Windows 部署使用 `%LOCALAPPDATA%\youtube-gemini-channel-summarizer\tasks.sqlite3`。
4. 通过用户已有的登录会话打开和操作 Gemini 网页，查看实际消息、生成结果、聊天 URL 和 Notebook 归属。
5. 在不确定消息是否提交时保留证据并遵守运行策略：strict暂停，collect-first按docs/WORKER.md登记单条延期；未知提交不重发。仅已确认提交且页面未跳转或中断时，才可按WORKER在20秒无正文并确认停止后于当前输入框重试一次；重试前不要求聊天地址。
6. Gemini 非提交 UI 动作前运行 `python ytg.py pace access --run RUN`；只在返回 ready=true 后立即执行一个动作。协调权只覆盖当前页面动作窗口，其他频道可以并行等待生成，但页面动作和提交必须按账号顺序执行；arm/retry 负责原子检查并登记提交时间；ready=false 时按返回的秒数等待，不做网页操作。

恢复约定：collect-first的临时连接/页面故障按docs/WORKER.md执行最多3次有限恢复；strict自动重连一次。已知账号或服务全局暂停后，用户明确继续/恢复才进行页面检查，不追加确认。恢复时读取详细租约状态，其他有效租约不得抢占。有限恢复失败时保留准确全局暂停、cleanup并结束，不重发或循环探测。

缺少任一必要能力时，不开始或恢复队列。说明具体缺少的能力，并等待用户配置或改用已具备这些能力的智能体。不得改用 Gemini API、私有 RPC、Cookie/Token、浏览器配置文件、远程调试或其他绕过方式。

## Codex

- 每次开始或恢复队列处理，先明确选用 Codex 内置浏览器并打开登记的 Gemini Notebook 工作标签，再运行队列状态命令。不要假定浏览器面板、工具存在或旧回合标签就代表本轮已建立可用会话；按 WORKER 验证页面可读和交互后才继续。
- 将本项目目录作为 canonical 技能源；Codex 个人技能入口可指向项目根目录，避免维护第二份内容。目录移动后需更新该入口。
- 使用 Codex 当前可用且已登录的浏览器会话。实际会话是否能访问 Gemini，以当前 UI 和工具检查为准。
- 按仓库 `AGENTS.md` 和 `docs/WORKER.md` 执行状态机；状态门槛适用于所有智能体。

## WorkBuddy

- WorkBuddy 文档支持以 `SKILL.md` 为入口、附带 `references/` 和 `scripts/` 的技能目录；平台市场创建与安装流程由 WorkBuddy 自身管理。
- 使用项目根目录的打包器生成 WorkBuddy 包：`python workbuddy_package.py --output PATH`。它只复制公开规则、脚本和扩展，不复制任务数据库、浏览器资料、凭据或摘要。
- 上传或安装生成目录中的 `SKILL.md` 所在目录，不要把 `%LOCALAPPDATA%/youtube-gemini-channel-summarizer/tasks.sqlite3` 打包进去。包内脚本位于 `scripts/`，执行时保持该包的项目根目录或明确使用 `python scripts/ytg.py`。
- 频道首次确认完成后，用 `python scripts/ytg.py channel-config CHANNEL_ID --rename-strategy RENAME_STRATEGY --title-format TITLE_FORMAT --membership MEMBERSHIP` 持久化选择；可选值分别是 `serial`/`extension`、`original`/`topic`/`date-topic`、`yes`/`no`/`unknown`。该命令不启动扫描、不发送 Gemini。
- 仅在当前 WorkBuddy 会话确实提供可操作 Gemini 页面、项目文件访问和 shell 时执行队列工作。启动前必须由 WorkBuddy 浏览器完成：打开登记 Notebook、读回完整页面、执行一次无副作用交互并读回结果，同时核对账号和 Notebook。仅有标签标题、URL、9222/CDP 端口或浏览器进程不算通过。
- 若 Agent Browser、Playwright MCP 或其他合规连接器未安装、未授权、未登录 Gemini，或无法核验 Notebook/聊天证据，则停止队列操作；不得改用外部 Chrome、远程调试、Cookie、Token、浏览器配置文件、Gemini API 或私有 RPC。
- 上一条限制不影响已明确授权的历史扩展例外：外部 Windows 浏览器只能在 `history-rename serve RUN` 后加载本项目扩展并执行改名/重开/核验；不能用它绕过 WorkBuddy 内置浏览器的生成、发送或恢复门槛。
- 可先运行只读自检：`python scripts/ytg.py preflight --channel CHANNEL_ID`。该命令永远不会替 WorkBuddy 伪造浏览器通过；返回 `ready:false` 时必须完成页面能力检查后再继续。
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

给浏览器集成维护者复现时，可报告：同一 Codex 内置浏览器中创建标签并打开 Gemini，确认 DOM 可读且搜索按钮可点击，标记 handoff 结束回合；下一回合 `tabs.list()` 仍含该标签，`tabs.get(id)` 后读取 DOM 报 `browser.user is unavailable`；在同一浏览器创建新标签后读取和点击恢复。当前仅有这些现象，不能归因到 Gemini 网页、账号或网络。此复现用于定位集成问题，不构成队列恢复或解除暂停的证据。
