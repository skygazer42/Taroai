import { mkdir, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const iconNames = [
  "activity",
  "app-window",
  "arrow-left",
  "arrow-right",
  "arrow-up",
  "audio-lines",
  "blocks",
  "bot",
  "brain-circuit",
  "check",
  "chevron-down",
  "chevron-right",
  "circle-alert",
  "circle-user-round",
  "clock",
  "code-xml",
  "compass",
  "copy",
  "database",
  "download",
  "ellipsis",
  "external-link",
  "eye",
  "file",
  "file-code",
  "file-search",
  "folder",
  "globe",
  "grid-2x2",
  "heart",
  "image",
  "info",
  "languages",
  "list",
  "lock",
  "log-out",
  "menu",
  "mic",
  "newspaper",
  "panel-left-close",
  "panel-left-open",
  "paperclip",
  "pin",
  "play",
  "plug",
  "plus",
  "presentation",
  "refresh-cw",
  "rotate-cw",
  "search",
  "settings",
  "share-2",
  "sparkles",
  "square-stop",
  "square-terminal",
  "terminal",
  "thumbs-down",
  "thumbs-up",
  "trash-2",
  "triangle-alert",
  "upload",
  "video",
  "wand-sparkles",
  "workflow",
  "wrench",
  "x",
  "zap",
];

const escapeAttribute = (value) => String(value)
  .replaceAll("&", "&amp;")
  .replaceAll('"', "&quot;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;");

const symbols = [];
for (const name of iconNames) {
  const { default: icon } = await import(`lucide/dist/esm/icons/${name}.mjs`);
  const children = icon.map(([tag, attributes]) => {
    const serialized = Object.entries(attributes)
      .map(([key, value]) => `${key}="${escapeAttribute(value)}"`)
      .join(" ");
    return `<${tag} ${serialized}/>`;
  }).join("");
  symbols.push(`<symbol id="lucide-${name}" viewBox="0 0 24 24">${children}</symbol>`);
}

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const output = resolve(root, "assets/lucide-sprite.svg");
await mkdir(dirname(output), { recursive: true });
await writeFile(
  output,
  `<!-- Generated from lucide@1.26.0. ISC License: https://lucide.dev/license -->\n<svg xmlns="http://www.w3.org/2000/svg"><defs>${symbols.join("")}</defs></svg>\n`,
);
