# 智能体适配说明

本文将项目流程与智能体工具能力分开。技能文件描述共同规则；每个平台只负责提供文件、命令行和 Gemini 网页会话的入口。平台具备某项工具，不代表当前用户环境已经配置或登录。

适配信息核对日期：2026-10-28。平台的安装入口和工具能力可能变化，分发前应复核对应官方文档。

## 通用能力契约

开始队列操作前，当前智能体必须能完成以下检查：

1. 读取项目根目录中的 `AGENTS.md`、`ytg.py` 和 `docs/WORKER.md`。
2. 在项目根目录运行 `python ytg.py status`，并读取结构化结果。
3. 在不复制数据库的前提下访问项目配置的唯一任务数据库；本项目当前 Windows 部署使用 `%LOCALAPPDATA%\youtube-gemini-channel-summarizer\tasks.sqlite3`。
4. 通过用户已有的登录会话打开和操作 Gemini 网页，查看实际消息、生成结果、聊天 URL 和 Notebook 归属。
5. 在不确定消息是否提交时保留证据并遵守运行策略：strict暂停，collect-first按docs/WORKER.md登记单条延期；首轮不重发。

恢复约定：collect-first的临时连接/页面故障按docs/WORKER.md执行最多3次有限恢复；strict自动重连一次。已知账号或服务全局暂停后，用户明确继续/恢复才进行页面检查，不追加确认。恢复时读取详细租约状态，其他有效租约不得抢占。有限恢复失败时保留准确全局暂停、cleanup并结束，不重发或循环探测。

缺少任一必要能力时，不开始或恢复队列。说明具体缺少的能力，并等待用户配置或改用已具备这些能力的智能体。不得改用 Gemini API、私有 RPC、Cookie/Token、浏览器配置文件、远程调试或其他绕过方式。

## Codex

- 将本项目目录作为 canonical 技能源；Codex 个人技能入口可指向项目根目录，避免维护第二份内容。目录移动后需更新该入口。
- 使用 Codex 当前可用且已登录的浏览器会话。实际会话是否能访问 Gemini，以当前 UI 和工具检查为准。
- 按仓库 `AGENTS.md` 和 `docs/WORKER.md` 执行状态机；状态门槛适用于所有智能体。

## WorkBuddy

- WorkBuddy 文档支持以 `SKILL.md` 为入口、附带参考文件的技能目录；平台市场创建与安装流程由 WorkBuddy 自身管理。
- 分发到 WorkBuddy 市场或 Open Platform 时，按当时的格式要求补齐展示名称、双语描述、示例、版本和作者等平台元数据。本文仓库的 `SKILL.md` 是通用源格式，不假设所有平台专用元数据字段都能原样互通。
- 仅在当前 WorkBuddy 会话确实提供可操作 Gemini 页面、项目文件访问和 shell 时执行队列工作。若 Agent Browser 或其他连接器未安装、未授权、未登录 Gemini，或无法核验 Notebook/聊天证据，则停止队列操作。
- WorkBuddy 的技能安装成功不等于已具备浏览器、终端或 Gemini 登录权限；启动前逐项执行“通用能力契约”。

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
- 先读当前工具返回的浏览器文档与 `browser-troubleshooting`。选定的内置浏览器保持绑定；旧标签不可操作或列表为空时，在**同一浏览器**用已公布的 `browser.tabs.new()` 建一个工作标签，导航到已登记的 Gemini 笔记本或聊天 URL。不要因空列表重选浏览器、循环重置会话、切换外部 Chrome、反射内部属性或调用私有接口。只有接口明确报告浏览器本身断开时，才按工具文档重新取得浏览器和标签。
- 首次 `domSnapshot()` 只有 `- main` 时按 WORKER 的观察间隔等加载。实际恢复判据是完整页面内容可读，并且一次无副作用的语义操作（例如打开对话搜索）成功，且操作后的状态可读；标题、标签列表或单次快照不足以证明交互恢复。后续每次动作都以当前 DOM 为依据，不复用旧元素索引。
- 如果现有聊天的发送结果未知，按当前策略保留证据：strict核对原消息与归属，collect-first可按WORKER登记延期。连接恢复本身不清全局pause，也不授权重发。跨回合确有待保留页面才使用markHandoff，下一回合仍须实测。旧标签不可操作时在同一浏览器建新工作标签；旧标签存在不代表可用。
- 此流程规避了已观察到的旧标签句柄失效，但未修复浏览器集成底层实现，也不保证后续永不再出现。新标签仍不能完成读与交互时保留暂停并停止本次尝试，记录可见的错误类别供后续排查。

给浏览器集成维护者复现时，可报告：同一 Codex 内置浏览器中创建标签并打开 Gemini，确认 DOM 可读且搜索按钮可点击，标记 handoff 结束回合；下一回合 `tabs.list()` 仍含该标签，`tabs.get(id)` 后读取 DOM 报 `browser.user is unavailable`；在同一浏览器创建新标签后读取和点击恢复。当前仅有这些现象，不能归因到 Gemini 网页、账号或网络。此复现用于定位集成问题，不构成队列恢复或解除暂停的证据。
