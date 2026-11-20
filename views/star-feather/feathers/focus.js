// 弹窗焦点工具：只做焦点查询与 Tab 循环，不接管弹窗生命周期。

const FOCUSABLE = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
].join(",");

export function focusables(root) {
  return [...root.querySelectorAll(FOCUSABLE)].filter((node) => {
    if (node.getAttribute("aria-hidden") === "true") return false;
    return node.getClientRects().length > 0;
  });
}

export function trap(root) {
  const onKey = (event) => {
    if (event.key !== "Tab") return;
    const items = focusables(root);
    if (!items.length) {
      event.preventDefault();
      return;
    }
    const first = items[0];
    const last = items[items.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };
  root.addEventListener("keydown", onKey);
  return () => root.removeEventListener("keydown", onKey);
}
