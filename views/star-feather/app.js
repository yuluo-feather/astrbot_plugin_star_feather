// 星羽塔罗 · 观测台：Dashboard iframe 里的只读视图。
// 数据全部经 bridge 取；直接打开 index.html 时走内置演示数据，方便看样式。
const bridge = window.AstrBotPluginView;
const DEMO = !bridge;

const $ = (id) => document.getElementById(id);
const state = {
  items: [], selected: null, cards: [], spreads: [], reversed: false,
  overview: null, cardDetail: null, detailUpright: true,
  preview: null, aiEnabled: true,
};
const thumbCache = new Map();

const SUIT_NAMES = {
  major: "大阿卡纳", wands: "权杖", cups: "圣杯", swords: "宝剑", pentacles: "星币",
};

const DEMO_CARDS = [
  { id: "major_00", cn: "愚者" }, { id: "major_01", cn: "魔术师" },
  { id: "major_02", cn: "女祭司" }, { id: "major_03", cn: "皇后" },
  { id: "major_06", cn: "恋人" }, { id: "major_10", cn: "命运之轮" },
  { id: "major_13", cn: "死神" }, { id: "major_17", cn: "星星" },
  { id: "major_18", cn: "月亮" }, { id: "major_19", cn: "太阳" },
  { id: "cups_01", cn: "圣杯王牌" }, { id: "swords_01", cn: "宝剑王牌" },
];

const DEMO_RECORDS = {
  "webchat:10001": {
    uid: "webchat:10001", date: "20261105", today: "20261105",
    card: { id: "major_17", cn: "星星", suit: "major", num: "17" },
    upright: true, stale: false, persona: "gentle",
    spirit: "别急着许愿，先把今晚的灯留给自己。",
    topics: [
      { topic: "今天感情运势", text: "【第1张·你的当下】星星正位落在你的当下，关系里的不安正在退潮。你不需要用力证明什么，先把自己的节奏找回来。\n\n【总结】先安顿自己，再谈靠近。", sections: [], summary: "" },
      { topic: "今天事业运势", text: "【第1张·你的当下】你把期待放得太远，反而看不见手边已经亮起来的那盏灯。", sections: [], summary: "" },
    ],
  },
  "webchat:10002": {
    uid: "webchat:10002", date: "20261105", today: "20261105",
    card: { id: "major_18", cn: "月亮", suit: "major", num: "18" },
    upright: false, stale: true, persona: "mystic",
    spirit: "雾里也有路，只是要先停下来听。",
    topics: [
      { topic: "今天运势", text: "【第1张·你的当下】月亮逆位把藏在雾里的东西照出来一半。别急着下结论，再等一个白天。", sections: [], summary: "" },
    ],
  },
  "webchat:10003": {
    uid: "webchat:10003", date: "20261105", today: "20261105",
    card: { id: "cups_01", cn: "圣杯王牌", suit: "cups", num: "1" },
    upright: true, stale: false, persona: "tsundere",
    spirit: "",
    topics: [],
  },
};

const DEMO_MEANING = {
  up: {
    keywords: ["新的开始", "冒险", "信任直觉"],
    what: "一条还没被标好的路正等你迈出第一步，未知里藏着机会。",
    how: "别急着问终点在哪里，先迈一步，路会自己长出来。",
  },
  down: {
    keywords: ["冲动冒险", "缺少规划", "逃避现实"],
    what: "脚步已经迈出去了，方向却还没准备好。",
    turn: "停下来核一次方向，别急着用速度盖过犹豫。",
    energy: "焦躁 ↓ 先把节奏放慢，再看下一步。",
  },
};

