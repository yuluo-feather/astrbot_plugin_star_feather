// 页面与背景共用的 DOM 工具。
// showToast 只给页面交互层用；背景模块按需导入 $，不需要就不导。
export const $ = (id) => document.getElementById(id);

export function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

let toastTimer = 0;
export function showToast(message) {
  const toast = $("toast");
  toast.textContent = message || "";
  toast.classList.toggle("show", Boolean(message));
  // 只留一个定时器：否则前一条提示到点会把后一条一起关掉
  clearTimeout(toastTimer);
  toastTimer = 0;
  if (message) {
    toastTimer = setTimeout(() => {
      toast.classList.remove("show");
      toastTimer = 0;
    }, 4200);
  }
}

