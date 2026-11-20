// 星羽塔罗 · 观测台：Dashboard iframe 里的只读视图。
// 数据访问在 feathers/api.js，DOM 工具在 feathers/dom.js；这里只放页面功能与装配。
import { $, el, showToast } from "./feathers/dom.js";
import {
  apiGet, apiPost, connect, connectionState, connectionTitle,
  onConnectionChange,
} from "./feathers/api.js";
import { init as initBackground, start as startBackground } from "./feathers/background/index.js";
import {
  thumbURL, ensureCardBack, getCardBackURL, loadThumb, makeThumb,
} from "./feathers/card-images.js";

const state = {
  items: [], selected: null, cards: [], spreads: [], reversed: false, suitFilter: "all",
  overview: null, cardDetail: null, detailUpright: true,
  preview: null, aiEnabled: true,
  listIndexedTotal: 0, listShown: 0, listTruncated: false,
  detailUid: "", cardsLoaded: false, spreadsLoaded: false,
  fetchedAt: { overview: 0, daily: 0, detail: 0, cards: 0, spreads: 0 },
  errors: {
    overview: false, list: false, detail: false,
    cards: false, spreads: false, preview: false,
  },
};
let detailSeq = 0;
let refreshSeq = 0;
let dataEpoch = 0;
let cardsRequest = null;
let spreadsRequest = null;
let lightboxTarget = null;

const SUIT_NAMES = {
  major: "大阿卡纳", wands: "权杖", cups: "圣杯", swords: "宝剑", pentacles: "星币",
};
const SUBTITLE_DEFAULT = "今日牌运与牌面，只读";
const TABS = new Set(["daily", "cards", "spreads", "preview"]);
let currentTab = "daily";

function formatTime(ts) {
  if (!ts) return "—";
  const date = new Date(ts);
  return `${String(date.getHours()).padStart(2, "0")}:`
    + `${String(date.getMinutes()).padStart(2, "0")}`;
}

function formatDay(value) {
  if (!value || value.length !== 8) return value || "";
  return `${value.slice(0, 4)}-${value.slice(4, 6)}-${value.slice(6)}`;
}

function renderFreshness() {
  const day = state.overview && state.overview.date
    ? formatDay(state.overview.date) : "";
  const parts = [];
  if (day) parts.push(`数据 ${day}`);
  parts.push(`概览 ${formatTime(state.fetchedAt.overview)}`);
  parts.push(`列表 ${formatTime(state.fetchedAt.daily)}`);
  for (const [key, label] of [["detail", "详情"], ["cards", "牌面"], ["spreads", "牌阵"]]) {
    if (state.fetchedAt[key]) parts.push(`${label} ${formatTime(state.fetchedAt[key])}`);
  }
  $("freshness").textContent = parts.join(" · ");
}

function setError(key, message, retry) {
  const strip = $(`error-${key}`);
  if (!strip) return;
  state.errors[key] = Boolean(message);
  strip.classList.toggle("hidden", !message);
  strip.querySelector(".error-text").textContent = message || "";
  const button = strip.querySelector(".error-retry");
  button.onclick = retry || null;
  button.disabled = !retry;
}

function clearError(key) {
  setError(key, "", null);
}

function delayedPlaceholder(box, build, isCurrent = () => true, ms = 150) {
  const timer = setTimeout(() => {
    if (isCurrent()) box.replaceChildren(build());
  }, ms);
  return () => clearTimeout(timer);
}

// ---- 概览 ----
const SEND_MODES = { forward: "合并转发", plain: "图文", text_only: "纯文字" };
// 人设取值见 core/settings.py 的 PERSONA_VALUES，这里只负责显示成中文
const PERSONA_NAMES = {
  off: "中立", tsundere: "傲娇", gentle: "温柔", mystic: "神秘", random: "随机",
};