const DEMO_SPREADS = [
  { name: "羽签", scenario: "一句话、一件小事、此刻的状态。抽一张，看当下最关键的那点。",
    positions: [{ name: "你的当下", meaning: "此刻最需要被看见的状态" }] },
  { name: "羽时三刻", scenario: "想看一件事怎么走到现在、接下来往哪去。三张牌按时间线排开。",
    positions: [
      { name: "过去", meaning: "事情的来处" },
      { name: "现在", meaning: "此刻的落点" },
      { name: "未来", meaning: "照当前趋势的走向" },
    ] },
  { name: "羽镜", scenario: "拿不定主意、想看清卡点与下一步。像照镜子一样照出阻力。",
    positions: [
      { name: "现状", meaning: "事情现在的样子" },
      { name: "阻碍", meaning: "卡住你的那一点" },
      { name: "建议", meaning: "可以试着挪动的地方" },
    ] },
  { name: "恋羽十字", scenario: "两个人的关系：各自的状态、关系本身，以及照当前趋势的走向。",
    positions: [
      { name: "你", meaning: "你在这段关系里的状态" },
      { name: "对方", meaning: "对方此刻的状态" },
      { name: "关系现状", meaning: "两个人之间的实际温度" },
      { name: "未来走向", meaning: "照当前趋势的走向" },
    ] },
];

function demoCardDetail(cardId) {
  const card = DEMO_CARDS.find((item) => item.id === cardId) || { id: cardId, cn: cardId };
  return {
    id: card.id, cn: card.cn, en: "Demo Card", suit: "major", num: "",
    up: DEMO_MEANING.up, down: DEMO_MEANING.down,
  };
}

function demoApi(endpoint, params = {}) {
  if (endpoint === "overview") {
    return {
      version: "0.10.0", date: "20261105", users: 3, with_spirit: 2,
      stale: 1, topics: 3,
      settings: { enable_ai: true, spirit_line: true, structure_check: "loose",
                  persona: "random", send_mode: "forward", daily_card: true },
    };
  }
  if (endpoint === "daily/list") {
    return {
      date: "20261105",
      items: Object.values(DEMO_RECORDS).map((r) => ({
        uid: r.uid, card_id: r.card.id, card_cn: r.card.cn, upright: r.upright,
        spirit: r.spirit, topics: r.topics.length, stale: r.stale,
      })),
    };
  }
  if (endpoint === "daily/detail") return DEMO_RECORDS[params.uid] || null;
  if (endpoint === "cards") return { items: DEMO_CARDS };
  if (endpoint === "card-detail") return demoCardDetail(params.id);
  if (endpoint === "spreads") return { items: DEMO_SPREADS };
  if (endpoint === "preview/draw") {
    const spread = DEMO_SPREADS.find((item) => item.name === params.spread)
      || DEMO_SPREADS[1];
    const picks = spread.positions.map((position, index) => {
      const card = DEMO_CARDS[(index * 3 + 1) % DEMO_CARDS.length];
      const upright = index % 2 === 0;
      const meaning = upright ? DEMO_MEANING.up : DEMO_MEANING.down;
      return {
        id: card.id, cn: card.cn, en: "Demo Card", suit: "major", num: "",
        upright, keywords: meaning.keywords, what: meaning.what,
        advice: upright ? meaning.how : meaning.turn,
        energy: upright ? "" : meaning.energy,
      };
    });
    return {
      spread: spread.name, question: params.question || "",
      positions: spread.positions.map((position) => position.name), picks,
    };
  }
  if (endpoint === "preview/interpret") {
    return {
      text: "【第1张·你的当下】这是预览模式的示例解读。连上 AstrBot 后，"
        + "点「AI 深度解读」才会真正请求模型；在此之前只显示内置牌义。",
      sections: [], summary: "",
    };
  }
  throw new Error("预览模式没有这个接口");
}

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

function showToast(message) {
  const toast = $("toast");
  toast.textContent = message || "";
  toast.classList.toggle("show", Boolean(message));
  if (message) setTimeout(() => toast.classList.remove("show"), 4200);
}

function spawnMeteor() {
  const sky = document.querySelector(".sky");
  if (!sky) return;
  const meteor = el("span", "meteor");
  meteor.style.left = `${24 + Math.random() * 58}%`;
  meteor.style.top = `${Math.random() * 38}%`;
  meteor.style.animationDuration = `${0.9 + Math.random() * 0.7}s`;
  meteor.addEventListener("animationend", () => meteor.remove());
  sky.append(meteor);
}

function scheduleMeteor() {
  setTimeout(() => {
    spawnMeteor();
    scheduleMeteor();
  }, 3600 + Math.random() * 5200);
}

