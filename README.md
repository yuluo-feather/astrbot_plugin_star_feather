<table align="center" cellspacing="0" cellpadding="0"><tr>
  <td valign="middle"><img src="https://raw.githubusercontent.com/yuluo-feather/astrbot_plugin_star_feather/main/logo.png" width="120" height="120" alt="Star Feather"/></td>
  <td valign="middle"><h1 style="margin:0;">Star Feather 🪶</h1></td>
</tr></table>

<p align="center">
  "The name is mine, and so is the reading. Hmph, not bad, right?" — Little Feather
</p>

<p align="center">
  <a href="https://github.com/yuluo-feather/astrbot_plugin_star_feather/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-AGPL%20v3-ffb3d9" alt="License: AGPL v3"/></a>
  <a href="https://astrbot.app"><img src="https://img.shields.io/badge/AstrBot-Plugin-ff9ecb" alt="AstrBot Plugin"/></a>
  <img src="https://img.shields.io/badge/version-v0.4.7-f8a5c2" alt="v0.4.7"/>
</p>

<p align="center">🪶 ✨ 🌸 💫 🃏</p>

> [简体中文](https://github.com/yuluo-feather/astrbot_plugin_star_feather/blob/main/README.zh-CN.md) | [English](https://github.com/yuluo-feather/astrbot_plugin_star_feather/blob/main/README.md)

---

## 📖 Table of Contents

- 🌸 [Features](#features)
- 🃏 [Commands](#commands)
- 🪶 [Spreads](#spreads)
- 🎛️ [Configuration](#configuration)
- 📦 [Installation](#installation)
- 🤍 [Technical Details](#technical-details)
- 📜 [Changelog](#changelog)
- 🌙 [Notes](#notes)

---

🎀 An AstrBot tarot reading plugin. All 78 cards built in, official card art rendering, AI deep interpretation with local meanings as fallback — no external image resources needed.

## 🌸 Features

- 🔮 **All 78 cards built in**: 22 Major Arcana + 56 Minor Arcana (Wands / Cups / Swords / Pentacles), each with Chinese & English names and upright/reversed meanings
- 🃏 **Four spreads**: Feather Sign, Feather Hour Three, Feather Mirror, Lovers' Feather Cross — classic names (Single Question / Time Flow / Three-Card Timeline / Three-Card Spread / Lovers' Cross) also recognized
- 🧠 **Smart spread selection**: scans the question keywords and picks the best spread automatically
- 🤖 **AI deep interpretation**: uses your current LLM provider, one paragraph per card plus a summary
- 🎴 **Official card art**: all 78 card faces + official card back included in `assets/` (WebP format, auto-compatible with .png), unified white-border card style, reversed cards rotated 180°
- 🛡️ **Double fallback**: AI failure falls back to built-in meanings; image rendering failure falls back to plain text

## 🃏 Commands

| Feature | Command | Description |
|---------|---------|-------------|
| 🎴 Smart reading | `/占卜 [question]` | Picks the best spread by keywords, draws cards and sends card art |
| 🃏 Quick draw | `/单抽` | Draw one card (Feather Sign) for today's fortune |
| ❓ Help | `/占卜 帮助` / `/占卜 help` | Show usage |

> 💡 **Trigger rules**: in private chat, commands need a `/` prefix (e.g. `/占卜`); in groups, `@bot 占卜 …` also works. Bare text (no `/`) does not trigger.

### Example

```
/占卜 How will my relationship develop?
```

Flow: smart match "Lovers' Feather Cross" → shuffle hint → draw four cards with meanings → AI deep reading.

## 🪶 Spreads

| Spread | Cards | Positions | Trigger |
|--------|-------|-----------|---------|
| Feather Sign | 1 | Your present | Use `/单抽` |
| Feather Hour Three | 3 | Past / Present / Future | Default; or keywords like "past / future / timeline / fortune / time flow / three-card timeline" |
| Feather Mirror | 3 | Situation / Obstacle / Advice | Keywords like "career / work / interview / study / exam / promotion / three-card spread" |
| Lovers' Feather Cross | 4 | You / Them / Relationship now / Outcome | Keywords like "love / relationship / breakup / reunion / lovers' cross", or "he / she / us / does he love me" |

> 💫 Classic names (Single Question / Time Flow / Three-Card Timeline / Three-Card Spread / Lovers' Cross) are still recognized.

## 🎛️ Configuration

Configure in the AstrBot plugin management UI:

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `enable_ai` | bool | `true` | Enable AI deep interpretation. When off, only built-in meanings are used. |
| `forward_result` | bool | `false` | Send results as a **merged forward message** (card art + paragraphs in one message); requires adapter support (e.g. NapCat / Lagrange). |
| `segment_size` | int | `300` | Fallback chunk size for unstructured interpretation text (min 50). Structured segments are not affected. |

## 📦 Installation

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

**Dependency**: card rendering needs [Pillow](https://pypi.org/project/pillow/) (usually bundled with AstrBot; otherwise `pip install -r requirements.txt`).

## 🤍 Technical Details

- Card database fully embedded in `tarot_data.py`; card art in `assets/` (78 official card faces + official card back `Extra/背景.webp`; stored as WebP, loader auto-compatible with .png)
- `card_render.py` composes the image: official card back cover-filled and dimmed (brightness 0.62) as background, unified white-border card style, reversed cards rotated 180°
- Titles & positions use dark navy capsule labels; info bars show orientation (gold/red) + card name + meaning keywords
- Fonts: bundled Noto Sans SC subset (`fonts/`, SIL OFL 1.1) preferred, then system fonts (Windows / macOS / Linux), consistent cross-platform rendering
- Cards drawn without replacement from 78, upright/reversed 50/50
- `/` prefix check: bare private-chat text is blocked with a hint, group @ triggers allowed (`_require_prefix`)
- AI reading via `context.get_using_provider().text_chat()`, falls back to local meanings on errors
- Keyword → spread matching by priority: classic names + new names, then content inference, defaulting to "Feather Hour Three"

## 📜 Changelog

See [CHANGELOG.md](https://github.com/yuluo-feather/astrbot_plugin_star_feather/blob/main/CHANGELOG.md) for the full version history. Latest (v0.4.7): bundled Noto Sans SC subset font for consistent cross-platform Chinese rendering, `@register` version fix, ruff lint fixes, logo & short_desc, requirements.txt, AGPL-3.0 license, bilingual READMEs.

---

## 🌙 Notes

- AI reading depends on your configured LLM Provider; on failure the plugin uses built-in meanings automatically
- Readings are for entertainment only — take them with a grain of salt 🍀
