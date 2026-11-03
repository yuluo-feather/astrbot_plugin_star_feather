# AI 候选链与总预算

## 候选链

按顺序收集、按 provider id 去重：

1. 配置指定的解读模型（`ai.ai_provider`，从已加载 Provider 里按 id 查找）
2. 当前会话 Provider（`get_using_provider_async(umo)`）
3. 全局默认 Provider（`get_using_provider_async()`）
4. 全部已加载 Provider——仅当 `ai.all_providers_fallback` 打开

每个候选来源独立吞异常，但**吞掉必须留痕**：某一来源取用失败只意味着本签少一个候选，
不能变成「用户看到回退本地牌义、日志里一片安静」。

候选调用用异步接口（同步版已被框架标 deprecated）。

## 失败冷却

- 失败按 **Provider 粒度**记冷却（`ai.ai_fail_cooldown`，默认 60 秒），只冷却挂掉的那个，
  其余模型照常服务。
- 冷却期内跳过该 Provider，省一次空等超时。

### 本地排队超时不算 Provider 失败

`_chat_once` 先按同一截止点等并发闸的槽位；排队耗尽预算时返回 `QUEUE_TIMEOUT` 哨兵。
它是**本地资源不足**，不是服务失败，调用方不记冷却。全部候选都因排队退出时，日志会
单独说明「未记 Provider 冷却」。

## 总预算

一次占卜开**一笔** `AiBudget`（`ReadingService.run` 开头调一次 `new_budget`），喂给这次
占卜的全部 AI 阶段：

- 牌灵的话 + 深度解读，含候选重试与供应商回退，共用同一个截止点；
- 阶段之间不许重置截止点——这正是预算存在的意义；
- 默认 120 秒（`ai.ai_budget_seconds`）；设 0 表示不限制，构造时日志留一条告警；
- 牌灵的话另有 20 秒阶段上限，避免装饰性产出独吞整笔预算；
- 解读阶段不设阶段上限：总预算关闭时，公开口径就是「没有上界」。

预算见底时回退本地牌义，不再发起新请求。

## 并发闸

- 每个 Provider 一个 `asyncio.Semaphore`，上限 `ai.max_concurrency`（默认 2，0 = 不限制）。
- 按事件循环分开存：同一解释器可能被多个线程各跑一个循环使用；键用弱引用，争用过的
  条目在取闸时按「循环已关闭」清理。

## 出处

- 实现：`ai/chain.py`、`ai/budget.py`。
- 配置口径：README 的 `ai.*` 配置表。
- 预算与阶段上限的来历：v0.8.0 的发布记录与 `changelogs/`。