function initSky() {
  const box = $("sky-stars");
  if (!box || box.childElementCount) return;
  const count = 80;
  for (let i = 0; i < count; i += 1) {
    const star = el("span", "star");
    const size = 9 + Math.random() * 17;
    // 星云在右侧更密：多数星星落在右半，左上留出深空
    const x = Math.random() < 0.55 ? 42 + Math.random() * 58 : Math.random() * 58;
    star.style.left = `${x}%`;
    star.style.top = `${Math.pow(Math.random(), 0.85) * 68}%`;
    star.style.width = `${size}px`;
    star.style.height = `${size}px`;
    star.style.opacity = `${0.2 + Math.random() * 0.6}`;
    star.style.animationDuration = `${3 + Math.random() * 5}s`;
    star.style.animationDelay = `${Math.random() * 4}s`;
    if (Math.random() < 0.08) star.classList.add("bright");
    box.append(star);
  }
  if (!window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    spawnMeteor();
    scheduleMeteor();
  }
}

async function apiGet(endpoint, params = {}) {
  if (DEMO) return demoApi(endpoint, params);
  return bridge.apiGet(endpoint, params);
}

async function apiPost(endpoint, body = {}) {
  if (DEMO) return demoApi(endpoint, body);
  return bridge.apiPost(endpoint, body);
}

// ---- 牌面缩略图（bridge 只回 JSON，图片走 base64） ----
const observer = new IntersectionObserver((entries) => {
  for (const entry of entries) {
    if (!entry.isIntersecting) continue;
    observer.unobserve(entry.target);
    loadThumb(entry.target);
  }
}, { rootMargin: "160px" });

