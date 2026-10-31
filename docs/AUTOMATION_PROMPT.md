# 自动化入口

## 历史首轮

王志安频道的聊天标题使用“发布日期-1–5个汉字中文主题-原视频标题”；原视频标题完整保留，不使用英文主题。发布日期必须来自已登记元数据，缺日期的任务延期，不猜测日期。

仅使用用户指定频道及已核实的登记Gemini Notebook。在仓库根目录读SKILL.md、AGENTS.md、docs/WORKER.md，查一次status/lease，然后执行频道范围历史collect-first。使用同线程单个Goal，不创建周期heartbeat。

每条prepare、arm、在Notebook独立聊天发送URL换行加“总结”、等待、核实并登记；王志安标题必须是“发布日期-中文主题-完整原视频标题”，总结完成立即改名，再核验后连续处理下一条。Gemini非提交UI动作前运行 `python ytg.py pace access --run RUN`，ready=true后立即执行一个动作；arm/retry负责提交时间门控，ready=false时按剩余秒数等待并重试原命令。账号协调权只覆盖当前页面动作窗口，其他频道可以并行等待生成，页面动作和提交按账号顺序执行。普通动作及不同视频提交的最小间隔均为15秒；恢复期较长间隔按WORKER执行。默认不因单条完成暂停，不因单条完成cleanup；只有完成、明确阻塞、用户停止、规定冷却或交接时才释放租约。正常条目在当前页面一次核对准确标题、完整总结、原视频链接、账号与Notebook，使用current-page-video-summary；操作报错、页面中断、标题未更新或归属不明先按WORKER登记require-reopen，再在同一工作标签重开已有聊天核验。首轮不搜索；每次提交后20秒检查一次，首次无正文且原请求已确认、停止已确认、页面未跳转或中断时，通过retry登记后在当前原输入框重发一次，无需先取得聊天地址或额外确认同一聊天；再等20秒，仍未完成则停止并延期。完成后仍无可登记聊天地址记chat_unidentified；首次已有正文但未完成则停止并延期，不重发；未知提交和内容失败不重发。快速重试及预算按WORKER执行，其他异常按策略延期，归档未核验登记archive_unverified。先确认停止当前生成再进入下一条。元数据复用扫描标题、日期和视频身份；缺日期的王志安任务延期，不猜日期。每20条结清后至少休息120秒，成功和明确延期各计一次，续跑不重置；不增加抽查，不例行重开。连续失败健康冷却仍为300秒，有限浏览器恢复以WORKER为准。

账号登录、验证码、/sorry、配额或确认的账号/浏览器会话故障立即保留全局pause并cleanup；单频道未知提交、来源不可用和健康检查失败只保留该频道的pause，不阻断其他频道。只通知一次。已知阻塞后的自动续行不调用受影响范围的队列或浏览器，不重复提醒，等待用户明确恢复。正常处理只汇总批次进度，单条失败不询问。

首轮结束清空归档积压并运行频道report；complete=true才报告首轮完成，包含真实生成/核验数、成功率及所有延期清单。等待用户选择个别补做。不得把租约或CLI描述成无人值守后台工作。

## 每日05:00增量（Asia/Shanghai）

每天只触发一次。先status --channel CHANNEL_ID；检查全局及本频道pause，有暂停则安静退出。其他worker持有本频道有效租约时退出，由正在执行的Goal承担每日交接；其他频道暂停/租约不阻断本频道，不抢占租约。

空闲时使用begin --mode incremental --scheduled --channel CHANNEL_ID --policy collect-first，scan --channel CHANNEL_ID --run RUN，扫描成功才处理新ID。20次提交预算按频道/本地自然日持久化，超额pending留次日；单条异常延期继续，账号及服务阻塞保留暂停、通知一次，不反复唤醒。独立每日轮次用end结束，不自动启动历史。

正在执行历史的同一Goal遇到daily_due时，结清当前视频并handoff HISTORY_RUN，扫描并处理返回的同频道增量run。增量empty/daily_cap/round_cap后handoff DAILY_RUN，使用返回的原policy历史run立即继续，不结束Goal、不要求用户再次继续。扫描失败不能end或当作增量完成，按WORKER有限恢复/暂停。每日轮次扫描成功及预算结果与历史完成状态分别报告。

## Gemini消息

```text
{url}
总结
```


## 历史日期补齐（可选）

王志安当前任务要求发布日期进入聊天标题；缺日期时延期，不阻塞其他视频，但必须进入最终补齐/人工复核清单。

单独要求日期资料时才运行 `metadata-backfill`；它不发送 Gemini，也不自动重新入队延期总结。

元数据请求共享数据库节流：提取开始至少间隔10秒，每50次至少休息300秒，跨进程和重启保留；短分段等待。429、认证挑战及连接故障写入独立 YouTube metadata_access.blocked，立即停止，不轮换客户端、不换ID探测。此暂停不表示Gemini登录失效。用户明确恢复后使用 metadata-backfill resume --confirmed 清除元数据服务暂停；不清除其他范围暂停，不自动唤醒。普通扫描和单条查询也遵守此访问门槛。

补齐状态与总结状态分离：pending/fetching/complete/unavailable/review；每条事务保存，失败保留待处理。明确限制或确实没有公开日期记unavailable，未知异常或身份冲突停止复核。保留release_date、upload_date、采用来源和检查时间，不推算日期。补齐不发送Gemini、不重新入队延期总结，不改已有聊天名称。频道租约互斥，退出释放本轮占用。

报告区分补齐首轮结束与全部日期齐全：complete表示无pending/fetching/review；all_dates_complete及date_coverage说明实际覆盖率。不可用项目不算日期成功。此专用模式实现共享节流与持久暂停，取代旧文档中“暂未实现共享限速”的描述；普通总结流程仍不批量预取。
