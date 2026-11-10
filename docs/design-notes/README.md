# 设计取舍记录

本目录记录「为什么这么实现」——踩过的坑、做过的取舍、红线的来历。
一个主题一个文件，按需增加；代码注释只保留当前规则与该主题的指路。

| 文件 | 主题 |
|---|---|
| [concurrency-and-locks.md](concurrency-and-locks.md) | 并发与锁：实例锁、模块级写锁、热重载与多进程边界 |
| [daily-cache.md](daily-cache.md) | 每日牌运缓存：单键记录、写回保护与失效条件 |
| [date-context.md](date-context.md) | 日期上下文：一次占卜只取一次日期 |
| [image-lifecycle.md](image-lifecycle.md) | 图片产物：实例目录、TTL 保险丝与启动清理 |
| [prompt-hardening.md](prompt-hardening.md) | 提示词防护：归一化、截断与越狱句式剥除 |
| [ai-chain-and-budget.md](ai-chain-and-budget.md) | AI 候选链与总预算 |
| [kv-degradation.md](kv-degradation.md) | KV 降级契约：存储故障与无记录的区分 |
| [host-decoupling.md](host-decoupling.md) | 宿主解耦任务书：日志 / Event DTO / LLM 网关三阶段与第二宿主准入条件 |
| [doc-voice.md](doc-voice.md) | 文档口吻与精简规则：口吻配额、破折号与 AI 味句式 |

约定：

- 代码里的注释写**规则**（做什么、不许做什么），本目录写**经过**（为什么、踩过什么）。
- 改了某条规则，同步改对应文档；文档与代码冲突时以代码为准，并回头修文档。
- 已发布的 changelog 是冻结记录，不回改；修正写进下一个版本的发布记录。
