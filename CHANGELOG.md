# 更新日志

> 每个版本条目均为中文在前、英文在后。

#### v0.5.5

##### 🔧 修复

- **越狱绕过防护增强（续）**：换行夹在越狱句中间不再能绕过——此前跨行换行拼接的越狱句式可躲过句式剥除
- **每日牌运泛时间词补充**：「这个月 / 这两天 / 这段时间 + 无主题」的泛问按当日牌运处理——此前这类问法会被判为普通占卜
- **判定边界加固**：①泛时间词收紧为**正向白名单**——「最近怎么样 / 每日塔罗」仍是当日牌运，「最近总是失眠 / 今天下午开会吗 / 今天真的好累」这类事件/日程/状态描述走具体占卜（旧规则「时间词+无主题」会把它们全吞进固定牌运）；②「我们的未来 / 她未来会爱我吗」这类关系语义+时间线词改用关系阵（恋羽十字），纯时间线问法「我的未来」不变

**English**

##### 🔧 Fixes

- **Jailbreak bypass protection, continued**: newlines inside a jailbreak phrase can no longer slip through — previously multi-line jailbreak phrasings could evade the matcher
- **More daily-reading time words**: generic questions with "这个月 / 这两天 / 这段时间" (this month / these days, no specific topic) now get the fixed daily reading — previously they fell into normal readings
- **Judgement boundary tightened**: ① time-word questions are now a positive whitelist — "how have I been lately / daily tarot" still count as daily fortune, while event/agenda/state descriptions ("I keep losing sleep lately", "is there a meeting this afternoon", "I'm so tired today") get a free draw (the old "time word + no topic" rule swallowed them all); ② relationship semantics + timeline words ("our future", "will she love me") now pick the Lovers' Cross (relationship spread); pure timeline questions ("my future") stay unchanged

#### v0.5.4

##### 🔧 修复

- **AI 失败冷却按提供商隔离**：同时配多个模型时，单个模型故障只冷却它自己，其余模型照常出解读——此前任一模型故障会让所有用户 60 秒内都拿不到 AI 解读
- **越狱绕过防护增强**：全角字符 / 零宽 / bidi 控制符不再能绕开注入句式剥除——此前用全角字母（ｉｇｎｏｒｅ）或零宽字符隔开（忽‌略‌上‌文）可绕过过滤

**English**

##### 🔧 Fixes

- **Provider-isolated AI failure cooldown**: with multiple models configured, a failing model only cools itself down while others keep serving readings — previously any single model failure left all users without AI readings for 60 seconds
- **Stronger jailbreak bypass protection**: full-width characters / zero-width / bidi control characters can no longer bypass injection stripping (previously e.g. "ｉｇｎｏｒｅ" or "忽‌略‌上‌文" slipped through)

#### v0.5.3

##### 🔧 修复

- **每日牌运判定收紧**：「最近 / 近期 / 每日」不再单独作为运势词——带具体主题（感情、事业、学业、他/她等）的问题按具体问题正常选阵并自由随机，不再被误吞进当日固定牌运（此前「最近她对我什么感觉」「我最近学业怎么样」问什么都拿到同一张牌、同一段解读）
- **每日牌运解读按主题区分**：同一天询问不同主题（如「今天感情运势」「最近学业怎么样」）各自生成并缓存解读，不再共用当天第一次生成的解读；无主题泛问（当日牌运）仍当天固定同一段
- **AI 解读牌义锚定**：解读须基于抽牌结果括号内给出的牌义关键词展开，不过度偏离、不自创牌义
- **帮助请求判定收紧**：只有整句是帮助请求（「帮助」/「help」/「使用帮助」/「看下说明」等）才显示使用说明；问题正文里带「帮助」二字（如「帮助我做出决定」）不再误触发帮助页——此前这类占卜问题收到的却是帮助文案

**English**

##### 🔧 Fixes