function demoThumbURL(cardId, upright, width) {
  const name = (DEMO_CARDS.find((card) => card.id === cardId) || {}).cn || cardId;
  const text = String(name).replace(/[<>&]/g, (c) =>
    ({ "<": "&lt;", ">": "&gt;", "&": "&amp;" }[c]));
  const w = width;
  const h = Math.round(width * 2);
  const inner = `
    <text x="50%" y="${Math.round(h * 0.42)}" text-anchor="middle"
          font-size="${Math.round(w * 0.22)}" fill="#e2b85f">✦</text>
    <text x="50%" y="${Math.round(h * 0.6)}" text-anchor="middle"
          font-size="${Math.round(w * 0.13)}" fill="#f2ead7"
          font-family="'Microsoft YaHei','PingFang SC',sans-serif">${text}</text>
    <text x="50%" y="${Math.round(h * 0.78)}" text-anchor="middle"
          font-size="${Math.round(w * 0.09)}" fill="#cbbf9a">${upright ? "正位" : "逆位"}</text>`;
  const content = upright ? inner
    : `<g transform="rotate(180 ${w / 2} ${h / 2})">${inner}</g>`;
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}"
      viewBox="0 0 ${w} ${h}">
    <defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#2a2148"/><stop offset="1" stop-color="#4a3a78"/>
    </linearGradient></defs>
    <rect width="${w}" height="${h}" rx="${Math.round(w * 0.08)}" fill="url(#g)"/>
    <rect x="2" y="2" width="${w - 4}" height="${h - 4}"
          rx="${Math.round(w * 0.07)}" fill="none"
          stroke="#e2b85f" stroke-opacity="0.55"/>${content}
  </svg>`;
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

async function thumbURL(cardId, upright, width) {
  const key = `${cardId}|${upright ? 1 : 0}|${width}`;
  if (thumbCache.has(key)) return thumbCache.get(key);
  if (DEMO) {
    const url = demoThumbURL(cardId, upright, width);
    thumbCache.set(key, url);
    return url;
  }
  const res = await apiGet("card-image", { id: cardId, upright: upright ? 1 : 0, w: width });
  const url = `data:${res.mime};base64,${res.base64}`;
  thumbCache.set(key, url);
  return url;
}

async function loadThumb(img) {
  if (img.dataset.loaded) return;
  img.dataset.loaded = "1";
  try {
    const url = await thumbURL(img.dataset.cardId, img.dataset.upright === "1",
                              Number(img.dataset.width));
    if (url) img.src = url;
  } catch (error) {
    console.warn("card-image failed", img.dataset.cardId, error);
    img.classList.add("thumb-error");
    img.alt = "图片加载失败";
  }
}

function makeThumb(cardId, upright, width, alt) {
  const img = el("img", "thumb");
  img.alt = alt || "";
  img.loading = "lazy";
  img.dataset.cardId = cardId;
  img.dataset.upright = upright ? "1" : "0";
  img.dataset.width = String(width);
  observer.observe(img);
  return img;
}

// ---- 概览 ----
const SEND_MODES = { forward: "合并转发", plain: "图文", text_only: "纯文字" };

function renderStats(data) {
  const box = $("stats");
  box.replaceChildren();
  const settings = data.settings || {};
  const chips = [
    ["今日用户", data.users],
    ["主题解读", data.topics],
    ["牌灵话", data.with_spirit],
    ["缓存过期", data.stale, data.stale > 0 ? "warn" : ""],
    ["AI", settings.enable_ai ? "开" : "关"],
    ["结构校验", settings.structure_check],
    ["发送", SEND_MODES[settings.send_mode] || settings.send_mode],
  ];
  for (const [label, value, cls] of chips) {
    const chip = el("div", `chip ${cls || ""}`);
    chip.append(el("span", "chip-label", label), el("strong", "chip-value", String(value)));
    box.append(chip);
  }
}

// ---- 今日牌运 ----
function renderDailyList() {
  const list = $("daily-list");
  list.replaceChildren();
  if (!state.items.length) {
    list.append(el("p", "empty", "今天还没有牌运记录。抽一签后这里会出现。"));
    return;
  }
  for (const item of state.items) {
    const button = el("button", "user" + (item.uid === state.selected ? " active" : ""));
    button.type = "button";
    button.append(makeThumb(item.card_id, item.upright, 84, item.card_cn));

    const body = el("div", "user-body");
    const top = el("div", "user-top");
    top.append(el("strong", "user-card", item.card_cn));
    top.append(el("span", "tag " + (item.upright ? "up" : "rev"),
                  item.upright ? "正位" : "逆位"));
    if (item.stale) top.append(el("span", "tag stale", "缓存过期"));
    body.append(top);
    body.append(el("span", "user-uid", item.uid));
    body.append(el("span", "user-spirit", item.spirit || "（今天还没有牌灵的话）"));
    body.append(el("span", "user-meta", `${item.topics} 个主题解读`));
    button.append(body);

    button.addEventListener("click", () => openDaily(item.uid));
    list.append(button);
  }
}

async function openDaily(uid) {
  state.selected = uid;
  renderDailyList();
  const box = $("daily-detail");
  box.replaceChildren(el("p", "empty", "读取中…"));
  try {
    const data = await apiGet("daily/detail", { uid });
    if (state.selected !== uid) return;  // 已被新的选择取代，别把旧响应画到新选中项下
    if (!data) throw new Error("没有这条记录");
    renderDetail(data);
  } catch (error) {
    if (state.selected !== uid) return;
    box.replaceChildren(el("p", "empty", error.message || "读取失败"));
  }
}

function renderDetail(data) {
  const box = $("daily-detail");
  box.replaceChildren();

  const head = el("div", "detail-head");
  const figure = el("div", "detail-card");
  figure.append(makeThumb(data.card.id, data.upright, 220, data.card.cn));
  head.append(figure);

  const info = el("div", "detail-info");
  const titleRow = el("div", "detail-title");
  titleRow.append(el("h2", "", data.card.cn));
  titleRow.append(el("span", "tag " + (data.upright ? "up" : "rev"),
                     data.upright ? "正位" : "逆位"));
  if (data.stale) titleRow.append(el("span", "tag stale", "缓存过期"));
  info.append(titleRow);
  info.append(el("p", "detail-uid", data.uid));
  info.append(el("p", "detail-meta",
                 `日期 ${data.date} · 人设 ${data.persona || "默认"}`));
  if (data.spirit) {
    const spirit = el("blockquote", "detail-spirit");
    spirit.append(el("span", "quote-mark", "“"));
    spirit.append(el("span", "", data.spirit));
    info.append(spirit);
  }
  head.append(info);
  box.append(head);

  if (!data.topics.length) {
    box.append(el("p", "empty", "今天还没有生成解读。"));
    return;
  }
  for (const topic of data.topics) {
    const section = el("section", "topic");
    section.append(el("h3", "topic-title", topic.topic));
    section.append(el("p", "topic-text", topic.text || topic.summary || "（空解读）"));
    box.append(section);
  }
}

// ---- 牌面预览 ----
async function loadCards() {
  if (state.cards.length) {
    renderCards();
    return;
  }
  try {
    const data = await apiGet("cards");
    state.cards = data.items || [];
    renderCards();
  } catch (error) {
    showToast(error.message || "牌面清单加载失败");
  }
}

function renderCards() {
  const grid = $("card-grid");
  grid.replaceChildren();
  for (const card of state.cards) {
    const cell = el("button", "card-cell");
    cell.type = "button";
    cell.append(makeThumb(card.id, !state.reversed, 150, card.cn));
    cell.append(el("span", "card-name", card.cn));
    cell.addEventListener("click", () => openCardDetail(card));
    grid.append(cell);
  }
}

async function openCardDetail(card) {
  try {
    const detail = await apiGet("card-detail", { id: card.id });
    state.cardDetail = detail;
    state.detailUpright = !state.reversed;
    $("card-modal").classList.remove("hidden");
    renderCardDetail();
  } catch (error) {
    showToast(error.message || "牌义加载失败");
  }
}

function renderCardDetail() {
  const detail = state.cardDetail;
  if (!detail) return;
  const upright = state.detailUpright;
  const part = (upright ? detail.up : detail.down) || {};

  $("card-modal-name").textContent = detail.cn;
  $("card-modal-en").textContent = detail.en || "";
  const suit = SUIT_NAMES[detail.suit] || detail.suit || "";
  $("card-modal-meta").textContent = `${suit}${detail.num ? ` · ${detail.num}` : ""}`;

  for (const button of $("card-orient").querySelectorAll("button")) {
    button.classList.toggle("active", button.dataset.upright === (upright ? "1" : "0"));
  }

  const img = $("card-modal-img");
  img.alt = detail.cn;
  img.removeAttribute("src");
  thumbURL(detail.id, upright, 240).then((url) => {
    if (url) img.src = url;
  }).catch(() => showToast("牌面加载失败"));

  const body = $("card-modal-body");
  body.replaceChildren();
  const keywords = Array.isArray(part.keywords) ? part.keywords : [];
  if (keywords.length) {
    const row = el("div", "kw-row");
    for (const keyword of keywords) row.append(el("span", "kw", String(keyword)));
    body.append(row);
  }
  const rows = upright
    ? [["牌义", part.what], ["建议", part.how]]
    : [["牌义", part.what], ["提醒", part.turn], ["能量", part.energy]];
  for (const [label, text] of rows) {
    if (!text) continue;
    const section = el("section", "meaning");
    section.append(el("dt", "meaning-label", label));
    section.append(el("dd", "meaning-text", String(text)));
    body.append(section);
  }
}

function closeCardDetail() {
  $("card-modal").classList.add("hidden");
  state.cardDetail = null;
}

// ---- 牌阵介绍 ----
async function ensureSpreads() {
  if (!state.spreads.length) {
    try {
      const data = await apiGet("spreads");
      state.spreads = data.items || [];
    } catch (error) {
      showToast(error.message || "牌阵清单加载失败");
    }
  }
  return state.spreads;
}

async function loadSpreads() {
  await ensureSpreads();
  renderSpreads();
}

// ---- 占卜预览：本地抽牌，AI 解读只在点击后请求 ----
let previewBusy = false;

async function loadPreviewForm() {
  const spreads = await ensureSpreads();
  const select = $("preview-spread");
  if (select.dataset.ready === "1") return;
  if (!spreads.length) {
    // 加载失败时不标记 ready，下次进标签页可以重试
    showToast("牌阵清单加载失败，稍后再试");
    return;
  }
  for (const spread of spreads) {
    const option = el("option", "", `${spread.name} · ${spread.positions.length} 张`);
    option.value = spread.name;
    select.append(option);
  }
  select.dataset.ready = "1";
}

async function drawPreview() {
  if (previewBusy) return;
  const spread = $("preview-spread").value;
  if (!spread) return;
  previewBusy = true;
  $("preview-draw").disabled = true;
  $("preview-ai").disabled = true;
  try {
    const question = $("preview-question").value.trim();
    const data = await apiPost("preview/draw", { spread, question });
    state.preview = {
      spread: data.spread,
      question: data.question || "",
      positions: data.positions || [],
      picks: data.picks || [],
      ai: null,
      aiError: "",
    };
    renderPreview();
  } catch (error) {
    showToast(error.message || "抽牌失败");
  } finally {
    previewBusy = false;
    $("preview-draw").disabled = false;
    updatePreviewAIButton();
  }
}

async function requestPreviewAI() {
  const preview = state.preview;
  if (!preview || !preview.picks.length) return;
  const button = $("preview-ai");
  button.disabled = true;
  button.textContent = "解读中…";
  preview.aiError = "";
  try {
    const data = await apiPost("preview/interpret", {
      spread: preview.spread,
      question: preview.question,
      picks: preview.picks.map((pick) => ({ id: pick.id, upright: pick.upright })),
    });
    preview.ai = data;
  } catch (error) {
    preview.aiError = error.message || "AI 解读失败";
  } finally {
    button.textContent = "AI 深度解读";
    renderPreview();
    updatePreviewAIButton();
  }
}

function updatePreviewAIButton() {
  const button = $("preview-ai");
  if (!button) return;
  const ready = Boolean(state.preview && state.preview.picks.length);
  button.disabled = !ready || !state.aiEnabled;
  button.title = state.aiEnabled ? "" : "AI 深度解读已在配置中关闭";
  $("preview-note").textContent = state.aiEnabled
    ? "预览不消耗次数、不写入缓存、不会发送到聊天"
    : "AI 已关闭，预览只显示内置牌义；不消耗次数、不写入缓存、不会发送到聊天";
}

function renderPreview() {
  const box = $("preview-result");
  box.replaceChildren();
  const preview = state.preview;
  if (!preview || !preview.picks.length) {
    box.append(el("p", "empty", "选一个牌阵，抽一次牌。"));
    return;
  }

  const head = el("div", "preview-head");
  head.append(el("h2", "", preview.spread));
  head.append(el("span", "tag", `${preview.picks.length} 张`));
  if (preview.question) head.append(el("span", "preview-question", preview.question));
  box.append(head);

  const grid = el("div", "preview-cards");
  preview.picks.forEach((pick, index) => {
    const card = el("article", "pick-card");
    const figure = el("div", "pick-figure");
    figure.append(makeThumb(pick.id, pick.upright, 150, pick.cn));
    figure.append(el("span", "pick-pos",
                     `${index + 1} · ${preview.positions[index] || ""}`));
    card.append(figure);

    const body = el("div", "pick-body");
    const title = el("div", "pick-title");
    title.append(el("strong", "", pick.cn));
    title.append(el("span", "tag " + (pick.upright ? "up" : "rev"),
                    pick.upright ? "正位" : "逆位"));
    body.append(title);
    if (Array.isArray(pick.keywords) && pick.keywords.length) {
      const row = el("div", "kw-row");
      for (const word of pick.keywords) row.append(el("span", "kw", String(word)));
      body.append(row);
    }
    if (pick.what) body.append(el("p", "pick-text", pick.what));
    if (pick.advice) body.append(el("p", "pick-advice", pick.advice));
    if (pick.energy) body.append(el("p", "pick-energy", pick.energy));
    card.append(body);
    grid.append(card);
  });
  box.append(grid);

  const ai = el("section", "ai-result");
  if (preview.ai) {
    ai.append(el("h3", "", "AI 深度解读"));
    ai.append(el("p", "ai-text", preview.ai.text || ""));
  } else if (preview.aiError) {
    ai.append(el("p", "empty", preview.aiError));
  } else {
    ai.append(el("p", "hint", state.aiEnabled
      ? "点上方「AI 深度解读」获取结构化解读。"
      : "AI 深度解读已在配置中关闭，以上是内置牌义。"));
  }
  box.append(ai);
}

function renderSpreads() {
  const box = $("spread-grid");
  box.replaceChildren();
  for (const spread of state.spreads) {
    const card = el("article", "spread-card");
    const head = el("div", "spread-head");
    head.append(el("h3", "", spread.name));
    head.append(el("span", "tag", `${spread.positions.length} 张`));
    card.append(head);
    if (spread.scenario) card.append(el("p", "spread-scenario", spread.scenario));

    const layout = el("div", "spread-layout");
    spread.positions.forEach((position, index) => {
      const slot = el("div", "spread-slot");
      slot.append(el("span", "spread-num", String(index + 1)));
      slot.append(el("strong", "spread-pos", position.name));
      if (position.meaning) slot.append(el("span", "spread-mean", position.meaning));
      layout.append(slot);
    });
    card.append(layout);
    box.append(card);
  }
}

async function openLightbox(card, upright = !state.reversed) {
  const box = $("lightbox");
  const img = $("lightbox-img");
  img.removeAttribute("src");
  img.alt = card.cn;
  box.classList.remove("hidden");
  img.dataset.loaded = "";
  try {
    const url = await thumbURL(card.id, upright, 420);
    if (url) img.src = url;
  } catch {
    showToast("大图加载失败");
  }
}

function closeLightbox() {
  $("lightbox").classList.add("hidden");
}

// ---- 装配 ----
function switchTab(tab) {
  for (const button of document.querySelectorAll(".tab")) {
    button.classList.toggle("active", button.dataset.tab === tab);
  }
  $("panel-daily").classList.toggle("hidden", tab !== "daily");
  $("panel-cards").classList.toggle("hidden", tab !== "cards");
  $("panel-spreads").classList.toggle("hidden", tab !== "spreads");
  $("panel-preview").classList.toggle("hidden", tab !== "preview");
  if (tab === "cards") loadCards();
  if (tab === "spreads") loadSpreads();
  if (tab === "preview") loadPreviewForm();
}

async function refresh() {
  try {
    const overview = await apiGet("overview");
    state.overview = overview;
    state.aiEnabled = Boolean(overview.settings && overview.settings.enable_ai);
    $("version").textContent = overview.version ? `v${overview.version}` : "";
    renderStats(overview);
    updatePreviewAIButton();
  } catch (error) {
    showToast(error.message || "概览加载失败");
  }
  try {
    const data = await apiGet("daily/list");
    state.items = data.items || [];
    const stillSelected = Boolean(state.selected)
      && state.items.some((item) => item.uid === state.selected);
    renderDailyList();
    if (stillSelected) {
      await openDaily(state.selected);  // 记录可能已变，刷新时重拉详情
    } else if (state.items.length) {
      await openDaily(state.items[0].uid);
    } else {
      state.selected = null;
      renderDailyList();
      $("daily-detail").replaceChildren(el("p", "empty", "今天还没有牌运记录。"));
    }
  } catch (error) {
    showToast(error.message || "牌运列表加载失败");
  }
}

async function boot() {
  if (bridge) {
    try {
      await bridge.ready();
      const title = bridge.t("views.star-feather.title", "星羽塔罗 · 观测台");
      $("heading").textContent = title;
      document.title = title;
    } catch {
      /* bridge 上下文拿不到也不影响只读数据 */
    }
  } else {
    $("subtitle").textContent = "预览模式 · 未连接 AstrBot";
    $("subtitle").classList.add("demo");
  }
  $("version").textContent = "";
  initSky();
  for (const button of document.querySelectorAll(".tab")) {
    button.addEventListener("click", () => switchTab(button.dataset.tab));
  }
  $("refresh").addEventListener("click", refresh);
  $("reversed").addEventListener("change", (event) => {
    state.reversed = event.target.checked;
    renderCards();
  });
  $("lightbox").addEventListener("click", closeLightbox);
  $("preview-draw").addEventListener("click", drawPreview);
  $("preview-ai").addEventListener("click", requestPreviewAI);
  $("preview-question").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      drawPreview();
    }
  });
  $("card-modal-close").addEventListener("click", closeCardDetail);
  $("card-modal").addEventListener("click", (event) => {
    if (event.target === $("card-modal")) closeCardDetail();
  });
  $("card-modal-img").addEventListener("click", () => {
    if (state.cardDetail) openLightbox(state.cardDetail, state.detailUpright);
  });
  for (const button of $("card-orient").querySelectorAll("button")) {
    button.addEventListener("click", () => {
      state.detailUpright = button.dataset.upright === "1";
      renderCardDetail();
    });
  }
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeLightbox();
      closeCardDetail();
    }
  });
  await refresh();
}

boot();
