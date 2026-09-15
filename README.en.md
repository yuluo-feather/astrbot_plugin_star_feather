<p align="center">
  <img src="https://raw.githubusercontent.com/yuluo-feather/astrbot_plugin_star_feather/main/logo_small.png" width="110" height="110" align="middle"/> <font size="6"><b>Star Feather 🪶</b></font>
</p>

<p align="center">
  "The name is mine, and so is the reading. Hmph, not bad, right?" — Yuluo
</p>

<p align="center">
  <a href="https://github.com/yuluo-feather/astrbot_plugin_star_feather/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-AGPL%20v3-ffb3d9" alt="License: AGPL v3"/></a>
  <a href="https://astrbot.app"><img src="https://img.shields.io/badge/AstrBot-Plugin-ff9ecb" alt="AstrBot Plugin"/></a>
  <img src="https://img.shields.io/badge/version-v0.7.0-f8a5c2" alt="v0.7.0"/>
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

🎀 An AstrBot tarot reading plugin — written by me. All 78 cards built in, official card art rendering, AI deep interpretation with local meanings as fallback — no external image resources needed. Got a question? Ask the deck. Hmph, but read the docs first. Don't make me repeat myself.

## 🌸 Features

- 🔮 **All 78 cards, built in**: 22 Major Arcana and 56 Minor Arcana (Wands, Cups, Swords and Pentacles). Every card has a Chinese and an English name and a layered meaning: upright readings give keywords, what's unfolding and what to do about it; reversed readings give keywords, the state you're in, where the energy sticks and how to turn it around.
- 🃏 **Four spreads**: Feather Sign, Feather Hour Three, Feather Mirror and Lovers' Feather Cross. The classic names work just as well — Single Question, Time Flow, Three-Card Timeline, Three-Card Spread, Lovers' Cross.
- 🧠 **It picks the spread for you**: Star Feather reads the keywords in your question and draws on the spread that fits. One less decision to make.
- 🤖 **AI deep reading**: the draw goes to an LLM (pin a dedicated model with `ai.ai_provider`) and comes back as one paragraph per card plus a summary. If the call fails, the built-in meanings take over, so a reading never stalls halfway.
- 🗣️ **A spirit with a personality**: the reading is spoken by the deck's spirit in one of three voices — tsundere, gentle or mystic (`ai.persona`; `off` restores the neutral tone). Each voice comes with its own tone rules and a sample of its signature style, which also shapes the spirit's opening line. `random` locks one voice per reading. Tone only: content and structure stay as they are.
- 💬 **Ask in plain words**: "帮我算一卦" or "看看我今天的运势" is enough to start a reading (natural-language entry, on when `llm_tool_enabled` is set). Nothing to memorise.
- 🗓️ **One fixed reading a day**: `/单抽` and requests carrying fortune words (运势 / 运气 / 牌运 …) return the same card and the same reading for the same person, until midnight resets it. Asking again won't reroll it.
- 🎴 **Official card art**: all 78 faces and the official card back ship inside `assets/` (WebP, with a .png fallback). A white border keeps the look consistent, and a reversed card rotates only the artwork — the frame and the info bar stay upright. Cards that look good are half the reading.
- 🛡️ **Two layers of fallback**: if the AI fails, the built-in meanings take over; if rendering fails, plain text does. Whichever part misbehaves, I won't let you go home empty-handed.

## 🃏 Commands

| Feature | Command | Description |
|---------|---------|-------------|
| 🎴 Reading | `/占卜 [question]` | Reads the question, picks the spread, draws the cards and sends the art. Fortune words (运势 / 运气 …) route to the fixed daily reading instead — except event-attribution asks such as "is it because my luck is bad?", which get a free draw. |
| 🃏 Quick draw | `/单抽` | Today's fixed fortune: one card per person per day, reset at midnight. The reading is per topic and holds for the day. You get a poster card — card art, date, the spirit's line, sign-off. |
| ❓ Help | `/占卜 帮助` / `/占卜 help` | Show usage. |

