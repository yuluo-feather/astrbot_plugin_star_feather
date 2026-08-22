# Star Feather Tarot

> [简体中文](README.zh-CN.md)

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](LICENSE)
[![AstrBot Plugin](https://img.shields.io/badge/AstrBot-Plugin-7c3aed.svg)](https://astrbot.app)

Star Feather Tarot is an AI-powered tarot divination plugin for [AstrBot](https://astrbot.app). All 78 cards are built in, with official card art rendering, AI deep interpretation, and a built-in card meaning fallback. No external image resources required.

---

## Features

- 🔮 **All 78 cards built in**: 22 Major Arcana + 56 Minor Arcana (Wands / Cups / Swords / Pentacles), each with Chinese & English names and upright/reversed meanings
- 🃏 **Four spreads**: Feather Sign (1 card), Feather Hour Three (past / present / future), Feather Mirror (situation / obstacle / advice), Lovers' Feather Cross (4 cards) — classic names (Single Question / Time Flow / Three-Card Timeline / Three-Card Spread / Lovers' Cross) are also recognized
- 🧠 **Smart spread selection**: scans the question keywords and picks the best spread automatically
- 🤖 **AI deep interpretation**: uses your current LLM provider, one paragraph per card plus a summary
- 🎴 **Official card art**: all 78 card faces + official card back included in `assets/` (compressed WebP, auto-compatible with .png), unified white-border card style, reversed cards rotated 180°
- 🛡️ **Double fallback**: AI failure falls back to built-in meanings; image rendering failure falls back to plain text

---

## Commands

| Function | Command | Description |
|----------|---------|-------------|
| 🎴 Smart divination | `/占卜 [question]` | Auto-selects a spread by keywords, draws cards, outputs card image |
| 🃏 Quick single draw | `/单抽` | Draws one card (Feather Sign) for today's fortune |
| ❓ Help | `/占卜 帮助` / `/占卜 help` | Shows usage instructions |

> **Trigger rule**: in private chats, commands must have a `/` prefix (e.g. `/占卜`); in groups, `@bot 占卜 …` also works. Plain text without `/` will not trigger.

### Example

```
/占卜 我和她最近的感情会怎么发展
```

Flow: smart match "Lovers' Feather Cross" spread → shuffle hint → output four cards with upright/reversed meanings → AI deep interpretation.

---

## Spreads

| Spread | Cards | Positions | Trigger |
|--------|-------|-----------|---------|
| Feather Sign (羽签) | 1 | Your present | `/单抽` command |
| Feather Hour Three (羽时三刻) | 3 | Past / Present / Future | Default spread; or question contains "过去 / 未来 / 时间线 / 运势 / 时间之流 / 三张时间线" |
| Feather Mirror (羽镜) | 3 | Situation / Obstacle / Advice | Question contains "事业 / 工作 / 面试 / 学业 / 考试 / 考研 / 升职 / 圣三角" |
| Lovers' Feather Cross (恋羽十字) | 4 | You / The Other / Relationship Status / Future | Question contains "情感 / 爱情 / 恋爱 / 分手 / 复合 / 恋人十字", or "他 / 她 / 我们 / 喜欢我吗 / 还爱" |

> Classic names (Single Question / Time Flow / Three-Card Timeline / Three-Card Spread / Lovers' Cross) are also recognized as keywords.

---

## Configuration

Configure in the AstrBot plugin management UI:

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `enable_ai` | bool | `true` | Enable AI deep interpretation (uses the configured LLM). Disable to use built-in meanings only. |
| `forward_result` | bool | `false` | Send results as a merged forward message chain. Requires adapter support (e.g. NapCat / Lagrange). |
| `segment_size` | int | `300` | Fallback chunk size for unstructured interpretation text (min 50). Structured segments are not affected. |

---

## Installation

### Option 1: Install from AstrBot Plugin Market (Recommended)

1. Open the AstrBot dashboard and go to "Plugin Market / Install Plugin"
2. Search for "Star Feather Tarot" in the market and click "Install"
3. Wait for the plugin to download and install dependencies automatically
4. After installation, find "Star Feather Tarot" in the "AstrBot Plugins" page and click "Reload Plugin"

### Option 2: Install from GitHub Repository URL

1. Open the AstrBot dashboard and go to "Plugin Market / Install Plugin"
2. Select "Install from GitHub repository URL" and enter:

   `https://github.com/yuluo-feather/astrbot_plugin_star_feather`

3. Click install and wait for the plugin to download and install dependencies automatically
4. After installation, find "Star Feather Tarot" in the "AstrBot Plugins" page and click "Reload Plugin"

### Option 3: Manual Installation

1. Clone or download this repository into AstrBot's `data/plugins/` directory, keeping the directory name as `astrbot_plugin_star_feather`:

   ```bash
   cd AstrBot/data/plugins
   git clone https://github.com/yuluo-feather/astrbot_plugin_star_feather.git
   ```

2. Restart AstrBot, the plugin will load automatically
3. Send `/占卜 我的问题` in chat to begin

**Dependency**: [Pillow](https://pypi.org/project/pillow/) is required for card art rendering (usually bundled with AstrBot; if missing, run `pip install -r requirements.txt`).

---

## Technical Details

- Card database fully embedded in `tarot_data.py`; card art in `assets/` (78 official card faces + official card back `Extra/背景.webp`; stored as compressed WebP, loader auto-compatible with .png)
- `card_render.py` composes the image: official card back cover-filled and dimmed (brightness 0.62) as background, unified white-border card style, reversed cards rotated 180°
- Title & position labels use dark navy capsules; info bar shows upright/reversed (gold/red) + card name + keyword meanings
- Fonts are loaded from the bundled Noto Sans SC subset (`fonts/`, SIL OFL 1.1) first, then common system fonts (Windows / macOS / Linux) — rendering looks identical across platforms
- Cards drawn without replacement from 78, upright/reversed 50/50
- `/` prefix required in private chats; group @ mention works (`_require_prefix`)
- AI interpretation via `context.get_using_provider().text_chat()`, auto-falls back to local meanings on error
- Keywords → spread matching: classic names first, then content inference, defaulting to Feather Hour Three

---

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for the full version history. Latest (v0.4.7): bundled Noto Sans SC subset font for consistent cross-platform Chinese rendering, `@register` version fix, ruff lint fixes, logo & short_desc, requirements.txt, AGPL-3.0 license, bilingual READMEs.

---

## Notes

- AI interpretation requires a configured LLM provider; falls back to built-in meanings otherwise
- Results are for entertainment only. Please use them responsibly.
