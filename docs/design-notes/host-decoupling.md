# 宿主解耦任务书：AstrBot 作为可替换适配器

> 状态：草案 v0.1（2026-11-05）· 未开工 · 等主人点头后按阶段立 issue / 分支 / PR。
> 目标不是「消灭 AstrBot 依赖」，而是把 AstrBot 从应用核心的**隐性依赖**变成**可替换的宿主适配器**。
> 现有 `core / services / ai / render` 与 `adapters` 的分层保留，不重写、不重排目录。

## 0. 先分清「已具备」与「真缺口」

已具备（不在本任务书范围内，别为了架构整洁回炉）：

| 能力 | 现状 | 位置 |
|---|---|---|
| 配置中立 | `parse(config)` 一处转成 `Settings`，业务层只收 `Settings` | `app/container.py` · `core/settings.py` |
| 消息构造中立 | 业务层产出 `Reading`，`MessageChain` 只在适配器里造 | `services/reading.py` · `adapters/deliver.py` |
| KV 有适配器 | `PluginKV` + `kv_get` / `kv_put` 的故障降级语义 | `adapters/astrbot_io.py` · `adapters/kv_utils.py` |
| 平台能力判定在适配器 | 合并转发白名单在 `supports_forward`，业务逻辑不判断平台名 | `adapters/deliver.py` |

真缺口（三个阶段，按此顺序）：

1. 日志出口直连框架：`core/log.py`、`ai/hardening.py`、`services/render.py`、`render/store.py` 四处 `from astrbot.api import logger`。
2. 事件对象穿透到业务侧：命令/工具入口、限流、身份解析都直接读框架事件字段。
3. LLM 只有表面注入：`AiInterpreter` 仍通过 `self.context` 取 Provider。

## 1. 阶段一：日志中立化

**范围**：只换日志出口，不换日志体系、不改级别与格式、不另开文件。

| 文件 | 动作 |
|---|---|
| `core/log.py` | 改为 stdlib `logging` 门面：`getLogger("star_feather")` + `NullHandler`，保留 `log` 名字，调用点不动 |
| 新增 `adapters/log.py` | 把中立 logger 的记录转发到 `astrbot.api.logger`，保持 `astrbot.plugin.<插件名>` 路由；绑定幂等，防热重载重复加 handler |
| `main.py` | 插件实例化最早处完成绑定；绑定失败只退回 stdlib 默认，不许让插件加载失败 |
| `ai/hardening.py` · `services/render.py` · `render/store.py` | 改从 `core.log` 取 logger，删掉直连框架的 import |

**验收**

- `rg "from astrbot" core services ai render app` 零命中（`adapters/` 与 `main.py` 除外）。
- 运行日志仍落在 `astrbot.plugin.astrbot_plugin_star_feather` 这条路由下（实机或日志单测各一次）。
- 全量 pytest 与 `finish_check` 绿。

**不做**：日志格式统一、第三方日志库、日志落盘策略。

## 2. 阶段二：Event DTO + Messenger 注入

**原则**：适配器读原始事件并解析身份；应用层只收中立 DTO。DTO 缺字段时**加字段或加端口能力**，不许把原始 `event` 整个塞进去。

| 文件 | 动作 |
|---|---|
| 新增 `app/dto.py` | `IncomingEvent`：`user_id`（已解析，含平台前缀口径）、`platform_id`、`conversation_id`、`message_text`、`platform_name`。不含原始事件 |
| 新增 `adapters/event.py` | `incoming(event)` 构造 DTO；`mark_handled(event)` 承接 `should_call_llm(True)` 这类宿主动作；`core/identity.py` 的二级降级（int 型 ID、`message_obj.sender.user_id`、平台前缀）移到这里，纯转义逻辑可留在 core |
| `app/handlers.py` | 入口签名改为 `(container, incoming, messenger, …)`；移除 `getattr(event, …)` 与 `should_call_llm`；`note_entry_binding` 移到 `main.py`（宿主侧诊断） |
| `services/gating.py` | `check(incoming, for_command)`；键值口径不变：`sf_cmd_cd_{umo}` / `sf_tool_cd_{umo}` / `sf_cmd_cnt_{platform_id}:{uid}` |
| `services/reading.py` | `ReadingContext` 收 DTO（或继续收 uid/umo 两字段；倾向前者，保持只有一种输入形状） |
| `adapters/deliver.py` · `adapters/presenter.py` | `present` / `deliver` 去掉 `event`，改收 `can_forward: bool`；`supports_forward(platform_name)` 留在适配器内 |
| `app/container.py` | 只负责装配；DTO 转换与 messenger 构造由 `main.py` 调适配器完成 |
| `main.py` | 先 `mark_handled` + `note_entry_binding`，再构造 DTO / messenger 调 handlers |