> 💡 **How to trigger it**: in private chat, `占卜 问题` is enough (`/占卜 问题` works as well). In a group, @ the bot. Set a wake word under WebUI → Settings → Wake Words and `wake-word 占卜 问题` will do it. Bare text with neither wake word nor @ stays quiet — the framework owns the gate; reading the cards is my job.

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
> Module names in brackets point to where each step lives (see [Technical Details](#technical-details)) — this section is the flow, those are the files.

```
User request (any one of three entry points)【main.py orchestration】
   │
   ├─ ① Command entry: 占卜 [question] or /占卜 [question]
   │     └─ The framework owns the gate (wake word / @ the bot / private chat); the command filter must match first
   │
   └─ ② Natural-language entry: ask in chat, e.g. "帮我算一卦" (llm_tool, switchable)
         └─ The tool description only fires on an explicit request, so complaints and small talk don't trigger it
   │
   ▼
② Rate-limit gate (one gate, three entries)【gating.py · limiter.py】
   ├─ Command: per-session throttle (cmd_rate_limit, stops double-taps)
   ├─ Natural language: per-session throttle (llm_tool_cooldown, stops spam)
   └─ Daily quota: per-user counter (daily_count, resets at midnight) → over the limit gets "come back tomorrow"
   │
   ▼
③ Draw routing (_pick_reading)【spreads.py picks the spread · daily.py handles the fixed daily draw】
   ├─ Fortune words (运势 / 运气 / 牌运) or /单抽 → today's fixed reading
   │     (one person, one day = the same card and the same reading, reset at midnight,
   │      no rerolls. Event-attribution asks — "I pinched my finger, is it because my
   │      luck is bad?" — are not fortune queries and go to a free draw. Time words go
   │      by a positive whitelist: generic ask-forms like "how have I been lately" and
   │      signature phrases like "daily tarot / one card today" stay daily, while
   │      "I keep losing sleep lately" or "is there a meeting this afternoon" do not.)
   └─ Everything else (love, career, event attribution, appointments) → free random draw
         ├─ Explicit spread name (/占卜 圣三角 考研如何) > keyword match > content inference
         │     (timeline words plus relationship semantics — "our future", "will she love me" —
         │      pick the relationship spread; pure timeline questions like "my future" don't)
         └─ No match → Feather Hour Three
   │
   ▼
④ Run the reading (_run_reading)【tarot_core.py draws and renders · interpret.py + hardening.py read】
   ├─ Shuffle hint: a random "✨ 洗牌中……" before multi-card spreads (shuffle_lines can turn it off)
   ├─ Render the card art: official assets, upright or reversed (thread pool; temp images are cleaned up after 30s,
   │  a daily poster after 300s). A collage background is built from one card picked at random out of *this*
   │  reading — cover-fill plus a deep-navy overlay — and /单抽 and the daily reading get their own poster card
   │  (art, date, the day's line from the pool, sign-off; `output.daily_card` switches it off, and a failure falls
   │  back to a plain card image)
   ├─ The spirit's line: every spread opens with one, generated by the AI in the persona's voice and fitted to this
   │  reading's cards and topic — fixed for the same person, day and card set, renewed the next day. When the AI is
   │  unavailable it falls back to the day's line from the pool. It arrives bare (no "spirit's words:" label, no
   │  quotes), right after the card art and before the reading
   └─ Generate the reading:
         ├─ Fixed daily → the day's cached reading (stays put all day)
         └─ Free random → the AI reads it live
               ├─ Provider chain: pinned model → session model → global default → every loaded provider (30s each)
               ├─ The question is cleaned first: clipped to 200 characters (head and tail kept) + injection phrases stripped (hardening)
               └─ All providers failed → built-in meanings (+ a 60s cooldown, so nothing waits in vain)
   │
   ▼
⑤ Send it (_deliver)【deliver.py orchestrates the send · settings.py decides what takes effect】
   ├─ Order: card art → the spirit's line → the reading paragraphs → disclaimer (the merged forward keeps that order)
   ├─ Split by structure: one paragraph per card plus a summary (the prompt asks for 80–140 characters each;
   │  overlong ones get cut down to `segment_size`)
   ├─ send_mode decides the shape: forward merged message (default) / plain single chain / text_only
   └─ The disclaimer goes last (disclaimer; may be empty)
   │
   ▼
⑥ Wrap up【prompts.py holds the copy · main.py sends it directly】
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
| `ai.enable_ai` | bool | `true` | Enable AI deep interpretation. When off, only built-in meanings are used. |
| `ai.ai_provider` | string | `(empty)` | Provider used for AI interpretation (dropdown). Empty = current session model; when set, the reading prefers this model (falls back to others if unavailable) without affecting the chat model. |
| `ai.ai_timeout` | int | `30` | Per-provider timeout for AI interpretation (seconds). On timeout the plugin tries the next available provider; if all fail it falls back to built-in meanings. Min 5. |
| `ai.ai_fail_cooldown` | int | `60` | Cooldown after an AI provider failure (seconds), **per provider**: a dead model only cools itself down, others keep serving; set 0 to disable. |
| `ai.persona` | select | `random` | Spirit persona for AI readings: `off`-neutral (same as before) / `tsundere`-tsundere / `gentle`-gentle / `mystic`-mystic / `random`-one persona fixed per reading (never changes mid-reading; next reading may differ). Tone only — reading content and output structure unchanged. |
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
| `output.daily_card` | bool | `true` | Daily fortune poster card. When on, `/单抽` and daily fortunes render a vertical poster (card art + date + the spirit's words + sign-off); turn off to fall back to the normal card image while the spirit's words still follow the reading. |
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

**Dependency**: card rendering needs [Pillow](https://pypi.org/project/pillow/) (usually bundled with AstrBot; otherwise `pip install -r requirements.txt`). Glyph-coverage fallback verification needs [fonttools](https://pypi.org/project/fonttools/), also listed in `requirements.txt` — those two, and nothing more.

## 🤍 Technical Details

- **Code structure (19 modules, one-way dependencies)**: `main.py` entry orchestration; `settings.py` config semantics (defaults + legacy migration); `config.py` config-read primitives (grouped-first / flat fallback / type coercion); `identity.py` event identity (2-level user-id fallback, shared by daily & rate limits); `spreads.py` formation selection & question cleaning; `tarot_core.py` draw & card presentation; `interpret.py` + `hardening.py` AI reading & prompt hardening; `log_setup.py` runtime log to disk; `deliver.py` delivery orchestration; `gating.py` + `limiter.py` rate-limit gate (KV glue) & pure logic; `kv_utils.py` KV read/write primitives (unified silent degradation); `daily.py` fixed daily reading; `dailylines.py` daily line pool & deterministic pick; `card_render.py` + `fonts.py` card rendering & font subsystem (incl. image lifecycle); `prompts.py` centralized copy (incl. help text); `tarot_data.py` card database — each module has a one-line responsibility, pure logic independently testable
- **Module layering (one-way dependencies, no cycles)**:
  - Entry orchestration: `main.py` (three entries, routing only)
  - Glue layer: `tarot_core` / `daily` / `gating` / `deliver` / `interpret` / `card_render` (KV, rate limits, delivery, rendering, AI reading — reads, writes and fallback decisions)
  - Pure logic layer: `limiter` / `spreads` / `identity` / `hardening` / `kv_utils` / `dailylines` / `tarot_data` / `prompts` / `fonts` / `config` / `settings` / `log_setup` (zero or near-zero framework dependencies, independently testable — the criterion is "can it be unit-tested standalone", not "where it lives")
- **Design principles (the three rules I set for myself)**: (1) A failure anywhere — storage, rendering or model — never blocks the reading: kv_utils swallows the exception, rendering falls back to text, the AI falls back to the built-in meanings. (2) Deterministic by default — the same person on the same day gets the same card, the same reading and the same daily line; the surprise belongs to the persona's tone, not to the draw. (3) The reading format protocol (【第N张·位置】 markers) has one source of truth in prompts, and deliver splits on exactly the regex hardening validates.
- The card database lives inside `tarot_data.py`; the art lives in `assets/` — 78 official faces plus the card back at `Extra/背景.png`, stored as WebP with a .png fallback in the loader.
- `card_render.py` composes the image: the background is a randomly picked card from *this* reading — its face cover-fills the canvas under a deep-navy overlay (same feel as the daily fortune card); unified white-border card style, reversed readings rotate only the card art 180° (frame and info bar stay upright) — the part I'm most proud of: a tarot reading deserves to look good
- Titles and positions sit in dark navy capsule labels; the info bar carries the orientation (gold for upright, red for reversed), the card name and its meaning keywords.
- Fonts: the bundled Noto Sans SC subset comes first (`fonts/`, SIL OFL 1.1, branded `StarFeather-*.otf`, covering every fixed string on a card; bold has its own `StarFeather-Bold` subset), with system fonts (Windows / macOS / Linux) behind it — so Chinese renders the same everywhere.
- Before anything is drawn, the text is checked for glyph coverage. If the bundled subset is missing characters, the renderer falls back to a system font that has them. The check ships a static glyph index of its own, so it still works without `fonttools` (which stays in requirements as the preferred dynamic checker). Today's card texts are fully covered — this is here for the rare character someone adds later.
- Cards are drawn from the 78 without replacement, and upright or reversed is a straight 50/50.
- Commands no longer need a `/` prefix. The framework owns the gate (wake words, group @, plain text in private chat), so `占卜 问题` on its own works in private chat. `_require_prefix` trusts that gate instead of second-guessing the WebUI wake-word config, and a regression test covers it.
- AI readings walk the provider chain in order: the pinned model (`ai.ai_provider`), the current session's provider, the global default, then every loaded provider (deduped by id). Each attempt gets `ai_timeout` (30s by default), and if they all fail the built-in meanings take over. A failure cools down **per provider** (`ai_fail_cooldown`, 60s by default): a dead model sits out while the rest keep serving, so nothing waits on it twice.
- **Natural-language entry**: ask for a reading in chat and the `star_feather_divine` tool picks it up. The tool description only fires on an explicit request, and `llm_tool_cooldown` (60s per session by default) keeps it from being spammed.
- **Fixed daily reading**: `/单抽` and fortune-word requests draw deterministically per person and per day — an md5-seeded RNG of their own that never touches the global random. The user id comes from a two-level fallback (the sender id, then `sender.user_id` read raw so int ids work; no session-id fallback in groups, so members never share a reading). The cache is a single KV key overwritten in place, and the card is guaranteed by the pure function even if KV is down. `output.daily_fixed` turns it off.
- **"Fixed card, topic-aware reading": why it works this way**: an AI reading frees card meanings from the old dictionary mapping in which one card equals one canned text. Traditional tarot pairs a card with fixed wording; here the card and your question both go into the model, so the same card yields a reading fitted to the topic — love, career, study. StarFeather doesn't let that freedom turn into a different answer every time. The variation is fenced in by determinism: the draw is fixed per person and per day, so asking a hundred times gives the same card, and the reading is per topic and holds for the day. With divination, the shakier the answer, the less real it feels.
- **One flow, three doors**: every entry runs through the same `_pick_reading` (routing the draw) and `_run_reading` (running the reading), so settings like the shuffle hint, the card image and the disclaimer take effect in one place. The shuffle hint only appears for multi-card spreads — `/单抽` and 羽签 (single card) stay quiet.
- **Image lifecycle**: cleanup is scheduled where the image is *created*, not where it might be sent — a delayed delete (30s for a plain card image, 300s for a daily poster) that covers every way out (AI reading, fallback, abort), plus a sweep of the previous run's leftovers at startup.
- Questions reach the AI **clipped to 200 characters**, head and tail kept: the opening context and the closing intent both survive, and the head ends on a sentence boundary. The system prompt keeps the model on tarot and nothing else.
- Keyword → spread matching by priority: explicit spread names (new + classic aliases: Single Question / Time Flow / Three-Card Timeline / Three-Card Spread (圣三角) / Lovers' Cross, plus 羽签 / 羽时三刻 / 羽镜 / 恋羽十字) → semantic keyword weight (love → Lovers' Cross, career/study → Feather Mirror, past/future/timeline → Feather Hour Three; fortune words are handled first by the fixed-daily layer) → content inference ("he / she / us / does he love me") → defaulting to "Feather Hour Three"
- Pure-logic unit tests in `tests/` (pytest, organized by module; case count grows with development): `test_core` (draw, render gating, interpreter integration, three-entry orchestration, daily fallbacks), `test_settings` (defaults & legacy migration), `test_spreads` (selection / alias / question cleaning), `test_hardening` (injection stripping / clipping / structure validation), `test_identity` (user-id fallback chain), `test_gating` (rate-limit gate), `test_log_setup` (log path candidates & idempotent install), `test_card_render` (render smoke, image cleanup), `test_fonts` (font fallback/cache regression), `test_deliver` (splitting & delivery), `test_limiter`, `test_config` (config primitives), `test_dailylines` (daily line pool & deterministic pick), `test_integrity` (data integrity domain: card pool / daily line pool / glyph coverage / config schema), `test_judgement_corpus` (judgement-corpus regression: help / daily-reading boundary phrasings locked), `test_kv_utils` (KV read/write fallbacks: store failure vs no record), `test_stub_signatures` (monkeypatch stub vs real signature), `test_docs_consistency` (test list vs actual files); `pip install pytest` first, then run `python -m pytest tests`. **Test import convention: always use plugin-root relative imports (`from daily import ...`), never `data.plugins.astrbot_plugin_star_feather.xxx` full paths** — that's the AstrBot runtime package path and breaks test collection when run standalone

## 📜 Changelog

#### v0.7.0

- Added: layered meanings for all 78 cards — instead of a string of keywords, an upright reading now gives keywords, what's unfolding and what to do about it, and a reversed reading gives keywords, the state you're in, where the energy sticks and how to turn it around. Richer material for the AI, and more guidance in the built-in fallback. The bundled font subset was extended to cover every rendered string — the new meaning texts plus the spaces and brand characters in titles, card labels, the signature and the watermark — so cards no longer show tofu boxes.
- Fixed: with `ai.persona = random`, the spirit's line and the reading body could land on different personas — a reading now draws its persona once. Corrupt cooldown data, or a model replying with nothing but quotes or whitespace, no longer aborts a reading.
- Fixed: in text-only mode the per-card meanings no longer collapse to the last card — the built-in fallback used to print only the spread's last card, and every card now gets its own block.

#### v0.6.3

- Changed: AstrBot 4.28 ready — the model behind an AI reading is now chosen through the framework's recommended async path, so readings keep working after upgrading to 4.28 instead of relying on a deprecated sync interface.

#### v0.6.2

- Fixed: card meanings are no longer lost when the AI is unavailable — in text mode, with the AI off or the reading failed, you used to get the spirit's line alone with every per-card meaning missing; the built-in meanings are now delivered as before.

#### v0.6.1

- Fixed: concurrent triggers no longer slip past the cooldown and the daily cap — when several people ask almost at once, both limits hold (storage failures still fail open).
- Fixed: jailbreak stripping covers more spellings — English identity overrides, multi-modifier instruction overrides, subject-less persona fakes, separator-padded and dotted role-name variants no longer reach the AI request.
- Fixed: the spirit's line now goes through the same adversarial cleaning as the main reading path; the question text is cleaned before either is generated.

#### v0.6.0

- Added: spirit personas — an AI reading speaks in one of three voices (tsundere / gentle / mystic) or draws one at random per reading (`ai.persona`; `off` restores the neutral tone). Each persona card carries tone rules and a sample of its signature style; content and structure are unchanged. The spirit's line joins in: every spread (Feather Sign / Feather Hour Three / Feather Mirror / Lovers' Feather Cross / the daily reading) opens with one, AI-generated in the persona's voice and fixed for the same person, day and card set, falling back to the pool when the AI is unavailable. It is sent bare — no label, no quotes, one plain sentence with an instantly readable image — right after the card art and before the reading.
- Added: the daily fortune poster — `/单抽` and the daily reading get their own poster card (art, date, the day's line, sign-off), with the pool's fixed line for that day printed on the card face; a render failure falls back to the plain card image. Ships with `output.daily_card` (on by default). The visual language carried over too: collage backgrounds for Feather Hour Three and Lovers' Feather Cross now use one card out of the reading as the base (a 3-way or 4-way pick, the same feel as the poster).
- Fixed: card font fallback hardened — with the font-checking component not yet ready (mid-install, say), card images and posters no longer show tofu boxes; missing glyphs always fall back to a system font.

#### v0.5.6

- Fixed: the group throttle message now says the spirit has just finished a reading, instead of counting someone else's cooldown against the asker.
- Fixed: help detection widened — casual phrasings like "怎么用 / 怎么玩 / help 一下" open the usage guide too.
- Fixed: event-attribution asks such as "is it because my luck is bad?" now get a free draw instead of being swallowed by the daily reading.

#### v0.5.5

- Fixed: jailbreak protection, continued — a newline tucked inside a jailbreak phrase can no longer slip past the stripping.
- Fixed: the daily reading recognises more generic time words — "这个月 / 这两天 / 这段时间" with no topic now counts as a daily reading.
- Fixed: the judgement boundary tightened — time words are now a positive whitelist ("how have I been lately / daily tarot" stay daily; "I keep losing sleep lately / is there a meeting this afternoon" get a concrete reading), and asks that combine relationship semantics with timeline words ("our future / will she love me") now pick the relationship spread, while pure timeline asks are unchanged.

#### v0.5.4

- Fixed: the AI failure cooldown is per provider — one failing model cools only itself while the others keep reading.
- Fixed: jailbreak protection strengthened — full-width, zero-width and bidi control characters can no longer dodge the injection stripping.

#### v0.5.3

- Fixed: daily reading detection tightened — "最近 / 近期 / 每日" no longer count as fortune words on their own, so a question with a specific topic gets a proper spread instead of the same fixed card all day.
- Fixed: daily readings are cached per topic — asking about two topics on the same day gives each one its own reading instead of sharing the first.
- Fixed: AI readings stay anchored to the meaning keywords in the draw instead of drifting away from them or inventing meanings.
- Fixed: help detection tightened — the word "帮助" inside a question no longer opens the help page; the whole sentence has to be a help request ("帮助 / help / 使用帮助 / 看下说明").

#### v0.5.2

- Fixed: command errors no longer leak exception details — server paths and internals go to the log, the user gets a friendly line.
- Dependency: Pillow floor raised to >=10.0.0 (known CVEs in 9.x; upgraded automatically on install).

#### v0.5.1

- Fixed: the natural-language entry no longer repeats "the reading has been sent" where it hasn't (entry off / throttled / daily cap reached / empty question) — it gives the matching hint instead.

#### v0.5.0

> Full history: [CHANGELOG.md](https://github.com/yuluo-feather/astrbot_plugin_star_feather/blob/main/CHANGELOG.md) (Chinese first, English after).
> The release in one breath: the natural-language entry (ask in chat, throttled per session), the fixed daily reading (same card and reading per person per day, reset at midnight), the AI provider chain (timeout switching + failure cooldown) and a model picked just for readings, three output modes (`send_mode`: one image-and-text chain / merged forward / text only, legacy configs migrating automatically, merged forward by default), new options (question length cap, disclaimer, shuffle hint, daily cap; upright/reversed stays pure random), rate limits on the entries (command throttle + daily counting), security hardening (injection stripping, AI output structure validation, questions clipped to 200 characters), concurrency (simultaneous readings don't block each other), the runtime requirement (Python 3.12+ / AstrBot 4.16+, below which it won't load), and a relaxed trigger gate (no forced `/` prefix — "占卜 问题" works in private chat, and AstrBot wake words and commands both work).

#### v0.4.10

- Fixed: the merged-forward node uin no longer depends on the adapter's `raw_message` (missing or shaped differently on some platforms, where it fell back to `'0'`) — it uses the framework's `event.get_self_id()`, the same lookup the framework uses for @-wake detection, and still falls back to `'0'` when it comes up empty.

#### v0.4.9

- Fixed: a `commit_msg.txt` committed by mistake is out of the repository and into `.gitignore`; four duplicate Queen assets (the same images filed under a second Chinese name) were removed, trimming about 0.47 MB from the package.
- Fixed: the `/占卜 帮助` branch now behaves like every other command path — no string of default replies after the help page.
- Font coverage fallback: texts are checked with fonttools before rendering, and a missing glyph falls back to a system font that covers it (new dependency `fonttools>=4.0`). The main font is a bundled Noto Sans SC subset (branded `StarFeather-*.otf`, covering all card copy), with rare characters outside GB2312 falling back to a system font.
- Added: `tests/` (pytest, 27 cases) — spread selection, alias stripping, splitting, drawing, glyph coverage and a rendering smoke test.
- Docs: the branded font naming (`StarFeather-*.otf`, in fact a Noto Sans SC subset) and the glyph fallback are written up.

#### v0.4.8

- A new plugin icon: pink feather, Sun tarot card and Venus (Pastel Feather Tarot).
- README reworked: logo and title side by side, pink badges (license / AstrBot / version), a table of contents, and every asset linked through absolute GitHub URLs, so GitHub, the AstrBot dashboard and the plugin market all render the same.

#### v0.4.7

- Bundled Noto Sans SC subset font (OFL): Chinese renders consistently across platforms, no more tofu boxes on Linux or macOS.
- Fixed: `@register` reported a version that disagreed with `VERSION` (0.4.5 → 0.4.7).
- The code passes ruff (import order, one statement per line, and the like).
- Added: the `logo.png` icon, plus `short_desc` and tags in metadata.
- Added: `requirements.txt` (Pillow), the AGPL-3.0 license, and this English README.

#### v0.4.6

- The shuffle hint now draws from a **pool of lines** instead of always saying the same thing.

#### v0.4.5

- The spread name is stripped from the question before the AI sees it: `/占卜 圣三角 考研如何` reads only "考研如何", with no "圣三角" muddying the meaning.
- @-everyone detection handles both shapes: `At(qq="all")` (OneBot adapters such as NapCat) and `AtAll` (a subclass). Platforms without the component never produce it, so nothing is rejected and nothing spams.

#### v0.4.4

- Spread selection became a **three-step decision**: explicit spread name > keyword weight accumulation (no more first-match-wins) > content inference > default.
- `/占卜 圣三角 考研如何` style explicit naming works, with both the new names and the classic ones (Time Flow / Three-Card Timeline / Lovers' Cross / Single Question …).
- Keywords score by how many of them hit and the highest score wins; ties break by priority order — "工作面试和我感情" correctly lands on Feather Mirror.
- The whole selection path is constant-time string scanning (around 30 `in` checks, microseconds), so it costs nothing.

#### v0.4.3

- Trigger rules aligned with the docs: in a group the bot must be **@-ed** (an At component matching its own QQ) to respond; messages with neither @ nor `/` no longer trigger, which keeps the spam down.
- Structured segments get a second cut at `segment_size`, so an overlong paragraph still sends when a model ignores the limit.
- `_render_image` validates what it returns: an empty or non-string result falls back to the text version.
- Type hints added to `_interpret_results` / `_deliver` / `_require_prefix`.

#### v0.4.2

- Robustness: structured splitting no longer depends on newlines — a model emitting "【第1张】…【第2张】…" in one run still splits correctly.
- The AI prompt is built with an f-string, so `%` or `{}` in a question can't break the formatting.
- `segment_size` falls back explicitly: unset, `None` or invalid all return to 300.
- Send interval and default segment length became module constants (`SEND_INTERVAL` / `DEFAULT_SEGMENT_SIZE` / `MIN_SEGMENT_SIZE`).
- Type hints added to `_draw` / `_render_text` / `_render_image` / `_ai_interpret` / `_pick_info`.
- The registered version was synced to v0.4.2.

#### v0.4.1

- Spreads renamed: Single Question → **Feather Sign**, Time Flow (Three-Card Timeline) → **Feather Hour Three**, Three-Card Spread → **Feather Mirror**, Lovers' Cross → **Lovers' Feather Cross**.
- The classic names all remain as keywords, so you can still ask for spread by the name you know.

#### v0.4.0

- The display name is now **Star Feather Tarot**, with `display_name` added to metadata.
- AI readings became **structured**: one paragraph per card (marked 【第N张·位置】) and a closing 【总结】 paragraph that states the result plainly.
- Splitting follows the structure marks rather than a character count; `segment_size` only serves unstructured text.
- **Images inside merged forwards**: with `forward_result` on, the card art rides as the first node of the same forwarded message as the reading paragraphs.
- Plain mode (forward off): the image goes out on its own, then the paragraphs one by one, 0.3s apart.

#### v0.3.1

- New `forward_result` option: readings are sent as a **merged forward message chain** — paragraphs packed into one forwarded message you open to read — saying goodbye to a wall of consecutive texts.
- AI readings are sent in segments by default: split at `segment_size` (300 characters) and sent one by one, 0.3s apart, in plain mode.
- Segmenting prefers paragraph breaks and falls back to length, hard-cutting overlong paragraphs.
- Merged forward nodes are signed "星羽塔罗", with the node uin taken from the bot's own QQ.

#### v0.3.0

- Renamed to **Star Feather Tarot** (formerly Cyber Tarot), plugin name `star_feather`.
- Official 幻星集 assets: all 78 card faces and the card back bundled into `assets/`.
- A unified white-border card style, with the official card back darkened as the collage background.
- Titles and positions became dark navy capsule labels — readable at a glance.
- **Command prefix required**: in private chat only `/`-prefixed commands respond (`/占卜`, `/单抽`) and bare text does nothing; in groups, @-ing the bot works.
- Hand-drawn leftovers and unused dependencies removed, rendering simplified.

#### v0.2.0

- Card images: Pillow draws the tarot cards locally and assembles the collage, so `占卜` and `单抽` send images.
- If rendering fails, the plain-text card faces go out instead — the reading still arrives.

#### v0.1.0

- All 78 cards built in (Major and Minor Arcana), with bilingual names and upright/reversed meanings.
- Four spreads with keyword matching.
- AI deep reading with the local meanings as an automatic fallback.
- No image dependencies — plain text output.

---

## 🌙 Notes

- AI reading follows a **provider chain**: pinned model (`ai.ai_provider`) → current session provider → global default → all loaded; if all fail or none is configured, built-in meanings are used automatically so the reading never stalls
- Readings are for entertainment only — take them with a grain of salt 🍀 The cards point at a direction; the walking is yours. Laying them out clearly is my part.

---

## ⭐ Support & Thanks

- Like it? A ⭐ on [GitHub](https://github.com/yuluo-feather/astrbot_plugin_star_feather) is all the motivation I need
- Found a bug or want a feature? [Issues](https://github.com/yuluo-feather/astrbot_plugin_star_feather/issues) and pull requests are always welcome — I read every one of them.
- **Thanks**: to 幻星集 for the official card art, to the AstrBot framework, and to everyone who filed an issue or left a star. The spirit remembers you.
