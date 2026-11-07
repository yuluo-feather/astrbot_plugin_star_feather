// 数据访问层：bridge 与演示数据都收在这里，页面只认 isDemo / apiGet / apiPost。

const bridge = window.AstrBotPluginView;
export const isDemo = !bridge;
export const CARD_BACK_ID = "__back__";

const DEMO_CARDS = [
  { id: "major_00", cn: "愚者", en: "The Fool", suit: "major" },
  { id: "major_01", cn: "魔术师", en: "The Magician", suit: "major" },
  { id: "major_02", cn: "女祭司", en: "The High Priestess", suit: "major" },
  { id: "major_03", cn: "皇后", en: "The Empress", suit: "major" },
  { id: "major_06", cn: "恋人", en: "The Lovers", suit: "major" },
  { id: "major_10", cn: "命运之轮", en: "Wheel of Fortune", suit: "major" },
  { id: "major_13", cn: "死神", en: "Death", suit: "major" },
  { id: "major_17", cn: "星星", en: "The Star", suit: "major" },
  { id: "major_18", cn: "月亮", en: "The Moon", suit: "major" },
  { id: "major_19", cn: "太阳", en: "The Sun", suit: "major" },
  { id: "cups_01", cn: "圣杯王牌", en: "Ace of Cups", suit: "cups" },
  { id: "swords_01", cn: "宝剑王牌", en: "Ace of Swords", suit: "swords" },
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
    id: card.id, cn: card.cn, en: card.en || "Demo Card", suit: "major", num: "",
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
    const items = Object.values(DEMO_RECORDS).map((r) => ({
      uid: r.uid, card_id: r.card.id, card_cn: r.card.cn, upright: r.upright,
      spirit: r.spirit, topics: r.topics.length, stale: r.stale,
    }));
    return {
      date: "20261105", items, indexed_total: items.length,
      shown: items.length, truncated: false,
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
        id: card.id, cn: card.cn, en: card.en || "Demo Card", suit: "major", num: "",
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

export async function apiGet(endpoint, params = {}) {
  if (isDemo) return demoApi(endpoint, params);
  return bridge.apiGet(endpoint, params);
}

export async function apiPost(endpoint, body = {}) {
  if (isDemo) return demoApi(endpoint, body);
  return bridge.apiPost(endpoint, body);
}

export async function connect() {
  await bridge.ready();
  return bridge.t("views.star-feather.title", "星羽塔罗 · 观测台");
}

export function demoBackURL(width) {
  const w = width;
  const h = Math.round(width * 2);
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}"
      viewBox="0 0 ${w} ${h}">
    <defs><linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#171532"/><stop offset="1" stop-color="#3a2440"/>
    </linearGradient></defs>
    <rect width="${w}" height="${h}" rx="${Math.round(w * 0.08)}" fill="url(#bg)"/>
    <rect x="${w * 0.06}" y="${h * 0.03}" width="${w * 0.88}" height="${h * 0.94}"
          rx="${Math.round(w * 0.05)}" fill="none" stroke="#e2b85f"
          stroke-opacity="0.55" stroke-width="${Math.max(1, Math.round(w * 0.012))}"/>
    <path d="M ${w / 2} ${h * 0.34} L ${w * 0.60} ${h * 0.50}
             L ${w / 2} ${h * 0.66} L ${w * 0.40} ${h * 0.50} Z"
          fill="#e2b85f" opacity="0.85"/>
    <circle cx="${w / 2}" cy="${h * 0.50}" r="${Math.round(w * 0.035)}"
            fill="#171532" opacity="0.6"/>
  </svg>`;
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

export function demoThumbURL(cardId, upright, width) {
  if (cardId === CARD_BACK_ID) return demoBackURL(width);
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

