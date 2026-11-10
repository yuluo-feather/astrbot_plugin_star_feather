<p align="center">
  <img src="https://raw.githubusercontent.com/yuluo-feather/astrbot_plugin_star_feather/main/logo_small.png" width="110" height="110" align="middle"/> <font size="6"><b>Star Feather 🪶</b></font>
</p>

<p align="center">
  "The name is mine, and so is the reading. Hmph, not bad, right?" — Yuluo
</p>

<p align="center">
  <a href="https://github.com/yuluo-feather/astrbot_plugin_star_feather/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-AGPL%20v3-ffb3d9" alt="License: AGPL v3"/></a>
  <a href="https://astrbot.app"><img src="https://img.shields.io/badge/AstrBot-Plugin-ff9ecb" alt="AstrBot Plugin"/></a>
<img src="https://img.shields.io/badge/version-v0.10.1-f8a5c2" alt="v0.10.1"/>
</p>

<p align="center">🪶 ✨ 🌸 💫 🃏</p>

> [简体中文](https://github.com/yuluo-feather/astrbot_plugin_star_feather/blob/main/README.md) | [English](https://github.com/yuluo-feather/astrbot_plugin_star_feather/blob/main/README.en.md)

---

## 📖 Table of Contents

- 🌸 [Features](#-features)
- 🃏 [Commands](#-commands)
- 🪶 [Spreads](#-spreads)
- ⚙️ [How It Works](#️-how-it-works)
- 🎛️ [Configuration](#️-configuration)
- 📦 [Installation](#-installation)
- 🤍 [Technical Details](#-technical-details)
- 📜 [Changelog](#-changelog)
- 🌙 [Notes](#-notes)
- ⭐ [Support & Thanks](#-support--thanks)

---

🎀 An AstrBot tarot reading plugin, written by me. All 78 cards built in, card art from the 幻星集 set, AI deep interpretation with local meanings as fallback (when the image renders, only the card's keywords; when rendering fails or in text-only mode, the full per-card meanings), no external image resources needed. Ask the deck if you have a question.

## 🌸 Features

- 🔮 **All 78 cards, built in**: 22 Major Arcana and 56 Minor Arcana (Wands, Cups, Swords and Pentacles). Every card has a Chinese and an English name and a layered meaning: upright readings give keywords, what's unfolding and what to do about it; reversed readings give keywords, the state you're in, where the energy sticks and how to turn it around.
- 🃏 **Four spreads**: Feather Sign, Feather Hour Three, Feather Mirror and Lovers' Feather Cross. The classic names work just as well — Single Question, Time Flow, Three-Card Timeline, Three-Card Spread, Lovers' Cross.
- 🧠 **It picks the spread for you**: Star Feather reads the keywords in your question and draws on the spread that fits. One less decision to make.
- 🤖 **AI deep reading**: the draw goes to an LLM (pin a dedicated model with `ai.ai_provider`) and comes back as one paragraph per card plus a summary. If the call fails, the built-in meanings take over: when the image renders, only the card's keywords; when rendering fails or in text-only mode, the full per-card meanings. The reading never stalls halfway.
- 🗣️ **A spirit with a personality**: the reading is spoken by the deck's spirit in one of three voices — tsundere, gentle or mystic (`ai.persona`; `off` restores the neutral tone). Each voice comes with its own tone rules and a sample of its signature style, which also shapes the spirit's opening line (`ai.spirit_line` can turn it off). `random` locks one voice per reading. Tone only: content and structure stay as they are.
- 💬 **Ask in plain words**: "帮我算一卦" or "看看我今天的运势" is enough to start a reading (natural-language entry, on when `llm_tool_enabled` is set). Nothing to memorise.
- 🗓️ **One fixed reading a day**: `/今日牌运` (aliases `/运势`, `/每日一签`) and requests carrying fortune words (运势 / 运气 / 牌运 …) return the same card and the same reading for the same person, until midnight resets it. Asking again won't reroll it. `/单抽` is separate: every call draws a fresh Feather Sign with a freshly generated reading, no cache.
- 🎴 **Card art**: all 78 faces and the card back ship inside `assets/` (WebP; a same-name .png takes precedence when present). A white border keeps the look consistent, and a reversed card rotates only the artwork — the frame and the info bar stay upright. Cards that look good are half the reading.
- 🛡️ **Two layers of fallback**: if the AI fails, the built-in meanings take over (when the image renders, only the card's keywords; when rendering fails or in text-only mode, the full per-card meanings); if rendering fails, plain text does. Whichever part misbehaves, I won't let you go home empty-handed.

## 🃏 Commands

| Feature | Command | Description |
|---------|---------|-------------|
| 🎴 Reading | `/占卜 [question]` | Reads the question, picks the spread, draws the cards and sends the art. Fortune words (运势 / 运气 …) route to the fixed daily reading instead — except event-attribution asks such as "is it because my luck is bad?", which get a free draw. |
| 🃏 Quick draw | `/单抽 [question]` | Draws one Feather Sign card, **fresh every time** — not fixed per day, and the reading is generated live with no cache. |
| 🗓️ Daily fortune | `/今日牌运 [topic]` | Today's fixed card and reading, reset at midnight; asking again the same day changes nothing. Aliases: `/运势`, `/每日一签`. Renders the poster card (art, date, the spirit's line, sign-off). |
| ❓ Help | `/占卜 帮助` / `/单抽 帮助` / `/今日牌运 帮助` | Show usage (`help` works too). |

> 💡 **How to trigger it**: in private chat, `占卜 问题` is enough (`/占卜 问题` works as well). In a group, @ the bot. Set a wake word under WebUI → Settings → Wake Words and `wake-word 占卜 问题` will do it. In a group, bare text with neither a wake word nor an @ stays quiet — the framework owns the gate; reading the cards is my job.

### 🧩 Multiple bots

Group messages reach every bot in the group, and each bot decides for itself whether to answer. If you run more than one (several accounts or instances), plan for it:

- **One divination bot per group**: leave Star Feather off the others, or turn off `tool.llm_tool_enabled` (the natural-language entry) on them — otherwise a single "占卜" can get several answers at once.
- **Give each bot its own wake word**: bot A on 「羽毛」, bot B on 「星羽」 — never share one.
- **In groups, @ is the safest trigger**: only the bot you @ answers.
- **Don't register "占卜" itself as a wake word**: the framework strips the wake word first, so what's left no longer matches the command — and several bots may each fall back to the natural-language entry.

Private chat is safe either way: it's point-to-point, so only the bot you're talking to sees the message.

### Example

```
/占卜 How will my relationship develop?
```

What happens: the keyword match picks Lovers' Feather Cross → shuffle hint → four cards with their meanings → AI deep reading.

![Star Feather Tarot in action](https://raw.githubusercontent.com/yuluo-feather/astrbot_plugin_star_feather/main/docs/preview_divine.png)

## 🪶 Spreads

| Spread | Cards | Positions | Trigger |
|--------|-------|-----------|---------|
| Feather Sign | 1 | Your present | Use `/单抽`; or specify explicitly with `/占卜` (羽签 / Single Question) |
| Feather Hour Three | 3 | Past / Present / Future | Default; or keywords like "past / future / timeline / time flow / three-card timeline" |
| Feather Mirror | 3 | Situation / Obstacle / Advice | Keywords like "career / work / interview / study / exam / promotion / three-card spread" |
| Lovers' Feather Cross | 4 | You / Them / Relationship now / Outcome | Keywords like "love / relationship / breakup / reunion / lovers' cross", or "he / she / us / does he love me" |

> 💫 Classic names (Single Question / Time Flow / Three-Card Timeline / Three-Card Spread / Lovers' Cross) are all still recognized — call them whatever you're used to.

## ⚙️ How It Works

Here is what happens between your question and the finished reading. Know the path and troubleshooting gets easy.
> Module names in brackets point to where each step lives (see [Technical Details](#-technical-details)) — this section is the flow, those are the files.

```
User request (any one of three entry points)【main.py orchestration】
   │
   ├─ Command entry: 占卜 [question] or /占卜 [question]
   │     └─ The framework owns the gate (wake word / @ the bot / private chat); the command filter must match first
   │
   └─ Natural-language entry: ask in chat, e.g. "帮我算一卦" (llm_tool, switchable)
         └─ The tool description only fires on an explicit request, so complaints and small talk don't trigger it
   │
   ▼
② Rate-limit gate (one gate, three entries)【services/gating.py · core/limiter.py】
   ├─ Command: per-session throttle (cmd_rate_limit, stops double-taps)
   ├─ Natural language: per-session throttle (llm_tool_cooldown, stops spam)
   └─ Daily quota: per-user counter (daily_count, resets at midnight) → over the limit gets "come back tomorrow"
   │
   ▼
③ Draw routing (ReadingService.prepare)【core/intent.py decides · core/spreads.py holds the spreads · services/daily.py handles the fixed daily draw】
   ├─ Fortune words (运势 / 运气 / 牌运) or /今日牌运 → today's fixed reading
   │     (one person, one day = the same card and the same reading, reset at midnight,
   │      no rerolls. Event-attribution asks — "I pinched my finger, is it because my
   │      luck is bad?" — are not fortune queries and go to a free draw. Time words go
   │      by a positive whitelist: generic ask-forms like "how have I been lately" and
   │      signature phrases like "daily tarot / one card today" stay daily, while
   │      "I keep losing sleep lately" or "is there a meeting this afternoon" do not.)
   └─ Everything else (love, career, event attribution, appointments) → free draw; the
      tool entry may fill the spread from the model's `formation` argument when rules
      have no signal, while command entries always stay on the rules
         ├─ Explicit spread name (/占卜 圣三角 考研如何) > keyword match > content inference
         │     (timeline words plus relationship semantics — "our future", "will she love me" —
         │      pick the relationship spread; pure timeline questions like "my future" don't)
         └─ No match → Feather Hour Three
   │
   ▼
④ Run the reading (ReadingService.run)【core/draw.py draws · services/render.py renders · ai/chain.py + ai/parse.py + ai/hardening.py read】
   ├─ Shuffle hint: a random "✨ 洗牌中……" before multi-card spreads (shuffle_lines can turn it off)
   ├─ The spirit's line (toggle with `ai.spirit_line`): every spread opens with one, generated by the AI in the persona's voice and fitted to this
   │  reading's cards and topic — fixed for the same person, day and card set, renewed the next day. When the AI is
   │  unavailable it falls back to the day's line from the pool. It arrives bare (no "spirit's words:" label, no
   │  quotes). It also runs under a 20-second stage cap (candidate lookup and every attempt share one stage
   │  deadline, so it accumulates rather than doubling per candidate)
   └─ Generate the reading:
   │     ├─ Fixed daily → the day's cached reading (stays put all day)
   │     └─ Free random → the AI reads it live
   │           ├─ Provider chain: pinned model → session model → global default (every loaded provider is only appended when `ai.all_providers_fallback` is on; a per-request timeout, `ai_timeout`, 30s by default; set it to 0 to leave that stage uncapped; per-provider concurrency `ai.max_concurrency`, 2 by default)
   │           ├─ The question is cleaned first: clipped to 200 characters (head and tail kept) + injection phrases stripped (hardening)
   │           ├─ Shares one AI budget with the spirit's line (ai_budget_seconds, 120s by default); the deadline is never reset between stages
   │           └─ All providers failed → built-in meanings (when the image renders, only the card's keywords; when rendering fails or in text-only mode, the full per-card meanings; + a 60s cooldown, so nothing waits in vain)
   ├─ Render the card art (deliberately *after* both AI stages, just before sending): the 幻星集 card art, upright or
   │  reversed (thread pool; one 600s fuse recycles every temp image, and startup recycles stale instance dirs).
   │  A collage background is built from one card picked at random out of *this* reading — cover-fill plus a
   │  deep-navy overlay — and the daily reading get their own poster card (art, date, the day's line from
   │  the pool, sign-off; `output.daily_card` switches it off, and a failure falls back to a plain card image)
   └─ Verify the image before sending (Renderer.ensure_image): if it vanished, re-render once; if that fails too, send
         without an image — the reading still goes out
   │
   ▼
⑤ Send it (Presenter.present)【adapters/deliver.py orchestrates the send · core/settings.py decides what takes effect】
   ├─ Order: card art → the spirit's line → the reading paragraphs → disclaimer (a separate message; its own last node in a merged forward)
   ├─ Split by structure: one paragraph per card plus a summary (the prompt asks for 80–140 characters each;
   │  overlong ones get cut down to `segment_size`)
   ├─ send_mode decides the shape: forward merged message (default) / plain single chain / text_only
   └─ The disclaimer is sent as its own message after the reading (empty means nothing is sent)
   │
   ▼
⑥ Wrap up【ai/prompts.py holds the copy · Presenter sends it directly】
   ├─ Command entry: a closing line goes out on its own after the result (one of the seven in RESULT_EPILOGUE)
   └─ Natural-language entry: the result goes out through event.send, then a closing hint is yielded to the model →
        the model repeats one random line from the same pool, e.g. "✨ 牌灵已把答案交到你手上了，祝好运～"
```

**All three entries share steps ②~⑤**, so draw rules, rate limits, delivery and fallbacks behave the same however the reading started. The differences are the trigger itself and where the closing line comes from: the command entry gets a fixed line sent by the plugin, the natural-language entry has the model repeat one. Same copy pool either way.

## 🎛️ Configuration

I grouped the options by purpose — configure them in the AstrBot plugin management UI:

### [AI Interpretation] Model calls, timeout & fallback

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `ai.enable_ai` | bool | `true` | Enable AI deep interpretation. When off, only built-in meanings are used (when the image renders, only the card's keywords; when rendering fails or in text-only mode, the full per-card meanings; the spirit line also falls back to the pool). |
| `ai.ai_provider` | string | `(empty)` | Provider used for AI interpretation (dropdown). Empty = current session model; when set, the reading prefers this model (falls back to others if unavailable) without affecting the chat model. |
| `ai.spirit_line` | bool | `true` | Spirit-line toggle. When on, every reading opens with one line from the spirit (AI-generated in the chosen persona, falling back to the day's pool line when AI is unavailable); turn it off to skip the line entirely (the sign-off on the `/今日牌运` poster is hidden too) — one less AI call and one less lead-in message. |
| `ai.ai_timeout` | int | `30` | Per-request timeout for AI calls — the spirit line and the deep reading (seconds). On timeout the plugin tries the next available provider; if all fail it falls back to built-in meanings (when the image renders, only the card's keywords; when rendering fails or in text-only mode, the full per-card meanings). Set to `0` to leave a single request uncapped (the total AI budget and the spirit-line stage cap still apply); any other value is floored at 5 seconds. |
| `ai.ai_fail_cooldown` | int | `60` | Cooldown after an AI provider failure (seconds), **per provider**: a dead model only cools itself down, others keep serving; set 0 to disable. |
| `ai.persona` | select | `random` | Spirit persona for AI readings: `off`-neutral (same as before) / `tsundere`-tsundere / `gentle`-gentle / `mystic`-mystic / `random`-one persona fixed per reading (never changes mid-reading; next reading may differ). Tone only — reading content and output structure unchanged. |
| `ai.question_max_len` | int | `200` | Length cap (chars) for the question sent to AI. Over-long questions are head+tail-clipped (opening context and closing intent kept); set 0 to disable clipping. |
| `ai.ai_budget_seconds` | int | `120` | Total AI budget for one reading (seconds): every AI call in that reading — the spirit's line plus the deep reading, including candidate retries and provider fallbacks — shares one deadline. When it runs out the plugin falls back to built-in meanings (same presentation rule) and stops issuing requests. The spirit's line is further capped at 20 seconds (candidate lookup and every attempt share one stage deadline, so it accumulates instead of doubling per candidate). Set 0 to disable the total budget — that removes the ceiling on AI wait time (a warning is logged), but the spirit stage cap still applies. It bounds the AI stage only, not the whole message. |
| `ai.parallel_stages` | bool | `true` | Whether the spirit's line and the deep reading are requested concurrently. On, total time is that of the slower stage; off, the spirit's line is produced first and the reading follows. Turn it off for single-slot local models or strict per-provider rate limits, where two simultaneous requests would queue or get throttled. |
| `ai.max_concurrency` | int | `2` | Maximum concurrent requests to the same provider. Default 2 lets the spirit line and the reading run in parallel without queueing on single-slot local models or strict rate limits; 0 means unlimited, negative values fall back to the default. |
| `ai.all_providers_fallback` | bool | `false` | After the pinned model → session model → global default chain, whether to try every loaded provider as well. Off by default to avoid requests and cost on unexpected models. |
| `ai.structure_check` | select | `loose` | Strength of the reading structure check: `loose`-default, only requires the 【第N张·位置】 sections with count ≥ number of cards; `strict`-numbers 1..N each exactly once, 【总结】 0 or 1 time, position names non-empty; non-conforming candidates are dropped and the next model is tried. |

### [Natural Language Entry] Conversation trigger & throttle

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `tool.llm_tool_enabled` | bool | `true` | Enable the natural-language entry: users can trigger a reading by asking for one in conversation (the tool description only fires on explicit requests, not casual complaints); turn off to keep only `/占卜` / `/单抽` / `/今日牌运` commands. |
| `tool.llm_tool_cooldown` | int | `60` | Throttle between natural-language triggers (seconds per session) to avoid repeated readings; set 0 to disable. |
| `tool.cmd_rate_limit` | int | `10` | Per-session throttle for the `/占卜`, `/单抽` and `/今日牌运` commands (seconds) to stop spam-clicking; set 0 to disable. |

### [Rate limits] Reading frequency control

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `limit.daily_count` | int | `0` | Per-user daily cap on readings across all entries (`/占卜`, `/单抽`, `/今日牌运`, natural language); resets at midnight; set 0 for unlimited. |

### [Output & Splitting] How results are delivered

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `output.send_mode` | string | `forward` | How results are sent (dropdown): `plain` = card image + segmented text packed into one message chain; `forward` = card art + reading packed into one merged forward message (verified on `aiocqhttp` / `satori`; other adapters, including WebChat, fall back to `plain` automatically so the result is never lost); `text_only` = plain text only, no card image — lightest. Legacy `forward_result` / `show_image` configs migrate automatically. |
| `output.segment_size` | int | `300` | Fallback chunk size for unstructured interpretation text (min 50). Structured segments are not affected. |
| `output.daily_fixed` | bool | `true` | Fixed daily reading. When on, `/今日牌运` and fortune-word requests return the same card all day per user (refreshed at midnight); turn off for free random draws (`/单抽` is unaffected — it always draws fresh). |
| `output.daily_card` | bool | `true` | Daily fortune poster card. When on, `/今日牌运` renders a vertical poster (card art + date + the spirit's words + sign-off); turn off to fall back to the normal card image while the spirit's words still follow the reading. |
| `output.shuffle_lines` | bool | `true` | Show shuffle hint messages. Fires only for multi-card spreads (single-card draws like 羽签 stay quiet); disable to skip the extra "shuffling…" line. |
| `output.disclaimer` | string | `✨ 占卜仅供娱乐参考，选择权永远在你手里。` | Disclaimer sent as a separate message after every reading; empty string sends nothing. |

## 📦 Installation

> ⚠️ **Runtime requirement**: this plugin requires **Python 3.12+** (matching AstrBot's own minimum) and **AstrBot 4.16+ (<5)**. Environments below these versions cannot load it. The "Star Feather · Observer" view in the WebUI requires **AstrBot 4.24.2+**; on older versions only the chat commands are available and the view is not listed in the panel.

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

**Dependency**: card rendering needs [Pillow](https://pypi.org/project/pillow/) (usually bundled with AstrBot; otherwise `pip install -r requirements.txt`). Glyph-coverage fallback verification needs [fonttools](https://pypi.org/project/fonttools/), also listed in `requirements.txt`; timezone resolution needs [tzdata](https://pypi.org/project/tzdata/) (Windows and slim containers ship no system tz database, and without it `ZoneInfo` cannot even parse UTC — the framework's `timezone` setting then silently does nothing), also in `requirements.txt` — those three, and nothing more.

> The Pillow security floor is `>=13.0.0`: 12.3.0 is covered by several security advisories (including critical decoding issues), and 13.0.0 is the first patched release; 10.x / 11.x still carry known CVEs.

## 🤍 Technical Details

- **Code structure (layered, one-way dependencies, no cycles; one line of responsibility per module, pure logic independently testable)**:
  - Entry layer: `main.py` (≤150 lines, only registers commands/tools) → `app/` (`container.py` wiring, `handlers.py` three entries, `diagnostics.py` config freshness, `webui.py` observer backend)
  - Orchestration layer: `services/` (`reading.py` reading pipeline, `render.py` rendering & image lifecycle, `daily.py` daily cache, `gating.py` rate-limit gate, `daily_store.py` single-key record, `singleflight.py` same-key dedup)
  - Adapters: `adapters/` (`astrbot_io.py` KV & messages, `event.py` event DTO / delivery capabilities, `deliver.py` segmentation, `presenter.py` ordered delivery, `llm.py` provider gateway, `clock.py` date boundary, `kv_utils.py` KV primitives, `log.py` neutral log binding, `web.py` WebUI request adapter)
  - AI layer: `ai/` (`chain.py` candidate chain, `gateway.py` provider port, `budget.py` budget, `prompts.py` / `persona.py` / `parse.py` / `hardening.py`)
  - Rendering implementation: `render/` (`layout` / `assets` / `draw` / `store` / `fonts`)
  - Pure logic layer: `core/` (`dto` / `models` / `draw` / `ports` / `intent` / `identity` / `limiter` / `spreads` / `dailylines` / `tarot_data` / `texts` / `config` / `settings`, no astrbot import — logging goes through the neutral facade `log.py`; the test is "can it be unit-tested")
- **Design principles (the three rules I set for myself)**: (1) A failure anywhere — storage, rendering or model — never blocks the reading: `adapters/kv_utils` swallows the exception, rendering falls back to text, the AI falls back to the built-in meanings (when the image renders, only the card's keywords; when rendering fails or in text-only mode, the full per-card meanings). (2) Deterministic by default — the same person on the same day gets the same card, the same reading and the same daily line; the surprise belongs to the persona's tone, not to the draw. (3) The reading format protocol (【第N张·位置】 markers) has one source of truth in `ai/prompts`, and `adapters/deliver` splits on exactly the regex `ai/hardening` validates.
- **Design rationale**: why things are built this way and what went wrong before lives in [`docs/design-notes/`](docs/design-notes/README.md) — one file per topic. Code comments keep only the current rules and a pointer to that topic.
- The card database lives inside `core/tarot_data.py`; the art lives in `assets/` — 78 faces (plus the unused card-back asset at `Extra/背景.png`, kept in the pack), stored as WebP; at render time a same-name .png takes precedence, then .webp/.jpg. The renderer currently uses the reading's own card art as the background and does not read the card back.
- `render/draw.py` composes the image: the background is a randomly picked card from *this* reading — its face cover-fills the canvas under a deep-navy overlay (same feel as the daily fortune card); unified white-border card style, reversed readings rotate only the card art 180° (frame and info bar stay upright) — the part I'm most proud of: a tarot reading deserves to look good
- Titles and positions sit in dark navy capsule labels; the info bar carries the orientation (gold for upright, red for reversed), the card name and its meaning keywords.
- Fonts: the bundled Noto Sans SC subset comes first (`fonts/`, SIL OFL 1.1, branded `StarFeather-*.otf`, covering every fixed string on a card; bold has its own `StarFeather-Bold` subset), with system fonts (Windows / macOS / Linux) behind it — so Chinese renders the same everywhere.
- Before anything is drawn, the text is checked for glyph coverage. If the bundled subset is missing characters, the renderer falls back to a system font that has them. The check ships a static glyph index of its own, so it still works without `fonttools` (which stays in requirements as the preferred dynamic checker). Today's card texts are fully covered — this is here for the rare character someone adds later.
- Cards are drawn from the 78 without replacement, and upright or reversed is a straight 50/50.
- Commands no longer need a `/` prefix. The framework owns the gate (wake words, group @, plain text in private chat), so `占卜 问题` on its own works in private chat. The plugin does not duplicate that gate, which keeps it from fighting the WebUI wake-word config.
- AI readings walk the provider chain in order: the pinned model (`ai.ai_provider`), the current session's provider, the global default — every loaded provider is appended only when `ai.all_providers_fallback` is on (off by default, so unexpected models are not pulled in). Each provider allows at most `ai.max_concurrency` concurrent requests (2 by default, 0 = unlimited), so two parallel stages do not queue on a single-slot local model and get cooled down as failures. Each attempt gets `ai_timeout` (30s by default; set it to 0 to leave a single request uncapped — the total budget and the spirit-line stage cap still apply), and if they all fail the built-in meanings take over (same presentation rule). A failure cools down **per provider** (`ai_fail_cooldown`, 60s by default): a dead model sits out while the rest keep serving, so nothing waits on it twice. The whole AI stage also shares a total budget, `ai_budget_seconds` (120s by default): the spirit's line and the reading — including candidate retries, provider fallbacks and the candidate lookup itself — share one deadline, and once it is spent the plugin stops issuing requests and falls back to built-in meanings (same presentation rule).
- **Natural-language entry**: ask for a reading in chat and the `star_feather_divine` tool picks it up. The tool description only fires on an explicit request, and `llm_tool_cooldown` (60s per session by default) keeps it from being spammed.
- **Fixed daily reading**: `/今日牌运` (aliases `/运势`, `/每日一签`) and fortune-word requests draw deterministically per person and per day — an md5-seeded RNG of their own that never touches the global random. The user id comes from a two-level fallback (the sender id, then `sender.user_id` read raw so int ids work; no session-id fallback in groups, so members never share a reading), and it carries a platform prefix (`platform_id:uid`, with `%` and `:` escaped) so the same numeric id on two adapters never shares a daily card, the spirit's line or the daily counter. A daily reading is cached per card fingerprint, so a changed card invalidates the old interpretation. The cache is a single KV key overwritten in place, and the card is guaranteed by the pure function even if KV is down. `output.daily_fixed` turns it off. `/单抽` is not part of this path: it always draws a fresh Feather Sign and generates its reading live, with no cache.
- **KV retention**: `sf_daily_index` is one shared key (the day's user index, capped at 500); `sf_daily_*` / `sf_cmd_cd_*` / `sf_cmd_cnt_*` / `sf_tool_cd_*` each occupy one key per user or conversation. Legacy `sf_spirit_*` keys are not enumerated or bulk-migrated: they are lazily deleted once, the next time that user reaches the daily/spirit path. Apart from that, the plugin does not delete keys on its own — the host's `PluginKVStoreMixin` exposes only get/put/delete with no enumeration, so a full scan is impossible; they are removed with the host's data when the plugin is uninstalled.
- **"Fixed card, topic-aware reading": why it works this way**: an AI reading frees card meanings from the old dictionary mapping in which one card equals one canned text. Traditional tarot pairs a card with fixed wording; here the card and your question both go into the model, so the same card yields a reading fitted to the topic — love, career, study. StarFeather doesn't let that freedom turn into a different answer every time. The variation is fenced in by determinism: the draw is fixed per person and per day, so asking a hundred times gives the same card, and the reading is per topic and holds for the day. With divination, the shakier the answer, the less real it feels.
- **One flow, four doors**: every entry runs through the same `intent.build_request` (intent), `ReadingService.prepare` (routing the draw) and `ReadingService.run` (running the reading), so settings like the shuffle hint, the card image and the disclaimer take effect in one place. The shuffle hint only appears for multi-card spreads — `/单抽` and 羽签 (single card) stay quiet.
- **Intent decisions live in one place** (`intent.py`): text in, `ReadingRequest(kind, formation, fixed, question, topic)` out. Daily detection, spread selection, topic bucketing, help detection and question cleaning all happen here, off a single vocabulary (the daily layer's `SPECIFIC_WORDS` is derived from the selection `KEYWORD_MAP`). The natural-language entry may use the tool's `formation` argument only when the rules have no signal (and only if it names one of the four spreads); command entries always follow the rules, so they stay predictable.
- **Image lifecycle**: rendering happens *after* both AI stages and just before sending, so the image only has to survive milliseconds instead of the whole AI wait. Cleanup is scheduled where the image is *created* — one 600s fuse shared by the plain card image and the daily poster, covering every way out (AI reading, fallback, abort). Each plugin instance gets its own image directory (`inst_<pid>_<seq>_<hex>`); startup reclaims expired leftovers only — an old same-process directory that still holds images is left untouched, and only an expired empty shell is reaped, so a hot reload cannot sweep away an older instance's image that is still waiting to be sent. If the directory happens to be reclaimed just before saving, it is recreated and the save retried. A final check before sending (`Renderer.ensure_image`) re-renders a vanished file.
- Questions reach the AI **clipped to 200 characters**, head and tail kept: the opening context and the closing intent both survive, and the head ends on a sentence boundary. The system prompt keeps the model on tarot and nothing else.
- Keyword → spread matching by priority: explicit spread names (new + classic aliases: Single Question / Time Flow / Three-Card Timeline / Three-Card Spread (圣三角) / Lovers' Cross, plus 羽签 / 羽时三刻 / 羽镜 / 恋羽十字) → semantic keyword weight (love → Lovers' Cross, career/study → Feather Mirror, past/future/timeline → Feather Hour Three; fortune words are handled first by the fixed-daily layer) → content inference ("he / she / us / does he love me") → defaulting to "Feather Hour Three"
- **Config defaults live in one place**: every default sits on the frozen `Settings` dataclasses in `settings.py`; the `default` fields in `_conf_schema.json` and the default column of both README config tables are written by `python -m scripts.gen_config --write`, and `--check` (plus `tests/test_config.py`) keeps CI honest
- Pure-logic unit tests in `tests/` (pytest, organized by module; case count grows with development): `test_core` (draw, card meanings, render gating, AI candidate chain and injection guard), `test_entries` (entry orchestration: daily detection, `ReadingService.run`, command entry, the closing line and the real chain), `test_golden` (refactor behaviour baseline: intent / bucketing / prompts / segmentation / render sizes), `test_tool_entry` (tool-entry direct-send contract and the AI-failure note), `test_daily_cache` (daily reading cache: per-topic buckets, day rollover, KV fallbacks), `test_spirit` (the spirit's line: generation, caching and prompt shape), `test_settings` (defaults & legacy migration), `test_spreads` (intent / selection / alias / question cleaning), `test_hardening` (injection stripping / clipping / structure validation), `test_identity` (user-id fallback chain), `test_gating` (rate-limit gate), `test_card_render` (render smoke, image cleanup), `test_clock` (date boundary: timezone resolution and the single source of "today"), `test_fonts` (font fallback/cache regression), `test_deliver` (splitting & delivery), `test_limiter`, `test_config` (config primitives), `test_dailylines` (daily line pool & deterministic pick), `test_integrity` (data integrity domain: card pool / daily line pool / glyph coverage / config schema), `test_judgement_corpus` (judgement-corpus regression: help / daily-reading boundary phrasings locked), `test_kv_utils` (KV read/write fallbacks: store failure vs no record), `test_log_port` (log facade: host injection, stable references, verbatim forwarding), `test_fault_injection` (fault-injection matrix: store failures vs each consumer's degradation contract, incl. AI exceptions never killing a reading), `test_ai_budget` (AI total budget: per-reading spending ceiling, one shared deadline across both stages, fallback), `test_structure_check` (interpretation structure check: loose/strict behaviour and strict-judgement red lines), `test_stub_signatures` (monkeypatch stub vs real signature), `test_docs_consistency` (test list vs actual files), `test_release_gates` (release red lines: the six version carriers agree, market package stays within its size limit, runtime modules are all tracked, no dev-side leftovers in the tracked set), `test_config_freshness` (config freshness self-witness: per-entry reading lines and stale-instance warnings), `test_astrbot_contract` (AstrBot contract guard: the framework surface we rely on is still alive; skipped when the framework is not installed), `test_import_model` (import-model and dependency-direction red lines: one identity per plugin file, no `sys.path` / `sys.modules` access in the plugin root, a prefix purge clears every plugin module; layer dependencies only point downward, plus a subprocess case proving that importing `services.daily` / `gating` never loads `astrbot`), `test_webui` (view backend: daily list, detail serialization, card thumbnails and registration guard), `test_adapters` (host adapters: the provider gateway treats a missing response as empty text); `pip install -r requirements-dev.txt` first (the dev toolchain's versions are pinned there, so local runs and CI install the very same set), then run `python -m pytest`. **Test import convention: always go through the AstrBot runtime package path (`from data.plugins.astrbot_plugin_star_feather.services.daily import ...`); `tests/conftest.py` wires that package chain up before collection. Plugin-root relative imports are gone** (2026-10-12: with the plugin itself on intra-package relative imports each module has exactly one identity — the old layout handed one file two module objects, so patches silently missed)

## 📜 Changelog

#### v0.10.1

- Added: the observer view (WebUI) — today's readings, topic interpretations, all 78 cards with their meanings, the four spreads, and a preview workbench (draw locally, request the AI reading only on click; no quota, no cache writes, no messages)
- Added: an observer backdrop with a mirrored live water surface, layered distant meteors, a drifting nebula, snow, stardust and golden embers
- Added: near meteors with a breathing flare, an independent group layer, and a local hue field that returns to cyan-teal once they leave it
- Changed: near meteors now fade point by point as they approach the horizon, so the trail still in the sky keeps its brightness
- Changed: the observer list follows registration order, and preview cards flip in 3D one after another; a new draw or a refresh invalidates responses still in flight
- Fixed: observer details match the daily boundary, rapid user switches no longer show stale detail, and the preview rejects duplicate cards and non-boolean upright flags
- Fixed: the daily index rotates correctly when full, and the list surfaces the most recently active users
- Fixed: readings generated before a prompt update, or with AI turned off, are no longer served; strict structure checking now covers cached readings
- Fixed: negative config values no longer silently disable protections; the overview reads with bounded concurrency and duplicate thumbnail requests for the same card are merged

#### v0.10.0

- Added: `ai.spirit_line` — turn it off to skip the spirit's opening line entirely (the `/今日牌运` poster drops its quote and sign-off too) without touching the deep reading
- Fixed: session cooldowns no longer release early — remaining time rounds up and the stored timestamp keeps sub-second precision, so a cooldown that is not truly over always reports at least one more second
- Fixed: the disclaimer is now its own message in all three send modes, AI-less fallbacks also follow `output.send_mode`, and an empty disclaimer sends nothing
- Fixed: failures in the natural-language entry's front steps (intent parsing, throttling, daily counter) no longer escape to the Agent Loop and instead send the usual failure note
- Refactored: the core is decoupled from the host — logging, events and the provider gateway now sit behind adapters, with no behaviour or config change

#### v0.9.5

- Fixed: the Pillow security floor is now 13.0.0, fonttools is 4.68.0, and tzdata is 2026.6

#### v0.9.4

- Fixed: a failed delivery no longer claims the reading was sent; the disclaimer is no longer skipped when the AI returns no reading or delivery falls back to plain text

#### v0.9.3

- Changed: card art resolves by trying the same file name with .png, .webp and .jpg in that order; PNG art dropped in place works without editing the manifest

#### v0.9.2

- Fixed: a corrupt daily-reading cache bucket is no longer treated as a hit — empty or structurally invalid content is regenerated and overwritten instead of silently delivering nothing

> Full per-version details for v0.9.0 and earlier live in [CHANGELOG.md](CHANGELOG.md) and the `changelogs/` directory — this page keeps only the six most recent releases.

---

## 🌙 Notes

- AI reading follows a **provider chain**: pinned model (`ai.ai_provider`) → current session provider → global default (`ai.all_providers_fallback` is off by default; turn it on to enumerate every loaded provider); if all fail or none is configured, built-in meanings are used automatically (when the image renders, only the card's keywords; when rendering fails or in text-only mode, the full per-card meanings) so the reading never stalls
- Readings are for entertainment only — take them with a grain of salt 🍀 The cards point at a direction; the walking is yours. Laying them out clearly is my part.

---

## ⭐ Support & Thanks

- Like it? A ⭐ on [GitHub](https://github.com/yuluo-feather/astrbot_plugin_star_feather) is all the motivation I need
- Found a bug or want a feature? [Issues](https://github.com/yuluo-feather/astrbot_plugin_star_feather/issues) and pull requests are always welcome — I read every one of them.
- **Thanks**: to 幻星集 for the card art, to Pixiv_ むっくー（成長中） for the starry-sky backdrop, to the AstrBot framework, and to everyone who filed an issue or left a star. The spirit remembers you.