**测试面**：`tests/stubs.py` 补 DTO 与 Messenger 假件；`test_entries`、`test_tool_entry`、`test_gating`、`test_identity`、`test_deliver`、`test_fault_injection`、`test_config_freshness` 跟签名。

**验收**

- `app/handlers.py` 与 `services/gating.py` 不再出现 `getattr(event` / `event.`。
- 事件字段只出现在 `adapters/` 与 `main.py`。
- 行为回归要钉**取值**：限流键名、身份键、收尾句顺序、发送失败兜底；只钉返回值不够。
- 全量 pytest 与 `finish_check` 绿，双入口实机各跑一次。

**不做**：目录重排、新建 `ports/` 包、PlatformCapabilities。

## 3. 阶段三：LLM Provider Gateway

**原则**：只抽当前真正用到的能力。预算、冷却、并发、候选顺序、结构校验**全部留在解释器**，网关只负责「拿句柄 / 认 id / 发一次请求」。

| 文件 | 动作 |
|---|---|
| 新增 `ai/gateway.py` | Protocol：`async current(umo) -> handle`、`all() -> list[handle]`、`identify(handle) -> str`、`async chat(handle, prompt, system_prompt) -> str` |
| 新增 `adapters/llm.py` | 包 `context.get_using_provider_async` / `context.get_all_providers` / `provider.meta().id` / `provider.text_chat` |
| `ai/chain.py` | `AiInterpreter(context, …)` → `AiInterpreter(gateway, …)`；删掉 `self.context`；时钟继续可注入 |
| `app/container.py` | 构造网关并注入 |

**测试面**：`tests/stubs.py` 提供 FakeGateway；`test_core`、`test_ai_budget`、`test_fault_injection`、`test_structure_check`、`test_spirit` 共 28 处构造跟着换。

**验收**

- `ai/chain.py` 零 `context.`。
- 现有预算 / 冷却 / 并发 / 结构校验用例全绿——它们就是行为契约，不另写等价断言。
- 候选来源全挂时仍回退本地牌义，日志仍能指出缺席来源。

**不做**：多模态 / 工具调用统一、Provider 配置 UI、预算语义调整、通用 LLM 框架。

## 4. 第二宿主准入条件（不绑版本号）

三项同时满足，才把「接入第二宿主」列为可开工任务：

1. **导入隔离**：`core / services / ai / render / app` 不直接导入 AstrBot。
2. **装配隔离**：一个测试能用替代适配器把 `Container` 装起来，不传 AstrBot 的 `context` / `plugin`。
3. **行为隔离**：抽牌 → 身份 → 限流 → 解读 → 交付全链在假适配器下跑通。

建议把这三项作为设计红线写进测试，而不是等接第二个宿主时人工检查。

## 5. 明确暂不处理

- `ports/` 目录重排、`core/ports.py` 拆文件：`Clock` / `Messenger` 目前无人引用，先把端口真正接进 `Container` 的类型，再谈组织。
- PlatformCapabilities：当前白名单已在 `adapters/deliver.py`，且只有一个宿主；第二个宿主出现时按真实差异定。
- Storage Protocol 与原子更新：上游 #10990 未提供，不在插件侧假承诺跨进程原子。
- 多进程 / 热重载锁边界：见 `concurrency-and-locks.md`，与宿主解耦无关。
- 通用配置 schema、跨宿主 Provider 抽象：不做。

## 6. 风险与顺序理由

- 阶段一碰全局日志绑定：幂等必须显式处理，绑定失败只能降级，不能带走插件加载。
- 阶段二碰三入口与限流 / 身份键：最容易出的是「看起来没变、键值悄悄变了」，回归测试要钉字段值。
- 阶段三碰预算与冷却：错了通常不是测试红，而是线上行为慢慢变；放最后，且以现有用例为契约。
