# 自动化入口

## 历史首轮

仅使用用户指定频道及已核实的登记Gemini Notebook。在仓库根目录读SKILL.md、AGENTS.md、docs/WORKER.md，查一次status/lease，然后执行频道范围历史collect-first。使用同线程单个Goal，不创建周期heartbeat。

每条prepare、arm、在Notebook独立聊天发送URL换行加“总结”、等待、核实并登记。首轮不搜索、不重发；无正文120秒、正文持续增长最多300秒，异常直接延期，改名与最终核验失败登记archive_unverified。先确认停止当前生成再进入下一条。元数据复用扫描缓存。每10条至少休息5分钟并集中finalize；连续失败健康判断与有限浏览器恢复以WORKER为准。

账号登录、验证码、/sorry、配额或确认的账号/浏览器会话故障立即保留全局pause并cleanup；单频道未知提交、来源不可用和健康检查失败只保留该频道的pause，不阻断其他频道。只通知一次。已知阻塞后的自动续行不调用受影响范围的队列或浏览器，不重复提醒，等待用户明确恢复。正常处理只汇总批次进度，单条失败不询问。

首轮结束清空归档积压并运行频道report；complete=true才报告首轮完成，包含真实生成/核验数、成功率及所有延期清单。等待用户选择个别补做。不得把租约或CLI描述成无人值守后台工作。

## 每日05:00增量（Asia/Shanghai）

先status；检查全局pause及pauses中本频道的暂停，任一存在或其他worker持有有效租约则直接退出；其他频道暂停不阻断本频道。使用strict的incremental --scheduled --channel CHANNEL_ID；按WORKER扫描和归档，不自动清暂停或恢复历史。历史遇到daily_due在安全边界park，增量结束后以原policy重开同频道历史。

## Gemini消息

```text
{url}
总结
```
