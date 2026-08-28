<p align="center">
  <img src="https://raw.githubusercontent.com/yuluo-feather/astrbot_plugin_star_feather/main/logo_small.png" width="110" height="110" align="middle"/> <font size="6"><b>Star Feather 🪶</b></font>
</p>

<p align="center">
  "The name is mine, and so is the reading. Hmph, not bad, right?" — Little Feather
</p>

<p align="center">
  <a href="https://github.com/yuluo-feather/astrbot_plugin_star_feather/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-AGPL%20v3-ffb3d9" alt="License: AGPL v3"/></a>
  <a href="https://astrbot.app"><img src="https://img.shields.io/badge/AstrBot-Plugin-ff9ecb" alt="AstrBot Plugin"/></a>
  <img src="https://img.shields.io/badge/version-v0.5.3-f8a5c2" alt="v0.5.3"/>
</p>

<p align="center">🪶 ✨ 🌸 💫 🃏</p>

> [简体中文](https://github.com/yuluo-feather/astrbot_plugin_star_feather/blob/main/README.md) | [English](https://github.com/yuluo-feather/astrbot_plugin_star_feather/blob/main/README.en.md)

---

## 📖 Table of Contents

- 🌸 [Features](#features)
- 🃏 [Commands](#commands)
- 🪶 [Spreads](#spreads)
- ⚙️ [How It Works](#how-it-works)
- 🎛️ [Configuration](#configuration)
- 📦 [Installation](#installation)
- 🤍 [Technical Details](#technical-details)
- 📜 [Changelog](#changelog)
- 🌙 [Notes](#notes)
- ⭐ [Support & Thanks](#support--thanks)

---

🎀 An AstrBot tarot reading plugin — written by me, Little Feather. All 78 cards built in, official card art rendering, AI deep interpretation with local meanings as fallback — no external image resources needed. Got a question? Ask the deck. Hmph, but read the docs first. Don't make me repeat myself.

## 🌸 Features

- 🔮 **All 78 cards built in**: 22 Major Arcana + 56 Minor Arcana (Wands / Cups / Swords / Pentacles), each with Chinese & English names and upright/reversed meanings
- 🃏 **Four spreads**: Feather Sign, Feather Hour Three, Feather Mirror, Lovers' Feather Cross — classic names (Single Question / Time Flow / Three-Card Timeline / Three-Card Spread / Lovers' Cross) also recognized
- 🧠 **Smart spread selection**: scans the question keywords and picks the best spread automatically
- 🤖 **AI deep interpretation**: LLM-based deep reading (optionally pinned to a dedicated model via `ai.ai_provider`), one paragraph per card plus a summary; falls back to built-in meanings automatically so a reading never stalls
- 💬 **Talk to divine**: just say "帮我算一卦" or "看看我今天的运势" to trigger (natural-language entry, when `llm_tool_enabled` is on) — no commands to memorize
- 🗓️ **Fixed daily reading**: `/单抽` and requests containing fortune words (运势 / 运气 / 牌运…) return the same card & reading for the same user all day, refreshed at midnight — no way to reroll
- 🎴 **Official card art**: all 78 card faces + official card back included in `assets/` (WebP format, auto-compatible with .png), unified white-border card style, reversed cards rotated 180°
- 🛡️ **Double fallback**: AI failure falls back to built-in meanings; image rendering failure falls back to plain text

## 🃏 Commands

| Feature | Command | Description |
|---------|---------|-------------|
| 🎴 Smart reading | `/占卜 [question]` | Picks the best spread by keywords, draws cards and sends card art; requests containing fortune words (运势 / 运气…) use the fixed daily reading instead |
| 🃏 Quick draw | `/单抽` | Today's fixed fortune: one card + reading per user per day, unchanged on repeat draws, refreshed at midnight |
| ❓ Help | `/占卜 帮助` / `/占卜 help` | Show usage |

> 💡 **Trigger rules**: in private chat just say `占卜 问题` (`/占卜 问题` works too); in groups, `@bot 占卜 …` triggers directly; with wake words configured (WebUI → Settings → Wake Words), `wake-word 占卜 问题` also works. Bare text without a wake word or @ does not trigger—the gate belongs to the framework, the feather just reads the cards.

### 🧩 Multiple bots

If you run several bots at once (multiple accounts / instances), keep this in mind: group messages are broadcast to every bot in the group, and each bot decides independently — so:

- **One divination bot per group**: don't install Star Feather on the other bots, or turn off `tool.llm_tool_enabled` (the natural-language entry) — one "占卜" can otherwise be answered by several bots at once
- **Give each bot its own wake word**: bot A wakes on 「羽毛」, bot B on 「星羽」; never share a single wake word across bots
- **@ is the safest trigger in groups**: only the bot you @ responds
- **Don't register "占卜" itself as a wake word**: the framework strips the wake word first, so the command no longer matches — and several bots may each fall back to the natural-language entry. Double chaos, don't try it

Private chat is safe: it's point-to-point, only the bot you're talking to sees the message.

### Example

```
/占卜 How will my relationship develop?
```

Flow: smart match "Lovers' Feather Cross" → shuffle hint → draw four cards with meanings → AI deep reading.

## 🪶 Spreads

| Spread | Cards | Positions | Trigger |
|--------|-------|-----------|---------|
| Feather Sign | 1 | Your present | Use `/单抽`; or specify explicitly with `/占卜` (羽签 / Single Question) |
| Feather Hour Three | 3 | Past / Present / Future | Default; or keywords like "past / future / timeline / time flow / three-card timeline" |
| Feather Mirror | 3 | Situation / Obstacle / Advice | Keywords like "career / work / interview / study / exam / promotion / three-card spread" |
| Lovers' Feather Cross | 4 | You / Them / Relationship now / Outcome | Keywords like "love / relationship / breakup / reunion / lovers' cross", or "he / she / us / does he love me" |

> 💫 Classic names (Single Question / Time Flow / Three-Card Timeline / Three-Card Spread / Lovers' Cross) are still recognized.

## ⚙️ How It Works

From your request to the final result, Star Feather runs this pipeline internally — understand it and troubleshooting gets easy.
> The module names in brackets show where each step lives (see [Technical Details](#technical-details)): what the pipeline does is here, where the logic lives is in the module name.

```
User request (any of three entry points)【main.py orchestration】
   │
   ├─ ① Command entry: 占卜 [question] or /占卜 [question]
   │     └─ The gate is the framework's (wake words / @ bot / private-chat direct); CommandFilter must match first
   │
   └─ ② Natural-language entry: just say "帮我算一卦" etc. (llm_tool, toggleable)
         └─ Tool description only fires on explicit requests; complaints / casual remarks won't trigger
   │
   ▼
② Rate-limit gate (shared by all three entries)【gating.py · limiter.py】
   ├─ Command: per-session throttle (cmd_rate_limit, anti double-click)
   ├─ Natural language: per-session throttle (llm_tool_cooldown, anti spam)
   └─ Daily quota: per-user counter (daily_count, resets at midnight) → over limit gets a "come back tomorrow" hint
   │
   ▼
③ Draw routing (_pick_reading)【spreads.py selection · daily.py fixed daily】
   ├─ Fortune words (运势/运气/牌运) or /单抽 → fixed daily reading
   │     (same user + same day = same card + same reading, refreshed at midnight, no rerolls)
   └─ Specific questions (love / career etc.) → free random
         ├─ Explicit spread name (/占卜 圣三角 考研如何) > keyword match > content inference
         └─ No match → default "Feather Hour Three"
   │
   ▼
④ Run the reading (_run_reading)【tarot_core.py draw & render · interpret.py + hardening.py reading】
   ├─ Shuffle hint: multi-card spreads send a random "✨ 洗牌中……" (shuffle_lines toggleable)
   ├─ Render card image: official assets + upright/reversed (thread pool, temp image auto-cleaned in 30s)
   └─ Generate interpretation:
         ├─ Fixed daily → cached reading for the day (stays fixed all day)
         └─ Free random → AI reading on the fly
               ├─ Provider chain: specified model → session model → global default → all loaded (30s timeout each)
               ├─ Question cleaned first: clipped to 200 chars (head + tail kept) + injection phrases stripped (hardening)
               └─ All failed → fall back to built-in meanings (+ 60s fail cooldown)
   │
   ▼
⑤ Deliver result (_deliver)【deliver.py delivery · settings.py config applied】
   ├─ Split by structure: one paragraph per card + summary (80–140 chars per prompt convention; overlong segments re-split at `segment_size`)
   ├─ send_mode decides form: forward merged (default) / plain single chain / text_only
   └─ Disclaimer appended at the end (disclaimer, can be empty)
   │
   ▼
⑥ Closing【prompts.py copy pool · main.py direct send】
   ├─ Command entries: a closing line is sent directly right after the result (random line from the shared 7-line RESULT_EPILOGUE pool)
   └─ Natural-language entry: content is sent via event.send, then a closing guidance is yielded to the model →
        the model echoes one random line from the same pool, e.g. "✨ 牌灵已把答案交到你手上了，祝好运～"
```

**All three entries share the same steps ②~⑤**, so draw rules, rate limits, delivery and fallbacks behave identically regardless of entry point; the only differences are the trigger itself and where the closing line comes from — command entries have the plugin send a fixed line directly, the natural-language entry has the model echo it (same copy pool, same style).

## 🎛️ Configuration

Configure in the AstrBot plugin management UI (grouped sections):

### [AI Interpretation] Model calls, timeout & fallback

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `ai.enable_ai` | bool | `true` | Enable AI deep interpretation. When off, only built-in meanings are used. |
| `ai.ai_provider` | string | `（empty）` | Provider used for AI interpretation (dropdown). Empty = current session model; when set, the reading prefers this model (falls back to others if unavailable) without affecting the chat model. |
| `ai.ai_timeout` | int | `30` | Per-provider timeout for AI interpretation (seconds). On timeout the plugin tries the next available provider; if all fail it falls back to built-in meanings. Min 5. |
| `ai.ai_fail_cooldown` | int | `60` | Cooldown after all AI providers fail (seconds). During cooldown the plugin uses built-in meanings directly without calling AI again; set 0 to disable. |
| `ai.question_max_len` | int | `200` | Length cap (chars) for the question sent to AI. Over-long questions are head+tail-clipped (opening context and closing intent kept); set 0 to disable clipping. |

### [Natural Language Entry] Conversation trigger & throttle

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `tool.llm_tool_enabled` | bool | `true` | Enable the natural-language entry: users can trigger a reading by asking for one in conversation (the tool description only fires on explicit requests, not casual complaints); turn off to keep only `/占卜` / `/单抽` commands. |
| `tool.llm_tool_cooldown` | int | `60` | Throttle between natural-language triggers (seconds per session) to avoid repeated readings; set 0 to disable. |
| `tool.cmd_rate_limit` | int | `10` | Per-session throttle for the `/占卜` and `/单抽` commands (seconds) to stop spam-clicking; set 0 to disable. |

### [Rate limits] Reading frequency control

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `limit.daily_count` | int | `0` | Per-user daily cap on readings across all entries (`/占卜`, `/单抽`, natural language); resets at midnight; set 0 for unlimited. |

### [Output & Splitting] How results are delivered

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `output.send_mode` | string | `forward` | How results are sent (dropdown): `plain` = card image + segmented text packed into one message chain; `forward` = card art + reading packed into one merged forward message (requires adapter support); `text_only` = plain text only, no card image — lightest. Legacy `forward_result` / `show_image` configs migrate automatically. |
| `output.segment_size` | int | `300` | Fallback chunk size for unstructured interpretation text (min 50). Structured segments are not affected. |
| `output.daily_fixed` | bool | `true` | Fixed daily reading. When on, `/单抽` and fortune-word requests return the same card all day per user (refreshed at midnight); turn off for free random draws. |
| `output.shuffle_lines` | bool | `true` | Show shuffle hint messages. Fires only for multi-card spreads (single-card draws like 羽签 stay quiet); disable to skip the extra "shuffling…" line. |
| `output.disclaimer` | string | `✨ 占卜仅供娱乐参考，选择权永远在你手里。` | Disclaimer appended to every reading; set empty string to hide. |

## 📦 Installation

> ⚠️ **Runtime requirement**: this plugin requires **Python 3.12+** (matching AstrBot's own minimum) and **AstrBot 4.16+ (<5)**. Environments below these versions cannot load it.

### 🌷 Option 1: From AstrBot Plugin Market (Recommended)

1. Open the AstrBot dashboard → "Plugin Market / Install Plugin"
2. Search for "Star Feather Tarot" and click "Install"
3. Wait for download and automatic dependency install
4. Find "Star Feather Tarot" in "AstrBot Plugins" and click "Reload Plugin"

### 🌷 Option 2: From GitHub Repository URL

1. Open the AstrBot dashboard → "Plugin Market / Install Plugin"
2. Select "Install from GitHub repository URL" and enter:

   `https://github.com/yuluo-feather/astrbot_plugin_star_feather`

3. Click install and wait for download and dependencies
4. Find "Star Feather Tarot" in "AstrBot Plugins" and click "Reload Plugin"

### 🌷 Option 3: Manual Installation

1. Clone or download into AstrBot's `data/plugins/` directory, keeping the folder name `astrbot_plugin_star_feather`:

   ```bash
   cd AstrBot/data/plugins
   git clone https://github.com/yuluo-feather/astrbot_plugin_star_feather.git
   ```

2. Restart AstrBot, the plugin loads automatically
3. Send `/占卜 your question` in chat to begin

**Dependency**: card rendering needs [Pillow](https://pypi.org/project/pillow/) (usually bundled with AstrBot; otherwise `pip install -r requirements.txt`). Glyph-coverage fallback verification needs [fonttools](https://pypi.org/project/fonttools/), also listed in `requirements.txt`.

## 🤍 Technical Details

- **Code structure (17 modules, one-way dependencies)**: `main.py` entry orchestration; `settings.py` config semantics (defaults + legacy migration); `config.py` config-read primitives (grouped-first / flat fallback / type coercion); `identity.py` event identity (2-level user-id fallback, shared by daily & rate limits); `spreads.py` formation selection & question cleaning; `tarot_core.py` draw & card presentation; `interpret.py` + `hardening.py` AI reading & prompt hardening; `log_setup.py` runtime log to disk; `deliver.py` delivery orchestration; `gating.py` + `limiter.py` rate-limit gate (KV glue) & pure logic; `daily.py` fixed daily reading; `card_render.py` + `fonts.py` card rendering & font subsystem (incl. image lifecycle); `prompts.py` centralized copy (incl. help text); `tarot_data.py` card database — each module has a one-line responsibility, pure logic independently testable
- Card database fully embedded in `tarot_data.py`; card art in `assets/` (78 official card faces + official card back `Extra/背景.png`; stored as WebP, loader auto-compatible with .png)
- `card_render.py` composes the image: official card back cover-filled and dimmed (brightness 0.62) as background, unified white-border card style, reversed cards rotated 180°
- Titles & positions use dark navy capsule labels; info bars show orientation (gold/red) + card name + meaning keywords
- Fonts: bundled Noto Sans SC subset (`fonts/`, SIL OFL 1.1; branded as `StarFeather-*.otf`, covers all fixed card texts; bold uses the separate `StarFeather-Bold` subset) preferred, then system fonts (Windows / macOS / Linux), consistent cross-platform rendering
- Before rendering, glyph coverage is verified: if the bundled subset lacks characters, it auto-falls back to a system font covering that text (requires `fonttools`, listed in requirements). Current card texts are fully covered; this guards future rare-character additions
- Cards drawn without replacement from 78, upright/reversed 50/50
- Commands no longer require a `/` prefix: the gate belongs to the framework (wake words / group @ / direct private-chat text), so bare `占卜 问题` works in private chat (`_require_prefix` now trusts the framework instead of re-checking and fighting the WebUI wake-word config; regression covered by `tests/`)
- AI reading tries providers in order (pinned model `ai.ai_provider` → current session → global default → all loaded, deduped by id), each with a timeout (`ai_timeout`, default 30s); if all fail it falls back to local meanings and enters a cooldown (`ai_fail_cooldown`, default 60s) so repeated reads don't wait on a dead provider
- **Natural-language entry**: just ask for a reading in chat (`star_feather_divine` LLM tool); the tool description only fires on explicit requests, and a per-session throttle (`llm_tool_cooldown`, default 60s) prevents spamming
- **Fixed daily reading**: `/单抽` and fortune-word requests draw deterministically per (user, date) — md5-seeded independent RNG (global random untouched); user id resolved by a 2-level fallback (sender id → raw `sender.user_id`, int-compatible; no session-id fallback in groups, so members don't share one reading); KV single-key overwrite cache with the card guaranteed by the pure function even if KV fails; `output.daily_fixed` can disable it
- **Unified flow**: all three entries share `_pick_reading` (draw routing) + `_run_reading` (delivery), so toggle settings (shuffle hint / card image / disclaimer) take effect in one place; the shuffle hint is produced for multi-card spreads only — `/单抽` and 羽签 (single-card) stay quiet, keeping the old behavior
- **Image lifecycle**: cleanup is registered at the image's *point of creation* — 30s delayed delete (all send paths covered: AI success / fallback / aborts), plus a startup sweep of leftovers from the previous run
- Questions sent to AI are **clipped to 200 chars** (head + tail kept: opening context and closing intent both survive, head ends at a sentence boundary); the system prompt restricts the model to tarot interpretation only
- Keyword → spread matching by priority: explicit spread names (new + classic aliases: Single Question / Time Flow / Three-Card Timeline / Three-Card Spread (圣三角) / Lovers' Cross, plus 羽签 / 羽时三刻 / 羽镜 / 恋羽十字) → semantic keyword weight (love → Lovers' Cross, career/study → Feather Mirror, past/future/timeline → Feather Hour Three; fortune words are handled first by the fixed-daily layer) → content inference ("he / she / us / does he love me") → defaulting to "Feather Hour Three"
- Pure-logic unit tests in `tests/` (pytest, organized by module; case count grows with development): `test_core` (draw, render gating, interpreter integration, three-entry orchestration, daily fallbacks), `test_settings` (defaults & legacy migration), `test_spreads` (selection / alias / question cleaning), `test_hardening` (injection stripping / clipping / structure validation), `test_identity` (user-id fallback chain), `test_gating` (rate-limit gate), `test_log_setup` (log path candidates & idempotent install), `test_card_render` (render smoke, image cleanup), `test_fonts` (font coverage & fallback/cache regression), `test_deliver` (splitting & delivery), `test_limiter`, `test_config` (config primitives); `pip install pytest` first, then run `python -m pytest tests`

## 📜 Changelog

See [CHANGELOG.md](https://github.com/yuluo-feather/astrbot_plugin_star_feather/blob/main/CHANGELOG.md) for the full version history. Latest release (v0.5.3): daily-reading detection fixed — "最近 / 近期 / 每日" no longer counts as a fortune word alone, questions with a specific topic now get a normal spread draw instead of the same fixed card & reading all day; daily readings are now cached per topic (each topic gets its own reading instead of sharing the first one; generic fortune questions stay fixed all day); AI readings are anchored to the meaning keywords given in the draw result; help detection tightened — only a standalone help request ("帮助 / help / 使用帮助 / 看下说明" etc.) shows the usage guide, the word "帮助" inside a question body no longer trips the help page. v0.5.2: security fixes — command errors no longer show exception details to users (log only); Pillow floor raised to `>=10.0.0` (known CVEs in 9.x). v0.5.1: the tool's non-reading branches (disabled / throttled / daily-quota / empty question) no longer echo a "reading sent" wrap-up that didn't happen. v0.5.0: natural-language entry (just ask in chat, throttled); fixed daily reading (same card & reading all day per user, independent per member in groups); AI provider chain with per-call timeout and fail cooldown + selectable reading model (`ai.ai_provider`); entry rate limits (command per-session throttle + per-user daily counter); send mode, three choices — a single `send_mode` dropdown (plain / forward / text_only, legacy configs auto-migrated, now defaulting to merged forward); new options: question length cap, disclaimer, shuffle hint, daily-fixed toggle (upright/reversed stays pure random 50/50 — no toggle); unified draw & delivery flow across all three entries; security hardening (jailbreak-sentence stripping, AI output structure validation, 200-char head + tail clipping); better concurrency (simultaneous readings don't block each other); runtime requirements declared (Python 3.12+ & AstrBot 4.16+ <5 — lower environments can't load); relaxed trigger gate — no more mandatory `/` prefix, bare 占卜 问题 works in private chat, and AstrBot wake words + command are supported.

---

## 🌙 Notes

- AI reading follows a **provider chain**: pinned model (`ai.ai_provider`) → current session provider → global default → all loaded; if all fail or none is configured, built-in meanings are used automatically so the reading never stalls
- Readings are for entertainment only — take them with a grain of salt 🍀

---

## ⭐ Support & Thanks

- Like it? A ⭐ on [GitHub](https://github.com/yuluo-feather/astrbot_plugin_star_feather) keeps the little feather motivated
- Found a bug or want a feature? [Issues](https://github.com/yuluo-feather/astrbot_plugin_star_feather/issues) and Pull Requests are always welcome — every one gets read
- **Thanks**: to 幻星集 (official card art), to the AstrBot framework, and to everyone who filed an issue or left a star — the spirit remembers you
