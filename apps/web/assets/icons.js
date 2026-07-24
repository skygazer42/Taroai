const spriteUrl = new URL("./lucide-sprite.svg", import.meta.url).href;

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

export function icon(name, { className = "ui-icon", label = "" } = {}) {
  const accessibility = label
    ? `role="img" aria-label="${escapeHtml(label)}"`
    : 'aria-hidden="true"';
  return `<svg class="${escapeHtml(className)} lucide-icon" ${accessibility}><use href="${spriteUrl}#lucide-${escapeHtml(name)}"></use></svg>`;
}

export function iconElement(name, options) {
  const template = document.createElement("template");
  template.innerHTML = icon(name, options);
  return template.content.firstElementChild;
}

export function setIcon(target, name, options) {
  target?.replaceChildren(iconElement(name, options));
}

export function hydrateIcons(root = document) {
  for (const use of root.querySelectorAll('use[href*="lucide-sprite.svg#"]')) {
    const name = use.getAttribute("href")?.split("#")[1];
    if (name) use.setAttribute("href", `${spriteUrl}#${name}`);
  }
}