- **Tighter daily-reading detection**: "最近 / 近期 / 每日" (recently / daily) no longer counts as a fortune word on its own — questions with a specific topic (love, career, study, him/her, etc.) now go through normal spread selection and free draws instead of being swallowed by the fixed daily reading (previously "what does she think of me lately" or "how's my study lately" always got the same card and the same reading all day)
- **Topic-aware daily readings**: asking different topics on the same day (e.g. today's love fortune / my study lately) now generates and caches a reading per topic instead of reusing the first one of the day; generic fortune questions (no specific topic) still stay fixed for the day
- **AI reading anchored to card meaning**: readings must be based on the meaning keywords given in the draw result, without drifting too far or inventing meanings
- **Tighter help detection**: only a standalone help request ("帮助 / help / 使用帮助 / 看下说明" etc.) shows the usage guide; the word "帮助" (help) inside a question body (e.g. "help me decide") no longer trips the help page — previously such divination questions got the usage text instead

#### v0.5.2

##### 🔧 修复

- **命令出错不再泄露内部细节**：占卜/单抽执行异常时，用户只收到固定友好文案（如「这场占卜断了」），异常详情（服务器路径、内部结构）只记录进日志——此前异常原文会被直接格式化进回复
- **依赖安全底线**：`Pillow` 下限提升到 `>=10.0.0`——9.x 存在多个已知 CVE（含 CVE-2023-4863 堆溢出），渲染链不冒这个险；安装时自动升级

**English**

##### 🔧 Fixes

- **No more internal details on command errors**: when a reading / single-draw fails, the user only gets a fixed friendly notice (e.g. "this reading broke"); exception details (server paths, internals) go to the server log only — previously the raw exception text was formatted into the reply
- **Dependency floor**: `Pillow` bottom raised to `>=10.0.0` — 9.x ships known CVEs (incl. CVE-2023-4863 heap overflow); not worth the risk in the render path; auto-upgraded on install

#### v0.5.1

##### 🔧 修复

- **工具入口非占卜分支措辞**：工具未开启 / 节流 / 每日上限 / 空问题等场景，不再复述「占卜结果已发送」这类与事实不符的收尾，改为对应的引导语

**English**

##### 🔧 Fixes

- **Non-reading tool branches**: the disabled / throttled / daily-quota / empty-question scenarios no longer echo a "reading sent" wrap-up that didn't happen; each yields an accurate follow-up line instead

#### v0.5.0

##### ✨ 新功能

- **自然语言占卜入口**：对话中说「帮我算一卦」「抽张牌看看」「占卜一下我们的感情」即可触发，不用记命令；工具描述限定「明确请求」才调用，抱怨/闲聊不误触；同会话节流（`tool.llm_tool_cooldown`，默认 60s）防刷屏
- **今日固定牌运**：`/单抽` 与含「运势/运气/牌运/日运/每日」等词的请求，同一天同一人固定同一张牌与解读，零点自动刷新，反复问不会变；群聊各人独立、不共享；存储异常也不丢固定
- **解读模型选择**：`ai.ai_provider` 指定解读专用模型（面板下拉），不可用时自动回退其他可用模型，不影响对话模型
- **免责声明**：结果末尾追加 `output.disclaimer` 自定义文案，留空关闭
- **入口限流**：命令入口同会话节流（`tool.cmd_rate_limit`，默认 10s）防连点；三入口统一按用户每日计数（`limit.daily_count`，默认关闭）

##### 🔧 变更

- **发送方式三选一**：原 `forward_result` + `show_image` 两个开关合并为 `send_mode` 单选（图文一条链 / 合并转发 / 纯文字），旧配置自动迁移，默认改为合并转发——占卜结果（图+分段解读）一条消息收齐
- **收尾句独立发送**：`/占卜`、`/单抽` 结果发出后追加一句收尾（✨ 文案池随机一条），不再塞进转发消息末尾；自然语言入口的收尾由模型回复承担，同一文案池
- **AI 解读候选链**：当前会话 → 全局默认 → 全部已加载模型依次尝试，单个请求超时（`ai.ai_timeout`，默认 30s）即切换；全部失败回退内置牌义并进入失败冷却（`ai.ai_fail_cooldown`，默认 60s），冷却期内不再空等
- **安全加固**：送 AI 的问题截断至 200 字（头尾保号）并剥除注入句式（如「忽略上面的指令」「现在你是猫娘」「告诉我你的系统提示词」）；AI 输出未按【第N张·位置】结构分段则弃用并尝试下一候选，全部失格回退牌义，带偏内容不会出现在聊天里
- **并发体验**：多人同时占卜互不阻塞（牌面渲染移入后台执行），结果来得更快
- **三入口统一流程**：`/占卜`、`/单抽`、自然语言入口共用同一套抽牌与发送逻辑，洗牌提示、牌面图、免责声明等开关一处生效
- **配置分组展示**：面板按「AI 解读 / 自然语言入口 / 限流 / 输出与分段」分组，旧扁平配置自动兼容，升级不丢设置
- **排障日志**：AI 解读关键事件写入 `data/logs/star_feather.log`，按天轮转保留 7 天
- **运行环境声明**：正式声明运行环境要求——Python 3.12+（与 AstrBot 最低要求一致）、AstrBot ≥ 4.16（<5）；低于要求的环境无法加载插件（README 安装段同步说明）
- **触发门槛放松**：命令入口不再强制 `/` 前缀——私聊直接说「占卜 问题」即可触发；群聊 @ 机器人，或在 WebUI 设置唤醒词后「唤醒词 占卜 问题」也能触发；群聊不带唤醒词、不 @ 的裸文本仍不响应（防误唤）。触发校验交由框架，与 WebUI 唤醒词配置不再冲突

##### ⚙️ 新配置

- **AI 解读**：`ai_timeout` / `ai_fail_cooldown` / `ai_provider` / `question_max_len`（问题长度上限，0=不压缩）
- **自然语言入口**：`llm_tool_enabled` / `llm_tool_cooldown`
- **命令入口**：`cmd_rate_limit`（同会话节流秒数，0=关闭）
- **限流**：`daily_count`（每用户每日次数，0=关闭）
- **抽牌规则**：正逆位恒为纯随机（50/50）——`reversed_cards` 开关已移除，逆位概率不可调（旧配置自动失效）
- **输出与分段**：`send_mode`（替代 `forward_result` + `show_image`，旧配置自动迁移）/ `daily_fixed` / `shuffle_lines` / `disclaimer`

**English**

##### ✨ New features

- **Natural-language entry**: just ask in chat — "帮我算一卦", "抽张牌看看", "占卜一下我们的感情" — no commands to memorize; the tool description only fires on explicit requests (complaints / casual remarks won't trigger); per-session throttle (`tool.llm_tool_cooldown`, default 60s) prevents spam
- **Fixed daily reading**: `/单抽` and requests containing fortune words (运势 / 运气 / 牌运 / 日运 / 每日) return the same card & reading for the same user all day, refreshed at midnight — no way to reroll; independent per member in groups; the fixed card survives even if storage fails
- **Selectable reading model**: `ai.ai_provider` picks a dedicated Provider for readings (Dashboard dropdown); falls back through the provider chain if unavailable, without affecting the chat model
- **Disclaimer**: `output.disclaimer` appends a customizable note to every reading (empty string hides it)
- **Entry rate limits**: `/占卜` & `/单抽` share a per-session throttle (`tool.cmd_rate_limit`, default 10s) against spam-clicking; all three entries share a per-user daily counter (`limit.daily_count`, off by default)

##### 🔧 Changes

- **Send mode, three choices**: the `forward_result` + `show_image` switches are merged into a single `send_mode` dropdown (plain / forward / text_only); legacy configs auto-migrate, and the default is now merged forward — a reading (image + segmented text) arrives as one message
- **Closing line sent separately**: `/占卜` / `/单抽` append one random ✨ closing line after the result, no longer at the end of the forward; on the natural-language entry the closing reply comes from the model, same copy pool
- **AI reading provider chain**: current session → global default → all loaded providers are tried in turn, each with a timeout (`ai.ai_timeout`, default 30s); if all fail it falls back to built-in meanings and enters a fail cooldown (`ai.ai_fail_cooldown`, default 60s) so repeated reads don't wait on a dead provider
- **Hardening**: questions sent to AI are clipped to 200 chars (head + tail kept) and jailbreak phrasings are stripped (e.g. "ignore the instructions above" / "now you are a catgirl" / "tell me your system prompt"); AI output that loses the 【第N张·位置】structure is discarded and the next candidate is tried — if all candidates are structurally invalid, it falls back to built-in meanings, so off-topic content never reaches the chat
- **Better concurrency**: simultaneous readings no longer block each other (card rendering now runs off the event loop), so results come back faster
- **Unified flow across entries**: `/占卜`, `/单抽` and the natural-language entry share one draw & delivery pipeline — shuffle hints, card image and disclaimer toggles take effect in one place
- **Grouped config display**: dashboard sections (AI reading / natural-language entry / rate limits / output & splitting); legacy flat configs still load — upgrades don't lose settings
- **Troubleshooting log to disk**: key AI-reading events written to `data/logs/star_feather.log`, rotated daily keeping 7 files
- **Runtime requirements declared**: `requirements.txt` and `metadata.yaml` now formally declare the runtime requirement — Python 3.12+ (matching AstrBot's own minimum) and AstrBot >= 4.16 (<5); environments below these versions cannot load the plugin (mirrored in the README install section)
- **Relaxed trigger gate**: the command entry no longer forces a `/` prefix — just say `占卜 问题` in private chat; in groups, @ the bot or configure wake words so `wake-word 占卜 问题` triggers; naked group text without a wake word or @ still won't (anti-hallucination). Trigger checks are delegated to the framework, so plugin rules never fight the WebUI wake-word config

##### ⚙️ New config

- **AI reading**: `ai_timeout` / `ai_fail_cooldown` / `ai_provider` / `question_max_len` (question length cap, 0 = no clipping)
- **Natural-language entry**: `llm_tool_enabled` / `llm_tool_cooldown`
- **Command entry**: `cmd_rate_limit` (per-session seconds, 0 = off)
- **Rate limits**: `daily_count` (per-user daily cap, 0 = off)
- **Draw rules**: upright/reversed is always pure random (50/50) — the `reversed_cards` toggle was removed; the reversed ratio is not configurable (legacy setting no longer applies)
- **Output & splitting**: `send_mode` (replaces `forward_result` + `show_image`, legacy configs auto-migrate) / `daily_fixed` / `shuffle_lines` / `disclaimer`

#### v0.4.10


- 修复：合并转发节点 uin 改用框架统一接口 `event.get_self_id()`（原依赖适配器 `raw_message`，部分平台会取空退回 `'0'`），与框架的 @ 唤醒判定同一套取值逻辑

**English**


- Fix: merged-forward node `uin` now uses the framework API `event.get_self_id()` (previously relied on adapter `raw_message`, which some platforms omit, falling back to `'0'`) — same value source as the framework's `@` wake-up check

#### v0.4.9


- 清理：误提交的 `commit_msg.txt`（发版脚本临时产物）移出版本库并加入 `.gitignore`；4 个冗余「皇后」素材副本（与「王后」字节完全相同）删除，包体瘦身约 0.47MB
- 修复：`/占卜 帮助` 分支行为与其余路径一致（补齐收尾抑制，帮助页之后不再跟一串默认回应）
- 改进：渲染前用 fonttools 校验文本字形覆盖，内置子集缺字时自动回退到能覆盖该系统字体（新增依赖 `fonttools>=4.0`）
- 新增 `tests/` 单测（pytest，27 例）：选阵、别名剥离、分段、抽牌、字体覆盖与渲染冒烟（conftest 打桩框架 API，不依赖 AstrBot 环境）
- 文档：中英 README 补充品牌化命名说明（`StarFeather-*.otf` 实为 Noto Sans SC 子集）、缺字回退机制与测试运行说明

**English**


- Cleanup: mistakenly committed `commit_msg.txt` (release-script artifact) removed from the repo and added to `.gitignore`; 4 duplicate "Queen" assets (byte-identical to the "王后" files) deleted — package shrunk by ~0.47MB
- Fix: the `/占卜 帮助` branch now behaves like every other path (it suppresses the default follow-up after the help page)
- Improvement: fonttools now validates glyph coverage before rendering; if the built-in subset lacks a glyph, it falls back to a system font that covers the text (new dependency `fonttools>=4.0`)
- Added `tests/` unit tests (pytest, 27 cases): spread selection, alias stripping, splitting, drawing, font coverage and render smoke tests (conftest stubs the AstrBot API — no framework required)
- Docs: EN/zh-CN README updated — branded font names (`StarFeather-*.otf` are Noto Sans SC subsets), glyph-fallback mechanism, test instructions

#### v0.4.8


- 全新插件图标：粉色羽毛 × 太阳塔罗牌 × 金星（Pastel Feather Tarot）
- README 全新排版：Logo + 标题横排居中、粉色徽章（License / AstrBot / 版本）、目录导航、资源改 GitHub 绝对链接（GitHub / AstrBot 面板 / 市场渲染一致）

**English**


- New plugin icon: pink feather × Sun tarot × Venus (Pastel Feather Tarot)
- README redesign: logo + title horizontally centered, pink badges (License / AstrBot / version), table of contents, all assets switched to absolute GitHub links (consistent on GitHub / AstrBot panel / marketplace)

#### v0.4.7


- 内置 Noto Sans SC 子集字体（OFL 开源许可）：跨平台渲染中文一致，Linux/macOS 不再回退方块字
- 修复：`@register` 注册版本号与 `VERSION` 不一致（0.4.5 → 0.4.7）
- 代码通过 ruff 规范检查；新增插件图标 `logo.png`、metadata `short_desc` 与 tags、`requirements.txt`（Pillow）、AGPL-3.0 许可证与中英双语 README
- 全量校验：78 张牌素材路径 100% 命中，渲染回归通过

**English**


- Bundled Noto Sans SC subset fonts (OFL license): consistent Chinese rendering across platforms — no more tofu on Linux/macOS
- Fix: `@register` version mismatched `VERSION` (0.4.5 → 0.4.7)
- Code passes ruff checks; added plugin icon `logo.png`, metadata `short_desc` + tags, `requirements.txt` (Pillow), AGPL-3.0 license and bilingual README
- Full validation: 100% of 78 card asset paths resolve; render regression passed

#### v0.4.6


- 洗牌提示改为**随机文案池**：从多条内置文案随机选取，不再固定一句

**English**


- Shuffle hint now picks randomly from a pool of lines instead of one fixed sentence

#### v0.4.5


- AI 解读前自动剥离用户文本中的阵名：`/占卜 圣三角 考研如何` 的解读只看到「考研如何」，不再混入「圣三角」干扰语义
- @全体成员兼容两种形态：`At(qq="all")`（NapCat 等 OneBot 适配器）与 `AtAll`（子类），不支持的平台自然拒绝，无刷屏风险

**English**


- AI reading strips spread names from the user's text first: `/占卜 圣三角 考研如何` gets read as "考研如何", no longer confused by "圣三角"
- @everyone detection supports both forms: `At(qq="all")` (OneBot adapters like NapCat) and `AtAll` (subclass); platforms without @everyone produce no such component, so no spam risk

#### v0.4.4


- 选阵升级为**三层决策**：显式指定阵名 > 关键词权重累计（不再「命中即返回」）> 内容推断 > 兜底；支持 `/占卜 圣三角 考研如何` 式显式指定，新名与经典名（时间之流 / 三张时间线 / 恋人十字 / 单张问询 等）均可
- 关键词按命中数计权重取最高分，平局按优先级顺序（如「工作面试和我感情」正确选中羽镜）；全链路常数级字符串扫描，无性能影响

**English**


- Spread selection upgraded to a **three-layer decision**: explicit spread name > keyword weight accumulation (no longer "first hit wins") > content inference > fallback; supports `/占卜 圣三角 考研如何` explicit naming with both new and classic names (时间之流 / 三张时间线 / 恋人十字 / 单张问询 etc.)
- Keywords counted by weight, highest wins; ties resolved by priority order (e.g. "工作面试和我感情" correctly picks Feather Mirror); whole chain is constant-time string scanning — no performance impact

#### v0.4.3


- 触发规则与文档对齐：群聊须 **@ 机器人**（At 命中自身 QQ）才响应，未 @ 且不带 `/` 的消息不再触发，避免刷屏
- 解读文本结构化分段后按 `segment_size` 二次截断：模型无视字数限制产生超长段落时也能正常发送
- 牌面图渲染结果为空或非字符串时回退文字版

**English**


- Trigger rules aligned with docs: group chats require **@ the bot** (At matching self QQ); messages without @ or `/` no longer trigger — no spam
- Structured segments are re-split by `segment_size`: oversize paragraphs survive even when the model ignores the limit
- Card image fallback: empty or non-string render results fall back to text

#### v0.4.2


- 健壮性：结构化分段不再依赖换行，AI 连续段落（如「【第1张】…【第2张】…」）也能正确切分
- AI 解读 prompt 改为 f-string 构造，用户问题中的 `%` / `{}` 等字符不影响格式化
- 配置 `segment_size` 显式兜底：未设置 / `None` / 非法值一律回默认 300；发送间隔与默认分段长度收敛为模块常量
- 插件注册版本号同步为 v0.4.2

**English**


- Robustness: structured splitting no longer depends on line breaks — consecutive paragraphs (e.g. "【第1张】…【第2张】…") split correctly
- AI reading prompt built with f-strings: `%` / `{}` in the user's question no longer break formatting
- `segment_size` explicit fallback: unset / `None` / invalid values all default to 300; send interval and default split length consolidated into module constants
- Plugin register version synced to v0.4.2

#### v0.4.1


- 牌阵更名：单张问询 → **羽签**、时间之流（三张时间线）→ **羽时三刻**、圣三角 → **羽镜**、恋人十字 → **恋羽十字**；经典名均保留为触发关键词

**English**


- Spreads renamed: 单张问询 → **Feather Sign**, 时间之流 (三张时间线) → **Feather Hour Three**, 圣三角 → **Feather Mirror**, 恋人十字 → **Lovers' Feather Cross**; classic names kept as triggers

#### v0.4.0


- 插件显示名统一为 **星羽塔罗**（webui/后端简介去掉「幻星集素材版」等后缀），metadata 新增 `display_name`
- AI 解读改为**结构化输出**：每张牌单独一段（【第N张·位置】标记），最后单独一段【总结】直白说清结果
- 解读按结构化标记切段发送，不再按字数硬切；`segment_size` 仅作为无标记文本的兜底
- **合并转发支持图片**：开启 `forward_result` 后牌面图作为首个节点与各段解读打包进同一条转发消息；普通模式图片单独发送、解读逐条发送（间隔 0.3s）

**English**


- Plugin display name unified to **星羽塔罗** (dropped suffixes like 「幻星集素材版」), metadata gained `display_name`
- AI reading switched to **structured output**: one paragraph per card (【第N张·位置】 markers) plus a final 【总结】 summary
- Reading sent by structured markers instead of hard char-splitting; `segment_size` is only the fallback for unstructured text
- **Forward-mode supports images**: with `forward_result` on, the card image becomes the first node bundled with all paragraphs; otherwise image is sent separately and paragraphs one by one (0.3s interval)

---

#### v0.3.1


- 新增配置 `forward_result`：占卜解读以**合并转发消息链**发送（分段打包成一条转发消息，点开查看），告别连续多条文本刷屏
- AI 解读默认**分段发送**：按 `segment_size`（默认 300 字）拆分，普通模式下逐条发送、间隔 0.3s
- 解读文本分段采用「段落优先、长度兜底」切分规则，长段落自动硬切
- 合并转发节点署名「星羽塔罗」，节点 uin 自动取机器人自身 QQ

**English**


- New config `forward_result`: readings sent as a **merged forward message chain** (point to open), no more message spam
- AI readings segmented by default: split by `segment_size` (default 300 chars), sent one by one (0.3s interval) in normal mode
- Splitting rule: paragraph-first, length as fallback; long paragraphs hard-split
- Forward nodes signed 「星羽塔罗」; node `uin` auto-taken from the bot's own QQ

#### v0.3.0


- 正式更名为 **星羽塔罗 Star Feather**（原赛博塔罗），插件名 `star_feather`
- 适配幻星集官方素材：78 张牌面 + 官方牌背背景全部内置到 assets/
- 白边卡牌统一样式，官方牌背 cover 压暗做拼图背景
- 标题/牌位改深藏青胶囊标签，第一眼可读性提升
- **命令前缀限制**：私聊仅响应带 `/` 的命令（`/占卜`、`/单抽`），裸文本不触发；群聊 @ 机器人可触发
- 清理手绘版遗留死代码与废弃依赖，精简渲染逻辑

**English**


- Officially renamed to **星羽塔罗 Star Feather** (was 赛博塔罗), plugin id `star_feather`
- Adapted to Fantasy Star collection assets: all 78 card faces + official card back bundled in `assets/`
- Unified white-border card style; official card back cover used as darkened collage background
- Title/position tags switched to deep-navy capsules; first-glance readability improved
- **Command prefix restriction**: private chats only respond to `/`-prefixed commands (`/占卜`, `/单抽`); bare text won't trigger; group chats can @ the bot
- Removed leftover hand-drawn dead code and abandoned dependencies; rendering logic streamlined

#### v0.2.0


- 新增牌面图片渲染：Pillow 本地绘制塔罗卡片并自动拼图，`占卜` / `单抽` 输出图片
- 图片渲染失败时自动回退纯文本牌面，不影响功能

**English**


- Card image rendering: Pillow draws tarot cards locally and composites them; `占卜` / `单抽` output images
- Render failure falls back to plain-text cards, no functional impact

#### v0.1.0


- 78 张塔罗牌全内置（大阿卡纳 + 小阿卡纳），中英双语牌名与正逆位牌义
- 四种牌阵与关键词智能匹配
- AI 深度解读 + 本地牌义自动兜底
- 零图片依赖，纯文本输出

**English**


- All 78 tarot cards bundled (Major + Minor Arcana), bilingual card names and upright/reversed meanings
- Four spreads with keyword matching
- AI deep reading + built-in meanings as auto fallback
- Zero image dependencies, pure text output