function renderStats(data) {
  const box = $("stats");
  box.replaceChildren();
  const settings = data.settings || {};
  const cards = [
    ["今日用户", data.users],
    ["主题解读", data.topics],
    ["牌灵低语", data.with_spirit],
  ];
  const strip = [
    ["缓存过期", data.stale, data.stale > 0 ? "warn" : ""],
    ["AI", settings.enable_ai ? "开" : "关"],
    ["结构校验", settings.structure_check],
    ["发送", SEND_MODES[settings.send_mode] || settings.send_mode],
  ];
  const cardBox = el("div", "stat-cards");
  for (const [label, value] of cards) {
    const card = el("div", "stat-card");
    card.append(el("span", "stat-label", label), el("strong", "stat-value", String(value)));
    cardBox.append(card);
  }
  const stripBox = el("div", "stat-strip");
  for (const [label, value, cls] of strip) {
    const item = el("span", `stat-item ${cls || ""}`);
    item.append(el("span", "stat-label", label), el("strong", "stat-value", String(value)));
    stripBox.append(item);
  }
  box.append(cardBox, stripBox);
}

// ---- 今日牌运 ----
function renderDailyList() {
  const list = $("daily-list");
  list.replaceChildren();
  const note = $("daily-note");
  if (state.listTruncated) {
    note.textContent = `当前索引覆盖 ${state.listIndexedTotal} 位用户，`
      + `页面显示最近 ${state.listShown} 位`;
    note.classList.remove("hidden");
  } else {
    note.textContent = "";
    note.classList.add("hidden");
  }
  if (!state.items.length) {
    list.append(el("p", "empty", "今天还没有牌运记录。在聊天里抽一签后这里会出现。"));
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

async function openDaily(uid, { preserve = false } = {}) {
  const seq = ++detailSeq;
  const box = $("daily-detail");
  const previousUid = state.detailUid;
  const sameDetail = previousUid === uid
    && Boolean(box.querySelector(".detail-head"));
  state.selected = uid;
  renderDailyList();
  clearError("detail");
  let cancelSkeleton = () => {};
  if (!preserve || !sameDetail) {
    box.replaceChildren();
    cancelSkeleton = delayedPlaceholder(
      box, detailSkeleton,
      () => seq === detailSeq && state.selected === uid);
  }
  try {
    const data = await apiGet("daily/detail", { uid });
    if (seq !== detailSeq || state.selected !== uid) return;
    if (!data) throw new Error("没有这条记录");
    state.detailUid = uid;
    state.fetchedAt.detail = Date.now();
    renderFreshness();
    renderDetail(data);
  } catch (error) {
    if (seq !== detailSeq || state.selected !== uid) return;
    const message = error.message || "读取失败";
    if (preserve && sameDetail) {
      setError("detail", `刷新失败：${message}`,
               () => openDaily(uid, { preserve: true }));
    } else {
      state.detailUid = "";
      const text = previousUid && previousUid !== uid
        ? `该用户读取失败：${message}` : message;
      box.replaceChildren(el("p", "empty", text));
      setError("detail", text, () => openDaily(uid));
    }
  } finally {
    cancelSkeleton();
  }
}

function detailSkeleton() {
  const wrap = el("div", "detail-loading");
  const head = el("div", "detail-head");
  head.append(el("div", "detail-card skeleton-card"));
  const info = el("div", "detail-info");
  info.append(el("div", "skeleton-line wide"));
  info.append(el("div", "skeleton-line"));
  info.append(el("div", "skeleton-line short"));
  head.append(info);
  wrap.append(head);
  for (const cls of ["wide", "", "short"]) {
    wrap.append(el("div", `skeleton-line ${cls}`.trim()));
  }
  return wrap;
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
                 `日期 ${data.date} · 人设 ${PERSONA_NAMES[data.persona] || data.persona || "默认"}`));
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
async function loadCards({ force = false } = {}) {
  const epoch = dataEpoch;
  if (state.cardsLoaded && !force) {
    renderSuitFilter();
    renderCards();
    return;
  }
  const box = $("card-grid");
  const cancel = state.cards.length ? () => {} : delayedPlaceholder(
    box, () => el("p", "empty loading", "牌面加载中…"),
    () => epoch === dataEpoch);
  let request = null;
  try {
    if (!cardsRequest || cardsRequest.epoch !== dataEpoch) {
      const requestEpoch = dataEpoch;
      cardsRequest = {
        epoch: requestEpoch,
        promise: apiGet("cards").then((data) => data.items || []),
      };
    }
    request = cardsRequest;
    const items = await request.promise;
    if (request.epoch !== dataEpoch) return;
    state.cards = items;
    state.cardsLoaded = true;
    state.fetchedAt.cards = Date.now();
    renderFreshness();
    clearError("cards");
    renderSuitFilter();
    renderCards();
  } catch (error) {
    if (epoch !== dataEpoch) return;
    const message = error.message || "读取失败";
    if (!state.cards.length) {
      box.replaceChildren(el("p", "empty", "牌面加载失败"));
    }
    setError("cards", `牌面加载失败：${message}`,
             () => loadCards({ force: true }));
  } finally {
    cancel();
    if (cardsRequest === request) cardsRequest = null;
  }
}

function renderSuitFilter() {
  const box = $("suit-filter");
  box.replaceChildren();
  for (const [key, label] of [["all", "全部"], ...Object.entries(SUIT_NAMES)]) {
    const button = el("button", "suit-chip" + (state.suitFilter === key ? " active" : ""));
    button.type = "button";
    button.dataset.suit = key;
    button.textContent = label;
    button.addEventListener("click", () => {
      state.suitFilter = key;
      renderCards();
    });
    box.append(button);
  }
}

function renderCards() {
  const grid = $("card-grid");
  grid.replaceChildren();
  if (state.cardsLoaded && !state.cards.length) {
    grid.append(el("p", "empty", "没有可显示的牌面。"));
    return;
  }
  for (const button of $("suit-filter").querySelectorAll(".suit-chip")) {
    button.classList.toggle("active", button.dataset.suit === state.suitFilter);
  }
  const cards = state.suitFilter === "all"
    ? state.cards
    : state.cards.filter((card) => card.suit === state.suitFilter);
  for (const card of cards) {
    const cell = el("button", "card-cell");
    cell.type = "button";
    const figure = el("div", "card-figure");
    figure.append(makeThumb(card.id, !state.reversed, 150, card.cn));
    cell.append(figure);
    cell.append(el("span", "card-name", card.cn));
    if (card.en) cell.append(el("span", "card-en", card.en));
    cell.addEventListener("click", () => openCardDetail(card));
    grid.append(cell);
  }
}

let cardDetailSeq = 0;
let cardImageSeq = 0;

async function openCardDetail(card) {
  const seq = ++cardDetailSeq;
  try {
    const detail = await apiGet("card-detail", { id: card.id });
    if (seq !== cardDetailSeq) return;  // 已被更新的点击或关闭取代
    state.cardDetail = detail;
    state.detailUpright = !state.reversed;
    $("card-modal").classList.remove("hidden");
    renderCardDetail();
  } catch (error) {
    if (seq !== cardDetailSeq) return;
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

  const imageSeq = ++cardImageSeq;
  const img = $("card-modal-img");
  img.alt = detail.cn;
  img.removeAttribute("src");
  thumbURL(detail.id, upright, 240).then((url) => {
    if (imageSeq !== cardImageSeq) return;  // 已被新的牌/朝向/关闭取代
    if (url) img.src = url;
  }).catch(() => {
    if (imageSeq === cardImageSeq) showToast("牌面加载失败");
  });

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
  cardDetailSeq += 1;  // 关掉后，未返回的详情请求作废
  cardImageSeq += 1;   // 未返回的缩略图请求同样作废
  $("card-modal").classList.add("hidden");
  state.cardDetail = null;
}

// ---- 牌阵介绍 ----
async function ensureSpreads({ force = false } = {}) {
  if (state.spreadsLoaded && !force) {
    return { ok: true, items: state.spreads };
  }
  let request = null;
  try {
    if (!spreadsRequest || spreadsRequest.epoch !== dataEpoch) {
      const requestEpoch = dataEpoch;
      spreadsRequest = {
        epoch: requestEpoch,
        promise: apiGet("spreads").then((data) => data.items || []),
      };
    }
    request = spreadsRequest;
    const items = await request.promise;
    if (request.epoch !== dataEpoch) return { ok: false, stale: true };
    state.spreads = items;
    state.spreadsLoaded = true;
    state.fetchedAt.spreads = Date.now();
    renderFreshness();
    clearError("spreads");
    return { ok: true, items };
  } catch (error) {
    if (!request || request.epoch !== dataEpoch) {
      return { ok: false, stale: true };
    }
    return { ok: false, error };
  } finally {
    if (spreadsRequest === request) spreadsRequest = null;
  }
}

async function loadSpreads({ force = false } = {}) {
  const epoch = dataEpoch;
  const box = $("spread-grid");
  const cancel = state.spreads.length ? () => {} : delayedPlaceholder(
    box, () => el("p", "empty loading", "牌阵加载中…"),
    () => dataEpoch === epoch);
  const result = await ensureSpreads({ force });
  cancel();
  if (result.stale) return;
  if (!result.ok) {
    if (!state.spreads.length) {
      box.replaceChildren(el("p", "empty", "牌阵加载失败"));
    }
    setError("spreads",
             `牌阵加载失败：${result.error.message || "读取失败"}`,
             () => loadSpreads({ force: true }));
    return;
  }
  renderSpreads();
}

// ---- 占卜预览：本地抽牌，AI 解读只在点击后请求 ----
let previewBusy = false;
let previewAiSeq = 0;
let previewSeq = 0;

async function loadPreviewForm({ force = false } = {}) {
  const select = $("preview-spread");
  if (select.dataset.ready === "1" && !force) return;
  select.dataset.ready = "";
  select.disabled = true;
  const loading = el("option", "", "牌阵加载中…");
  loading.value = "";
  select.replaceChildren(loading);
  const result = await ensureSpreads({ force });
  if (result.stale) {
    select.disabled = false;
    return;
  }
  select.disabled = false;
  if (!result.ok) {
    const failed = el("option", "", "牌阵加载失败");
    failed.value = "";
    select.replaceChildren(failed);
    setError("preview",
             `牌阵加载失败：${result.error.message || "读取失败"}`,
             () => loadPreviewForm({ force: true }));
    return;
  }
  clearError("preview");
  select.replaceChildren();
  if (!result.items.length) {
    const option = el("option", "", "暂无牌阵");
    option.value = "";
    select.append(option);
    select.dataset.ready = "1";
    return;
  }
  for (const spread of result.items) {
    const option = el("option", "", `${spread.name} · ${spread.positions.length} 张`);
    option.value = spread.name;
    select.append(option);
  }
  select.dataset.ready = "1";
  ensureCardBack().catch(() => {});
}

async function drawPreview() {
  if (previewBusy) return;
  const select = $("preview-spread");
  const spread = select.value;
  if (!spread) {
    showToast(select.disabled ? "牌阵还在加载，请稍候" : "暂无可选牌阵");
    return;
  }
  const seq = ++previewSeq;
  previewBusy = true;
  previewAiSeq += 1;  // 抽牌一开始就作废上一轮 AI，不必等抽牌结果返回
  $("preview-draw").disabled = true;
  $("preview-ai").disabled = true;
  try {
    const question = $("preview-question").value.trim();
    const data = await apiPost("preview/draw", { spread, question });
    if (seq !== previewSeq) return;
    state.preview = {
      spread: data.spread,
      question: data.question || "",
      positions: data.positions || [],
      picks: data.picks || [],
      ai: null,
      aiError: "",
    };
    renderPreview({ animate: true });
  } catch (error) {
    if (seq === previewSeq) showToast(error.message || "抽牌失败");
  } finally {
    if (seq === previewSeq) {
      previewBusy = false;
      $("preview-draw").disabled = false;
      updatePreviewAIButton();
    }
  }
}

function resetPreview() {
  previewSeq += 1;
  previewAiSeq += 1;
  previewBusy = false;
  state.preview = null;
  const draw = $("preview-draw");
  const ai = $("preview-ai");
  if (draw) draw.disabled = false;
  if (ai) {
    ai.disabled = true;
    ai.textContent = "AI 深度解读";
  }
  renderPreview();
  updatePreviewAIButton();
}

async function requestPreviewAI() {
  const preview = state.preview;
  if (!preview || !preview.picks.length) return;
  const seq = ++previewAiSeq;
  const button = $("preview-ai");
  button.disabled = true;
  button.textContent = "解读中…";
  preview.aiError = "";
  try {
    const data = await apiPost("preview/interpret", {
      spread: preview.spread,
      question: preview.question,
      picks: preview.picks.map((pick) => ({ id: pick.id, upright: pick.upright })),
    }, { timeoutMs: 0 });  // AI 解读耗时由服务端预算控制，客户端不另设超时
    if (seq !== previewAiSeq || state.preview !== preview) return;
    preview.ai = data;
  } catch (error) {
    if (seq !== previewAiSeq || state.preview !== preview) return;
    preview.aiError = error.message || "AI 解读失败";
  } finally {
    if (seq === previewAiSeq) {
      button.textContent = "AI 深度解读";
      updateAiBlock({ animate: true });
      updatePreviewAIButton();
    }
  }
}

function updatePreviewAIButton() {
  const button = $("preview-ai");
  if (!button) return;
  const ready = Boolean(state.preview && state.preview.picks.length) && !previewBusy;
  button.disabled = !ready || !state.aiEnabled;
  button.title = state.aiEnabled ? "" : "AI 深度解读已在配置中关闭";
  $("preview-note").textContent = state.aiEnabled
    ? "预览不消耗次数、不写入缓存、不会发送到聊天"
    : "AI 已关闭，预览只显示内置牌义；不消耗次数、不写入缓存、不会发送到聊天";
}

function renderPreview({ animate = false } = {}) {
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
    if (animate) {
      card.classList.add("flip-animate");
      card.style.setProperty("--flip-delay", `${index * 130}ms`);
    }
    const figure = el("div", "pick-figure");
    const thumb = el("div", "pick-thumb");
    const flip = el("div", "pick-flip");
    const back = el("div", "pick-face pick-back");
    const backURL = getCardBackURL();
    if (backURL) back.style.backgroundImage = `url("${backURL}")`;
    const front = el("div", "pick-face pick-front");
    front.append(makeThumb(pick.id, pick.upright, 150, pick.cn));
    flip.append(back, front);
    thumb.append(flip);
    if (animate) {
      if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
        card.classList.add("card-flipped");
      } else {
        front.addEventListener("animationend", () => card.classList.add("card-flipped"), { once: true });
      }
    }
    figure.append(thumb);
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
  box.append(buildAiBlock(preview, false));
  if (animate) {
    // 等所有正面和牌背都就绪后再统一起跳：否则先 decode 完的牌会抢先翻，
    // 顺序和间隔都会随图片加载时间漂移。
    const fronts = [...grid.querySelectorAll(".pick-front .thumb")];
    Promise.all(fronts.map((img) => loadThumb(img)))
      .then(() => ensureCardBack())
      .then((url) => {
        if (url) {
          box.querySelectorAll(".pick-back").forEach((node) => {
            node.style.backgroundImage = `url("${url}")`;
          });
        }
        grid.classList.add("flips-ready");
      })
      .catch(() => {
        grid.classList.add("flips-ready");
      });
  }
}

function buildAiBlock(preview, animate = false) {
  const ai = el("section", "ai-result");
  if (animate) ai.classList.add("ai-reveal");
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
  return ai;
}

function updateAiBlock({ animate = false } = {}) {
  const box = $("preview-result");
  const old = box.querySelector(".ai-result");
  if (!old || !state.preview) return;
  old.replaceWith(buildAiBlock(state.preview, animate));
}

function renderSpreads() {
  const box = $("spread-grid");
  box.replaceChildren();
  if (state.spreadsLoaded && !state.spreads.length) {
    box.append(el("p", "empty", "没有可显示的牌阵。"));
    return;
  }
  let expanded = null;
  for (const spread of state.spreads) {
    const card = el("article", "spread-card");
    const toggle = el("button", "spread-toggle");
    toggle.type = "button";
    toggle.setAttribute("aria-expanded", "false");
    toggle.append(el("span", "spread-name", spread.name));
    toggle.append(el("span", "spread-count", `${spread.positions.length}张`));

    const body = el("div", "spread-body");
    if (spread.scenario) body.append(el("p", "spread-scenario", spread.scenario));
    const layout = el("div", "spread-layout");
    spread.positions.forEach((position, index) => {
      const slot = el("div", "spread-slot");
      slot.append(el("span", "spread-num", String(index + 1)));
      slot.append(el("strong", "spread-pos", position.name));
      if (position.meaning) slot.append(el("span", "spread-mean", position.meaning));
      layout.append(slot);
    });
    body.append(layout);

    toggle.addEventListener("click", () => {
      const open = !card.classList.contains("expanded");
      if (expanded && expanded !== card) {
        expanded.classList.remove("expanded");
        expanded.querySelector(".spread-toggle").setAttribute("aria-expanded", "false");
      }
      card.classList.toggle("expanded", open);
      toggle.setAttribute("aria-expanded", String(open));
      expanded = open ? card : null;
    });

    card.append(toggle, body);
    box.append(card);
  }
}

let lightboxSeq = 0;

async function openLightbox(card, upright = !state.reversed) {
  const seq = ++lightboxSeq;
  const box = $("lightbox");
  const img = $("lightbox-img");
  const status = $("lightbox-status");
  const retry = $("lightbox-retry");
  lightboxTarget = { card, upright };
  img.removeAttribute("src");
  img.alt = card.cn;
  box.classList.remove("hidden");
  status.textContent = "大图加载中…";
  status.classList.remove("hidden");
  retry.classList.add("hidden");
  try {
    const url = await thumbURL(card.id, upright, 420);
    if (seq !== lightboxSeq) return;  // 已关闭或已换牌
    if (!url) throw new Error("empty image");
    const loaded = new Promise((resolve, reject) => {
      img.addEventListener("load", resolve, { once: true });
      img.addEventListener("error", () => reject(new Error("image load failed")), {
        once: true,
      });
    });
    img.src = url;
    await loaded;
    if (seq !== lightboxSeq) return;
    status.classList.add("hidden");
  } catch {
    if (seq !== lightboxSeq) return;
    status.textContent = "大图加载失败";
    status.classList.remove("hidden");
    retry.classList.remove("hidden");
  }
}

function closeLightbox() {
  lightboxSeq += 1;  // 关闭后，未返回的大图请求作废
  lightboxTarget = null;
  $("lightbox").classList.add("hidden");
  $("lightbox-status").classList.add("hidden");
  $("lightbox-retry").classList.add("hidden");
}

// ---- 装配 ----
function tabFromURL() {
  const raw = new URLSearchParams(window.location.search).get("tab");
  return TABS.has(raw) ? raw : "daily";
}

function syncTabURL(tab, { replace = false } = {}) {
  const url = new URL(window.location.href);
  url.searchParams.set("tab", tab);
  if (url.href === window.location.href) return;
  history[replace ? "replaceState" : "pushState"](null, "", url);
}

function normalizeTabURL() {
  const raw = new URLSearchParams(window.location.search).get("tab");
  if (raw === null || TABS.has(raw)) return;
  const url = new URL(window.location.href);
  url.searchParams.set("tab", "daily");
  history.replaceState(null, "", url);
}

function resetScroll() {
  const reduce = window.matchMedia
    && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  window.scrollTo({ top: 0, behavior: reduce ? "auto" : "smooth" });
  for (const node of document.querySelectorAll(".list, .detail")) {
    node.scrollTop = 0;
  }
}

function switchTab(tab, { push = false, scroll = true, load = true } = {}) {
  const next = TABS.has(tab) ? tab : "daily";
  const changed = next !== currentTab;
  currentTab = next;
  for (const button of document.querySelectorAll(".tab")) {
    const active = button.dataset.tab === next;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
    button.tabIndex = active ? 0 : -1;
  }
  for (const panel of document.querySelectorAll(".panel")) {
    const active = panel.id === `panel-${next}`;
    panel.classList.toggle("hidden", !active);
    panel.setAttribute("aria-hidden", String(!active));
  }
  if (push) syncTabURL(next);
  if (scroll && changed) resetScroll();
  if (!load) return;
  if (next === "cards") loadCards();
  if (next === "spreads") loadSpreads();
  if (next === "preview") loadPreviewForm();
}

async function refresh({ announce = false } = {}) {
  const seq = ++refreshSeq;
  const button = $("refresh");
  const label = button.querySelector("span");
  button.classList.add("is-loading");
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  if (label) label.textContent = "刷新中…";
  try {
    if (!$("panel-preview").classList.contains("hidden")) resetPreview();
    try {
      const overview = await apiGet("overview");
      if (seq !== refreshSeq) return;
      state.overview = overview;
      state.aiEnabled = Boolean(overview.settings && overview.settings.enable_ai);
      state.fetchedAt.overview = Date.now();
      $("version").textContent = overview.version ? `v${overview.version}` : "";
      renderStats(overview);
      renderFreshness();
      clearError("overview");
      updatePreviewAIButton();
    } catch (error) {
      if (seq === refreshSeq) {
        setError("overview", `概览加载失败：${error.message || "读取失败"}`,
                 () => refresh({ announce: true }));
      }
    }
    try {
      const data = await apiGet("daily/list");
      if (seq !== refreshSeq) return;
      state.items = data.items || [];
      state.listIndexedTotal = Number.isFinite(data.indexed_total)
        ? data.indexed_total : state.items.length;
      state.listShown = Number.isFinite(data.shown) ? data.shown : state.items.length;
      state.listTruncated = Boolean(data.truncated);
      state.fetchedAt.daily = Date.now();
      clearError("list");
      renderFreshness();
      const stillSelected = Boolean(state.selected)
        && state.items.some((item) => item.uid === state.selected);
      renderDailyList();
      if (seq !== refreshSeq) return;
      if (stillSelected) {
        await openDaily(state.selected, { preserve: true });
      } else if (state.items.length) {
        await openDaily(state.items[0].uid);
      } else {
        state.selected = null;
        state.detailUid = "";
        clearError("detail");
        renderDailyList();
        $("daily-detail").replaceChildren(
          el("p", "empty", "今天还没有牌运记录。在聊天里抽一签后这里会出现。"),
        );
      }
    } catch (error) {
      if (seq === refreshSeq) {
        setError("list", `牌运列表加载失败：${error.message || "读取失败"}`,
                 () => refresh({ announce: true }));
      }
    }
    if (seq === refreshSeq && state.spreads.length) renderSpreads();
    const retries = [];
    if (seq === refreshSeq && state.errors.cards) {
      retries.push(loadCards({ force: true }));
    }
    if (seq === refreshSeq && state.errors.spreads) {
      retries.push(loadSpreads({ force: true }));
    }
    if (seq === refreshSeq && state.errors.preview) {
      retries.push(loadPreviewForm({ force: true }));
    }
    if (retries.length) await Promise.all(retries);
    if (seq !== refreshSeq) return;
    if (announce) {
      const keys = Object.keys(state.errors);
      const failed = keys.filter((key) => state.errors[key]);
      if (!failed.length) showToast("已刷新");
      else if (failed.length === keys.length) showToast("刷新失败");
      else showToast("部分数据刷新失败");
    }
  } finally {
    if (seq === refreshSeq) {
      button.classList.remove("is-loading");
      button.disabled = false;
      button.setAttribute("aria-busy", "false");
      if (label) label.textContent = "刷新";
    }
  }
}

function renderConnectionEmpty() {
  $("stats").replaceChildren(el("p", "empty", "连接 AstrBot 后显示今日概览。"));
  $("daily-list").replaceChildren(el("p", "empty", "连接 AstrBot 后显示今日牌运。"));
  $("daily-detail").replaceChildren(el("p", "empty", "连接 AstrBot 后显示解读。"));
  $("card-grid").replaceChildren(el("p", "empty", "连接 AstrBot 后显示牌面。"));
  $("spread-grid").replaceChildren(el("p", "empty", "连接 AstrBot 后显示牌阵。"));
  $("preview-result").replaceChildren(el("p", "empty", "连接 AstrBot 后可预览抽牌。"));
}

function applyConnection(status) {
  const banner = $("connection-banner");
  const degraded = status.state === "degraded";
  banner.classList.toggle("hidden", !degraded);
  if (degraded) {
    $("connection-text").textContent = status.error
      ? `无法连接 AstrBot：${status.error}`
      : "无法连接 AstrBot。";
  }
  if (status.state === "demo") {
    $("subtitle").textContent = "预览模式 · 未连接 AstrBot";
    $("subtitle").classList.add("demo");
    return;
  }
  $("subtitle").textContent = SUBTITLE_DEFAULT;
  $("subtitle").classList.remove("demo");
  if (status.state === "connected" && status.title) {
    $("heading").textContent = status.title;
    document.title = status.title;
  }
}

function clearHostData() {
  refreshSeq += 1;
  detailSeq += 1;
  dataEpoch += 1;
  cardsRequest = null;
  spreadsRequest = null;
  closeLightbox();
  closeCardDetail();
  state.items = [];
  state.selected = null;
  state.overview = null;
  state.cards = [];
  state.spreads = [];
  state.detailUid = "";
  state.cardsLoaded = false;
  state.spreadsLoaded = false;
  state.listIndexedTotal = 0;
  state.listShown = 0;
  state.listTruncated = false;
  state.fetchedAt = {
    overview: 0, daily: 0, detail: 0, cards: 0, spreads: 0,
  };
  for (const key of Object.keys(state.errors)) clearError(key);
  resetPreview();
  const select = $("preview-spread");
  if (select) {
    select.dataset.ready = "";
    select.disabled = false;
    select.replaceChildren();
  }
  renderFreshness();
  const button = $("refresh");
  button.classList.remove("is-loading");
  button.disabled = false;
  button.setAttribute("aria-busy", "false");
  const label = button.querySelector("span");
  if (label) label.textContent = "刷新";
  renderConnectionEmpty();
}

function reloadVisiblePanel() {
  const active = document.querySelector(".tab.active");
  const tab = active ? active.dataset.tab : "daily";
  if (tab === "cards") loadCards();
  else if (tab === "spreads") loadSpreads();
  else if (tab === "preview") loadPreviewForm();
}

async function retryConnection() {
  const button = $("connection-retry");
  const label = button.textContent;
  button.disabled = true;
  button.textContent = "连接中…";
  const status = await connect();
  button.textContent = label;
  button.disabled = false;
  applyConnection(status);
  if (status.state === "degraded") clearHostData();
}

async function boot() {
  $("version").textContent = "";
  renderFreshness();
  if ("scrollRestoration" in history) history.scrollRestoration = "manual";
  initBackground();
  startBackground();
  normalizeTabURL();
  switchTab(tabFromURL(), { scroll: false, load: false });
  for (const button of document.querySelectorAll(".tab")) {
    button.addEventListener("click", () => switchTab(button.dataset.tab, { push: true }));
  }
  const tablist = document.querySelector(".tabs");
  tablist.addEventListener("keydown", (event) => {
    const tabs = [...tablist.querySelectorAll('[role="tab"]')];
    const index = tabs.indexOf(document.activeElement);
    if (index < 0) return;
    let target = null;
    if (event.key === "ArrowRight") target = (index + 1) % tabs.length;
    else if (event.key === "ArrowLeft") target = (index - 1 + tabs.length) % tabs.length;
    else if (event.key === "Home") target = 0;
    else if (event.key === "End") target = tabs.length - 1;
    if (target === null) return;
    event.preventDefault();
    const next = tabs[target];
    switchTab(next.dataset.tab, { push: true });
    next.focus();
  });
  window.addEventListener("popstate", () => {
    switchTab(tabFromURL());
    const active = document.querySelector(".tab.active");
    if (active) active.focus();
  });
  $("refresh").addEventListener("click", () => refresh({ announce: true }));
  $("lightbox-retry").addEventListener("click", (event) => {
    event.stopPropagation();
    if (lightboxTarget) openLightbox(lightboxTarget.card, lightboxTarget.upright);
  });
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
  $("connection-retry").addEventListener("click", retryConnection);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeLightbox();
      closeCardDetail();
    }
  });

  const status = await connect();
  applyConnection(status);
  if (status.state === "degraded") renderConnectionEmpty();

  let lastState = connectionState();
  onConnectionChange(async (next, error) => {
    if (next === "degraded") {
      applyConnection({ state: next, error });
      clearHostData();
      lastState = next;
      return;
    }
    applyConnection({
      state: next,
      error,
      title: next === "connected" ? connectionTitle() : "",
    });
    if (lastState !== "connected" && next === "connected") {
      reloadVisiblePanel();
      await refresh();
    }
    lastState = next;
  });

  if (status.state === "connected" || status.state === "demo") {
    reloadVisiblePanel();
    await refresh();
  }
  // context 可能在 connect 返回与订阅之间到达
  if (status.state !== "connected" && connectionState() === "connected") {
    applyConnection({ state: "connected", title: connectionTitle() });
    reloadVisiblePanel();
    await refresh();
  }
}

boot();
