# 自动化入口

提示词仅指向项目与任务类型。执行流程以 docs/WORKER.md 为准，可推进才读，同轮读一次。

## 首次历史（一次性任务）

收到频道后立即按项目规程连续处理历史视频，先保存总结再统一改名，不设置定时续跑。已确认的单视频错误跳过并记入最终人工核验清单，继续下一条；额度、登录、验证码、浏览器整体故障或未知提交仍须暂停。网页操作统一使用 Codex 内置浏览器及当前登录会话，不切换外部 Chrome。意外中断时尽可能保存状态并提醒用户，等待用户处理和明确要求后继续。全部历史处理完后运行 `report --mode history`，确认 complete=true 再汇报最终处理结果。

## 每日05:00增量（Asia/Shanghai）

```text
在项目目录 H:\BaiduSyncdisk\个人基础数据\obsidian代权的知识库\NOTE\04-Creator\AI\AI产品\youtobe视频批量总结保存（gemini）\项目根目录（包含 AGENTS.md、ytg.py 和 docs/WORKER.md 的目录），先运行 python ytg.py status；可推进时按 AGENTS.md 和 docs/WORKER.md 执行每日增量检查与归档，使用 incremental --scheduled。
```

## 发送给 Gemini 的内容

```text
{url}
```
