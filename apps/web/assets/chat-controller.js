import { chatApi } from "./chat-api.js?v=20260722-flow115";
import { icon, iconElement, setIcon } from "./icons.js?v=20260724-icons2";
import {
  filterMentionCandidates,
  insertMention,
  mentionQuery,
  normalizeCapabilities,
  resourceReference,
} from "./mentions.js?v=20260722-flow115";

export const chatState = {
  currentThreadId: null,
  currentRunId: null,
  currentRunMode: "chat",
  lastThreadSequence: 0,
  threads: [],
  messages: [],
  queue: [],
  events: [],
  artifacts: [],
  commandOutputs: new Map(),
  disclosureOpen: new Map(),
  codingWorkspace: null,
  activeCodingChange: null,
  modelCatalog: [],
  selectedModel: null,
  capabilities: [],
  creationCapabilities: {
    image: false,
    video: false,
    voice: false,
    browser: false,
    workflow: false,
    slides: false,
  },
  resourceRefs: [],
  browserProfile: null,
  createIntent: null,
  uploads: [],
  thread: null,
  running: false,
  loading: false,
  streamAbort: null,
  streamRetry: null,
  reconnectAttempt: 0,
  mentionContext: null,
  activeSidecar: "artifacts",
  share: null,
  suggestions: [],
  promotingManual: false,
  inputRequest: null,
  inputAnswers: {},
  inputExtra: "",
  feedbackByMessage: new Map(),
  enteringMessageId: null,
  inputRequestEntering: false,
  suggestionsEntering: false,
};

const AGENT_RUN_HANDOFF_KEY = "taroai.agentRunHandoff";

export function queueAgentRunHandoff(payload) {
  sessionStorage.setItem(AGENT_RUN_HANDOFF_KEY, JSON.stringify(payload));
}

function takeAgentRunHandoff() {
  const raw = sessionStorage.getItem(AGENT_RUN_HANDOFF_KEY);
  if (!raw) return null;
  sessionStorage.removeItem(AGENT_RUN_HANDOFF_KEY);
  try {
    const payload = JSON.parse(raw);
    return payload && typeof payload === "object" ? payload : null;
  } catch {
    return null;
  }
}

const ACTIVE_RUN_STATES = new Set([
  "created",
  "queued",
  "classifying",
  "retrieving_context",
  "planning",
  "running",
  "awaiting_approval",
  "retrying",
]);

const TERMINAL_EVENT_WORDS = ["completed", "succeeded", "failed", "cancelled", "stopped", "timed_out"];

const CREATE_INTENTS = {
  agent: {
    label: "Agent",
    chipLabel: "Mode",
    prefix: "The user explicitly chose Agent mode. Use the available tools when needed and complete the task end to end.\n\n",
  },
  image: {
    label: "Image",
    prefix: "The user explicitly chose image generation in the composer. Use your image generation tool to fulfill this request.\n\n",
  },
  video: {
    label: "Video",
    prefix: "The user explicitly chose video generation in the composer. Use your video generation tool to fulfill this request.\n\n",
  },
  voice: {
    label: "Voice",
    prefix: "The user explicitly chose voice generation in the composer. Use your text-to-speech tool to fulfill this request.\n\n",
  },
  workflow: {
    label: "Workflow",
    prefix: "The user explicitly chose a multi-step workflow in the composer. Plan clear phases, inputs, parallel work where useful, and verification before producing the final result.\n\n",
  },
  slides: {
    label: "Slide deck",
    prefix: "The user explicitly chose slide deck creation in the composer. Create a polished slide deck artifact that fulfills this request.\n\n",
  },
};

function query(selector, root = document) {
  return root.querySelector(selector);
}

function queryAll(selector, root = document) {
  return Array.from(root.querySelectorAll(selector));
}

function arrayFrom(payload, ...keys) {
  if (Array.isArray(payload)) return payload;
  for (const key of keys) {
    if (Array.isArray(payload?.[key])) return payload[key];
    if (Array.isArray(payload?.[key]?.items)) return payload[key].items;
  }
  if (Array.isArray(payload?.items)) return payload.items;
  return [];
}

function isDisplayableArtifact(artifact) {
  if (artifact.name !== "agent-result.md") return true;
  return Boolean(artifact.storage_object_id || Object.keys(artifact.preview_payload || {}).length);
}

function text(value, fallback = "") {
  if (value === null || value === undefined) return fallback;
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function safeCommandStream(value, limit = 8000) {
  if (typeof value !== "string") return "";
  if (value.length <= limit) return value;
  const half = Math.floor(limit / 2);
  return `${value.slice(0, half)}\n… output truncated …\n${value.slice(-half)}`;
}

function safeToolInput(value, tool = "") {
  if (value === null || value === undefined) return "";
  if (typeof value !== "object") return "[Input provided]";
  try {
    return JSON.stringify(value, (key, item) => (
      /password|passwd|secret|token|api[-_]?key|private[-_]?key|access[-_]?key|authorization|bearer|cookie|credential/i.test(key)
      || (tool === "browser.action" && /^(text|value)$/i.test(key))
        ? "••••••••"
        : item
    ), 2);
  } catch {
    return "[Input details unavailable]";
  }
}

function toolLabel(tool) {
  if (tool === "web.search") return "Web search";
  if (tool === "web.fetch") return "Web page";
  if (tool === "sandbox.command") return "Code execution";
  if (tool === "browser.action") return "Browser action";
  if (tool === "tool.search") return "Tool search";
  if (tool === "ui.render") return "Structured result";
  if (tool === "memory.save") return "Memory";
  if (tool === "skill.load" || tool.startsWith("skill.")) return "Skill";
  if (tool.startsWith("mcp.")) return "MCP tool";
  if (tool.startsWith("connector.")) return "Connected tool";
  return tool;
}

function toolIcon(tool) {
  if (tool === "web.search") return "search";
  if (tool === "web.fetch") return "globe";
  if (tool === "sandbox.command") return "terminal";
  if (tool === "browser.action") return "compass";
  if (tool === "tool.search") return "search";
  if (tool === "ui.render") return "app-window";
  if (tool === "memory.save") return "brain-circuit";
  if (tool === "skill.load" || tool.startsWith("skill.")) return "blocks";
  if (tool.startsWith("mcp.") || tool.startsWith("connector.")) return "plug";
  return "wrench";
}

function setText(element, value) {
  if (element) element.textContent = value;
}

function threadIdFromHash() {
  const match = window.location.hash.match(/^#chat\/([^/?#]+)/i);
  return match
    ? decodeURIComponent(match[1])
    : new URLSearchParams(window.location.search).get("threadId");
}

function updateThreadHash(threadId, replace = false) {
  const url = new URL(window.location.href);
  url.searchParams.delete("threadId");
  url.hash = threadId ? `#chat/${encodeURIComponent(threadId)}` : "#chat";
  if (url.href === window.location.href) return;
  if (replace) history.replaceState({}, "", url);
  else history.pushState({}, "", url);
  window.dispatchEvent(new CustomEvent("taroai:route-changed"));
}

function publishChatContext() {
  window.dispatchEvent(new CustomEvent("taroai:chat-context-changed", {
    detail: {
      threadId: chatState.currentThreadId,
      runId: chatState.currentRunId,
    },
  }));
}

function modelKey(model) {
  return `${model.provider_id || model.provider || "provider"}:${model.model_id || model.id || model.name}`;
}

const PROVIDER_LABELS = {
  anthropic: "Anthropic",
  openai: "OpenAI",
  google: "Google",
  gemini: "Google",
  meta: "Meta",
  deepseek: "DeepSeek",
  zhipu: "Zhipu AI",
  zai: "Z.AI",
  "z-ai": "Z.AI",
  minimax: "MiniMax",
  moonshot: "Moonshot AI",
  mistral: "Mistral",
  xai: "xAI",
  sakana: "Sakana",
};

const MODEL_PRESENTATION = {
  "claude-sonnet-5": { name: "Claude Sonnet 5", description: "Everyday coding, agents, and professional work at scale.", isNew: true },
  "claude-opus-4-8": { name: "Claude Opus 4.8", description: "Deep reasoning for the hardest problems." },
  "claude-haiku-4-5": { name: "Claude Haiku 4.5", description: "Quick replies and light tasks." },
  "gpt-5.6": { name: "GPT-5.6", description: "Rigorous coding and step-by-step reasoning.", isNew: true },
  "gpt-5.5": { name: "GPT-5.5", description: "Rigorous coding and step-by-step reasoning." },
  "deepseek-v4-flash": { name: "DeepSeek V4 Flash", description: "Fast, high-volume agentic work at low cost.", isNew: true },
  "glm-4.7": { name: "GLM 4.7", description: "Agentic reasoning, coding, and tool use.", isNew: true },
  "glm-4.5-flash": { name: "GLM 4.5 Flash", description: "Fast everyday chat and tool use.", isNew: true },
  "glm-5.2": { name: "GLM 5.2", description: "Long agentic coding on a budget.", isNew: true },
  "glm-5": { name: "GLM 5", description: "Reliable general assistant work." },
  "minimax-m2.1": { name: "MiniMax M2.1", description: "Fast multimodal agents at a low price.", isNew: true },
  "kimi-k2.5": { name: "Kimi K2.5", description: "Agentic reasoning with a huge context window.", isNew: true },
  "grok-4.1": { name: "Grok 4.1", description: "Frontier reasoning with real-time knowledge." },
  "grok-4.1-fast": { name: "Grok 4.1 Fast", description: "Snappy responses for everyday tasks.", isNew: true },
  "grok-4.5": { name: "Grok 4.5", description: "Cost-efficient development and agent work.", isNew: true },
};

function providerLabel(providerId) {
  const key = String(providerId || "").toLowerCase();
  const known = Object.keys(PROVIDER_LABELS).find((id) => key === id || key.startsWith(`${id}-`));
  return PROVIDER_LABELS[known] || `${key.slice(0, 1).toUpperCase()}${key.slice(1)}` || "Models";
}

function displayProvider(model) {
  return model.provider_id === "default" && String(model.model_id).toLowerCase().startsWith("grok-")
    ? "xai"
    : model.provider_id;
}

function normalizedModel(model, providerFallback = "") {
  const providerId = model.provider_id || model.provider || providerFallback || "default";
  const modelId = model.model_id || model.id || model.slug || model.name;
  const efforts = model.reasoning_efforts || model.efforts || model.supported_efforts || [];
  const reasoningEfforts = Array.isArray(efforts) ? efforts : [];
  const presentation = MODEL_PRESENTATION[modelId] || null;
  return {
    ...model,
    provider_id: providerId,
    model_id: modelId,
    display_name: presentation?.name || model.display_name || model.label || model.name || modelId,
    description: presentation?.description || model.description || model.summary || "Available for this workspace",
    is_new: Boolean(presentation?.isNew || model.is_new),
    configured: model.configured !== false,
    reasoning_efforts: reasoningEfforts,
    reasoning_effort: model.reasoning_effort || model.default_reasoning_effort || reasoningEfforts[0] || null,
    enabled: model.enabled !== false && model.allowed !== false,
  };
}

function currentWorkspaceId() {
  return chatApi.settings().workspaceId;
}

function eventType(event) {
  const type = String(event.type || event.event_type || event.name || event.event || "event").toLowerCase();
  return type === "workflow_completed" ? "workflow.completed" : type;
}

function eventPayload(event) {
  return event.payload || event.data || event.detail || {};
}

function eventSequence(event) {
  return Number(event.thread_sequence || event.sequence || event.id || 0);
}

const COMMAND_ACTIVITY_COPY = {
  read_file: { started: "Reading file", completed: "Read file", noun: "File read" },
  list_files: { started: "Listing files", completed: "Listed files", noun: "File listing" },
  search_files: { started: "Searching files", completed: "Found files", noun: "File search" },
  run_command: { started: "Running command", completed: "Ran command", noun: "Command" },
};

function commandActivity(payload = {}) {
  return COMMAND_ACTIVITY_COPY[payload.command_kind] || COMMAND_ACTIVITY_COPY.run_command;
}

function commandSubject(command, kind = "run_command") {
  const value = String(command || "").trim().replace(/\s+/g, " ");
  if (kind !== "run_command") return value.length > 180 ? `${value.slice(0, 180)}…` : value;
  const tokens = value.match(/"[^"]*"|'[^']*'|\S+/g)?.map((item) => item.replace(/^(['"])(.*)\1$/, "$2")) || [];
  if (!tokens.length) return "";
  const executable = tokens[0].split(/[\\/]/).at(-1);
  let target = executable;
  if (/^(python\d*|node|bash|sh|zsh)$/.test(executable)) {
    target = tokens.slice(1).find((item) => /\.(py|m?js|cjs|sh)$/i.test(item)) || executable;
  }
  return target.split(/[\\/]/).filter(Boolean).at(-1)?.slice(0, 80) || executable;
}

function toolActivityKey(event) {
  const payload = eventPayload(event);
  return payload.action_id || payload.step_id || payload.call_id || null;
}

function bindDisclosure(details, key, defaultOpen = false) {
  details.dataset.disclosureKey = key;
  details.open = chatState.disclosureOpen.has(key)
    ? chatState.disclosureOpen.get(key)
    : defaultOpen;
  details.addEventListener("toggle", () => {
    chatState.disclosureOpen.set(key, details.open);
  });
}

function dispatchStatus(message) {
  return String(message.dispatch_status || message.status || "completed").toLowerCase();
}

function messageContent(message) {
  return message.content || message.message || message.text || "";
}

function visibleMessageContent(message) {
  const content = String(messageContent(message));
  if (isAssistant(message)) return content;
  const intent = Object.values(CREATE_INTENTS).find((item) => content.startsWith(item.prefix));
  return intent ? content.slice(intent.prefix.length) : content;
}

function appendInlineMarkup(parent, value) {
  const source = String(value);
  const pattern = /(\*\*([^*\n]+)\*\*|`([^`\n]+)`|\[([^\]\n]+)\]\((https?:\/\/[^)\s]+)\)|(https?:\/\/[^\s<>()\[\]]*[^\s<>()\[\].,!?;:'"。，！？；：]))/g;
  let cursor = 0;
  for (const match of source.matchAll(pattern)) {
    if (match.index > cursor) parent.append(source.slice(cursor, match.index));
    if (match[2]) {
      const strong = document.createElement("strong");
      strong.textContent = match[2];
      parent.append(strong);
    } else if (match[3]) {
      const code = document.createElement("code");
      code.textContent = match[3];
      parent.append(code);
    } else {
      const link = document.createElement("a");
      link.href = match[5] || match[6];
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = match[4] || match[6];
      parent.append(link);
    }
    cursor = match.index + match[0].length;
  }
  if (cursor < source.length) parent.append(source.slice(cursor));
}

const MATH_NS = "http://www.w3.org/1998/Math/MathML";
const MATH_SYMBOLS = {
  cdot: "·", times: "×", div: "÷", pm: "±", le: "≤", leq: "≤",
  ge: "≥", geq: "≥", neq: "≠", approx: "≈", infty: "∞", sum: "∑",
  prod: "∏", int: "∫", alpha: "α", beta: "β", gamma: "γ", delta: "δ",
  theta: "θ", lambda: "λ", mu: "μ", pi: "π", sigma: "σ", phi: "φ", omega: "ω",
};

function mathElement(name, textValue = null) {
  const node = document.createElementNS(MATH_NS, name);
  if (textValue !== null) node.textContent = textValue;
  return node;
}

function appendMath(parent, value, display = false) {
  const source = String(value).trim().slice(0, 2000);
  let cursor = 0;
  let depth = 0;
  const sequence = (grouped = false) => {
    const row = mathElement("mrow");
    while (cursor < source.length && !(grouped && source[cursor] === "}")) {
      if (/\s/.test(source[cursor])) {
        cursor += 1;
        continue;
      }
      let base = atom();
      let sub = null;
      let sup = null;
      while (source[cursor] === "_" || source[cursor] === "^") {
        const marker = source[cursor++];
        if (marker === "_") sub = group();
        else sup = group();
      }
      if (sub || sup) {
        const script = mathElement(sub && sup ? "msubsup" : sub ? "msub" : "msup");
        script.append(base);
        if (sub) script.append(sub);
        if (sup) script.append(sup);
        base = script;
      }
      row.append(base);
    }
    return row;
  };
  const group = () => {
    if (depth >= 20) {
      cursor += 1;
      return mathElement("mtext", "");
    }
    if (source[cursor] !== "{") return atom();
    cursor += 1;
    depth += 1;
    const row = sequence(true);
    depth -= 1;
    if (source[cursor] === "}") cursor += 1;
    return row;
  };
  const atom = () => {
    const start = cursor;
    const char = source[cursor++];
    if (char === "{") {
      cursor = start;
      return group();
    }
    if (char === "\\") {
      const command = source.slice(cursor).match(/^[A-Za-z]+/)?.[0] || source[cursor++] || "";
      cursor += /^[A-Za-z]+/.test(command) ? command.length : 0;
      if (command === "frac") {
        const fraction = mathElement("mfrac");
        fraction.append(group(), group());
        return fraction;
      }
      if (command === "sqrt") {
        const root = mathElement("msqrt");
        root.append(group());
        return root;
      }
      if (command === "text" || command === "mathrm") {
        const content = group();
        return mathElement("mtext", content.textContent);
      }
      if (command === "left" || command === "right") return atom();
      const symbol = MATH_SYMBOLS[command];
      return mathElement(/[A-Za-zͰ-Ͽ]/.test(symbol || "") ? "mi" : "mo", symbol || command);
    }
    if (/\d/.test(char)) {
      const tail = source.slice(cursor).match(/^[\d.]*/)?.[0] || "";
      cursor += tail.length;
      return mathElement("mn", char + tail);
    }
    if (/[A-Za-z]/.test(char)) return mathElement("mi", char);
    return mathElement(/[+\-=(),\[\]|<>]/.test(char) ? "mo" : "mtext", char || "");
  };
  const math = mathElement("math");
  math.classList.add("chat-math");
  if (display) math.setAttribute("display", "block");
  math.setAttribute("aria-label", source);
  math.append(sequence());
  parent.append(math);
}

function appendInlineText(parent, value) {
  const source = String(value);
  const pattern = /(\\\((.+?)\\\)|\$(?!\s)([^$\n]*?\S)\$)/g;
  let cursor = 0;
  for (const match of source.matchAll(pattern)) {
    if (match.index > cursor) appendInlineMarkup(parent, source.slice(cursor, match.index));
    appendMath(parent, match[2] || match[3]);
    cursor = match.index + match[0].length;
  }
  if (cursor < source.length) appendInlineMarkup(parent, source.slice(cursor));
}

function appendCodeBlock(parent, source, language = "") {
  const block = document.createElement("section");
  block.className = "message-code";
  const header = document.createElement("header");
  const label = document.createElement("span");
  label.textContent = language || "code";
  const copy = document.createElement("button");
  copy.type = "button";
  copy.textContent = "Copy";
  copy.setAttribute("aria-label", "Copy code");
  copy.addEventListener("click", async () => {
    await navigator.clipboard?.writeText(source);
    copy.textContent = "Copied";
    window.setTimeout(() => { copy.textContent = "Copy"; }, 1200);
  });
  header.append(label, copy);
  const pre = document.createElement("pre");
  const code = document.createElement("code");
  if (language) code.className = `language-${language.replace(/[^a-z0-9_-]/gi, "")}`;
  code.textContent = source;
  pre.append(code);
  block.append(header, pre);
  parent.append(block);
}

function tableCells(line) {
  return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim());
}

function isTableDivider(line) {
  const cells = tableCells(line);
  return cells.length > 1 && cells.every((cell) => /^:?-{3,}:?$/.test(cell));
}

function appendMarkdown(parent, value) {
  const lines = String(value).replaceAll("\r\n", "\n").split("\n");
  for (let index = 0; index < lines.length;) {
    const line = lines[index];
    if (!line.trim()) {
      index += 1;
      continue;
    }
    const singleLineMath = line.match(/^\s*(?:\$\$(.+)\$\$|\\\[(.+)\\\])\s*$/);
    if (singleLineMath) {
      const block = document.createElement("div");
      block.className = "chat-math-block";
      appendMath(block, singleLineMath[1] || singleLineMath[2], true);
      parent.append(block);
      index += 1;
      continue;
    }
    if (["$$", "\\["].includes(line.trim())) {
      const close = line.trim() === "$$" ? "$$" : "\\]";
      const formula = [];
      index += 1;
      while (index < lines.length && lines[index].trim() !== close) formula.push(lines[index++]);
      if (index < lines.length) index += 1;
      const block = document.createElement("div");
      block.className = "chat-math-block";
      appendMath(block, formula.join(" "), true);
      parent.append(block);
      continue;
    }
    const fence = line.match(/^```([\w.+-]*)\s*$/);
    if (fence) {
      const code = [];
      index += 1;
      while (index < lines.length && !/^```\s*$/.test(lines[index])) code.push(lines[index++]);
      if (index < lines.length) index += 1;
      appendCodeBlock(parent, code.join("\n"), fence[1]);
      continue;
    }
    const heading = line.match(/^(#{1,4})\s+(.+)$/);
    if (heading) {
      const element = document.createElement(heading[1].length < 3 ? "h2" : "h3");
      appendInlineText(element, heading[2]);
      parent.append(element);
      index += 1;
      continue;
    }
    if (index + 1 < lines.length && line.includes("|") && isTableDivider(lines[index + 1])) {
      const wrapper = document.createElement("div");
      wrapper.className = "message-table-wrap";
      const table = document.createElement("table");
      const head = document.createElement("thead");
      const headRow = document.createElement("tr");
      for (const cell of tableCells(line)) {
        const th = document.createElement("th");
        appendInlineText(th, cell);
        headRow.append(th);
      }
      head.append(headRow);
      table.append(head);
      index += 2;
      const body = document.createElement("tbody");
      while (index < lines.length && lines[index].includes("|") && lines[index].trim()) {
        const row = document.createElement("tr");
        for (const cell of tableCells(lines[index])) {
          const td = document.createElement("td");
          appendInlineText(td, cell);
          row.append(td);
        }
        body.append(row);
        index += 1;
      }
      table.append(body);
      wrapper.append(table);
      parent.append(wrapper);
      continue;
    }
    const listMatch = line.match(/^\s*(?:([-*])|(\d+)\.)\s+(.+)$/);
    if (listMatch) {
      const ordered = Boolean(listMatch[2]);
      const list = document.createElement(ordered ? "ol" : "ul");
      if (ordered) list.start = Number(listMatch[2]);
      while (index < lines.length) {
        const itemMatch = lines[index].match(/^\s*(?:([-*])|(\d+)\.)\s+(.+)$/);
        if (!itemMatch || Boolean(itemMatch[2]) !== ordered) break;
        const item = document.createElement("li");
        appendInlineText(item, itemMatch[3]);
        list.append(item);
        index += 1;
      }
      parent.append(list);
      continue;
    }
    const paragraph = document.createElement("p");
    appendInlineText(paragraph, line);
    index += 1;
    while (index < lines.length && lines[index].trim() && !/^```/.test(lines[index]) && !/^(#{1,4})\s+/.test(lines[index])) {
      if (lines[index].match(/^\s*(?:[-*]|\d+\.)\s+/)) break;
      paragraph.append(document.createElement("br"));
      appendInlineText(paragraph, lines[index]);
      index += 1;
    }
    parent.append(paragraph);
  }
}

function isAssistant(message) {
  return ["assistant", "agent", "system"].includes(String(message.role || message.kind || "").toLowerCase());
}

function assistantResponseReady() {
  const message = chatState.messages.at(-1);
  return isAssistant(message || {}) && dispatchStatus(message) === "completed";
}

function runSubject() {
  if (chatState.currentRunMode === "chat") return "Response";
  if (chatState.currentRunMode === "workflow") return "Workflow";
  return "Agent";
}

function workingStatus(live = false) {
  const message = chatState.currentRunMode === "chat"
    ? "Response is streaming"
    : `${runSubject()} is working`;
  return live ? `Live · ${message.toLowerCase()}` : message;
}

function escapeFilename(value) {
  return String(value || "artifact").replace(/[\\/:*?"<>|]+/g, "-");
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

export class ChatController {
  constructor(api = chatApi) {
    this.api = api;
    this.refs = {};
    this.initialized = false;
    this.boundClick = (event) => {
      Promise.resolve(this.onClick(event)).catch((error) => {
        this.network(error?.message || "The action could not be completed", "error");
      });
    };
    this.boundInput = (event) => this.onInput(event);
    this.boundKeydown = (event) => this.onKeydown(event);
    this.boundChange = (event) => this.onChange(event);
    this.boundHash = () => this.restoreFromHash();
    this.boundAuth = (event) => this.onAuthChanged(event);
    this.boundWindowMessage = (event) => this.onWindowMessage(event);
    this.boundDragOver = (event) => this.onDragOver(event);
    this.boundDrop = (event) => this.onDrop(event);
    this.conversationFrame = null;
  }

  captureRefs() {
    this.refs = {
      shell: query("[data-app='taroai-workspace']"),
      conversation: query("[data-thread-conversation]"),
      emptyState: query("[data-testid='chat-empty-state']"),
      input: query("#composer-input"),
      send: query("#send-button"),
      stop: query("[data-thread-stop]"),
      newChat: query("[data-new-chat]"),
      threadList: query("[data-thread-list]"),
      threadSearch: query("[data-thread-search]"),
      threadStatus: query("[data-thread-status]"),
      threadPresence: query("[data-thread-presence]"),
      modelButton: query("#model-selector-button"),
      modelMenu: query("#model-selector-menu"),
      selectedModel: query("[data-selected-model]"),
      selectedModelGlyph: query("[data-selected-model-glyph]"),
      fileInput: query("#composer-file-input"),
      dropzone: query("[data-chat-dropzone]"),
      uploadList: query("[data-upload-list]"),
      resourceChips: query("[data-resource-chips]"),
      mentionMenu: query("[data-mention-menu]"),
      mentionResults: query("[data-mention-results]"),
      networkState: query("[data-chat-network-state]"),
      queueCount: query("[data-queue-count]"),
      sidecarQueueCount: query("[data-sidecar-queue-count]"),
      queue: query("[data-message-queue]"),
      sidecar: query("[data-workspace-sidecar]"),
      sidecarState: query("[data-sidecar-state]"),
      chatSidecar: query("[data-chat-sidecar]"),
      artifactEmpty: query("[data-thread-artifacts-empty]"),
      sidecarTitle: query("#artifact-panel-title"),
      artifactList: query("[data-thread-artifacts]"),
      artifactStage: query("[data-artifact-stage]"),
      artifactStageTitle: query("[data-artifact-stage-title]"),
      artifactStageContent: query("[data-artifact-stage-content]"),
      codingEmpty: query("[data-coding-empty]"),
      codingRoot: query("[data-coding-workspace]"),
      codingChanges: query("[data-coding-changes]"),
      codingDiff: query("[data-coding-diff]"),
      codingTests: query("[data-coding-tests]"),
      codingCheckpoints: query("[data-coding-checkpoints]"),
      codingDeliveries: query("[data-coding-deliveries]"),
      detailId: query("[data-thread-detail-id]"),
      detailModel: query("[data-thread-detail-model]"),
      detailRun: query("[data-thread-detail-run]"),
      detailStream: query("[data-thread-detail-stream]"),
      actionsMenu: query("[data-thread-actions-menu]"),
      moreButton: query("[data-thread-more]"),
      shareButton: query("[data-thread-share]"),
      createAgentButtons: queryAll("[data-thread-create-agent]"),
    };
  }

  async init() {
    if (this.initialized) return;
    this.initialized = true;
    this.captureRefs();
    document.addEventListener("click", this.boundClick, true);
    document.addEventListener("input", this.boundInput, true);
    document.addEventListener("keydown", this.boundKeydown, true);
    document.addEventListener("change", this.boundChange, true);
    window.addEventListener("hashchange", this.boundHash);
    window.addEventListener("taroai:auth-changed", this.boundAuth);
    window.addEventListener("message", this.boundWindowMessage);
    this.refs.dropzone?.addEventListener("dragover", this.boundDragOver);
    this.refs.dropzone?.addEventListener("drop", this.boundDrop);
    this.refs.dropzone?.addEventListener("dragleave", () => this.refs.dropzone?.classList.remove("is-dragging"));
    this.restoreDraft();
    this.renderAll();
    if (this.api.settings().accessToken) {
      await Promise.allSettled([this.loadModelCatalog(), this.loadThreads(), this.loadCapabilities()]);
      await this.restoreFromHash();
    } else {
      this.renderThreadListNotice("Sign in to load threads", "Your conversations will appear here.");
      this.network("Sign in to continue", "idle");
    }
  }

  stopOwnedEvent(event) {
    event.preventDefault();
    event.stopPropagation();
    event.stopImmediatePropagation();
  }

  ownedTarget(target) {
    return target?.closest?.(
      "[data-new-chat], #send-button, #model-selector-button, [data-chat-model], [data-model-effort], " +
        "[data-run-history-refresh], [data-thread-refresh], [data-thread-id], #composer-add-button, [data-add-command], " +
        "[data-open-queue], [data-open-artifacts], [data-open-code], [data-sidecar-tab], [data-queue-action], [data-queue-dispatch], " +
        "[data-thread-stop], [data-thread-share], [data-thread-more], [data-thread-rename], [data-thread-pin], " +
        "[data-thread-archive], [data-thread-delete], [data-remove-upload], [data-remove-resource], [data-mention-id], " +
        "[data-remove-create-intent], [data-remove-browser-profile], [data-browser-profile-id], [data-browser-profile-none], " +
        "[data-browser-profile-create-default], [data-browser-profile-new], [data-voice-input], " +
        "[data-thread-create-agent], [data-thread-artifact], [data-artifact-copy], [data-artifact-download], " +
        "[data-coding-tab], [data-coding-change], [data-coding-action], " +
        "[data-message-copy], [data-message-retry], [data-message-speak], [data-message-summarize], " +
        "[data-run-retry], [data-run-continue], [data-ui-submit], [data-ui-action], " +
        "[data-message-feedback], [data-message-more], [data-input-option], [data-input-submit], [data-suggestion]",
    );
  }

  onClick(event) {
    const control = this.ownedTarget(event.target);
    if (!control) {
      if (!event.target?.closest?.("#model-selector-menu, #model-selector-button")) this.closeModelMenu();
      if (!event.target?.closest?.("[data-thread-actions-menu], [data-thread-more]")) this.closeThreadMenu();
      if (!event.target?.closest?.("#composer-add-menu, #composer-add-button")) this.closeAddMenu();
      return;
    }
    this.stopOwnedEvent(event);

    if (control.matches("[data-new-chat]")) return this.startNewChat();
    if (control.matches("#send-button")) return this.sendThreadMessage();
    if (control.matches("#model-selector-button")) return this.toggleModelMenu();
    if (control.matches("[data-chat-model]")) return this.selectModel(control.dataset.chatModel);
    if (control.matches("[data-model-effort]")) return this.selectModelEffort(control.dataset.modelEffort, control.dataset.modelKey);
    if (control.matches("[data-run-history-refresh]")) return this.loadThreads();
    if (control.matches("[data-thread-refresh]")) return this.loadThreads();
    if (control.matches("[data-thread-id]")) return this.loadThread(control.dataset.threadId, true);
    if (control.matches("#composer-add-button")) return this.toggleAddMenu();
    if (control.matches("[data-add-command]")) return this.handleAddCommand(control.dataset.addCommand);
    if (control.matches("[data-open-queue]")) return this.openSidecar("queue");
    if (control.matches("[data-open-artifacts]")) return this.openSidecar("artifacts");
    if (control.matches("[data-open-code]")) return this.openSidecar("code");
    if (control.matches("[data-sidecar-tab]")) return this.openSidecar(control.dataset.sidecarTab);
    if (control.matches("[data-queue-action]")) return this.handleQueueAction(control);
    if (control.matches("[data-queue-dispatch]")) return this.dispatchQueue();
    if (control.matches("[data-thread-stop]")) return this.stopThread();
    if (control.matches("[data-thread-share]")) return this.shareThread();
    if (control.matches("[data-thread-more]")) return this.toggleThreadMenu();
    if (control.matches("[data-thread-rename]")) return this.renameCurrentThread();
    if (control.matches("[data-thread-pin]")) return this.pinCurrentThread();
    if (control.matches("[data-thread-archive]")) return this.archiveCurrentThread();
    if (control.matches("[data-thread-delete]")) return this.deleteCurrentThread();
    if (control.matches("[data-remove-upload]")) return this.removeUpload(control.dataset.removeUpload);
    if (control.matches("[data-remove-resource]")) return this.removeResource(control.dataset.removeResource);
    if (control.matches("[data-remove-create-intent]")) return this.selectCreateIntent(null);
    if (control.matches("[data-remove-browser-profile]")) return this.selectBrowserProfile(null);
    if (control.matches("[data-browser-profile-id]")) return this.selectBrowserProfile(control.dataset.browserProfileId);
    if (control.matches("[data-browser-profile-none]")) return this.selectBrowserProfile(null);
    if (control.matches("[data-browser-profile-create-default]")) return this.createBrowserProfile("default", true);
    if (control.matches("[data-browser-profile-new]")) return this.showBrowserProfileForm();
    if (control.matches("[data-mention-id]")) return this.chooseMention(control.dataset.mentionId);
    if (control.matches("[data-voice-input]")) return this.startVoiceInput(control);
    if (control.matches("[data-thread-create-agent]")) return this.openCreateAgentDialog();
    if (control.matches("[data-thread-artifact]")) return this.openArtifact(control.dataset.threadArtifact);
    if (control.matches("[data-artifact-copy]")) return this.copyArtifact();
    if (control.matches("[data-artifact-download]")) return this.downloadArtifact();
    if (control.matches("[data-coding-tab]")) return this.switchCodingTab(control.dataset.codingTab);
    if (control.matches("[data-coding-change]")) return this.selectCodingChange(control.dataset.codingChange);
    if (control.matches("[data-coding-action]")) return this.requestCodingAction(control.dataset.codingAction);
    if (control.matches("[data-message-copy]")) return this.copyMessage(control.dataset.messageCopy, control);
    if (control.matches("[data-message-retry]")) return this.retryMessage(control.dataset.messageRetry);
    if (control.matches("[data-run-retry]")) return this.retryRun(control.dataset.runRetry);
    if (control.matches("[data-run-continue]")) return this.continueFailedRun(control.dataset.runContinue);
    if (control.matches("[data-ui-submit]")) return this.submitUiCard(control);
    if (control.matches("[data-ui-action]")) return this.sendThreadMessage(control.dataset.uiMessage || "");
    if (control.matches("[data-message-speak]")) return this.speakMessage(control.dataset.messageSpeak, control);
    if (control.matches("[data-message-summarize]")) return this.summarizeMessage(control.dataset.messageSummarize);
    if (control.matches("[data-message-feedback]")) return this.submitMessageFeedback(control);
    if (control.matches("[data-message-more]")) return this.toggleMessageMenu(control);
    if (control.matches("[data-input-option]")) return this.selectInputOption(control);
    if (control.matches("[data-input-submit]")) return this.submitInputRequest();
    if (control.matches("[data-suggestion]")) return this.applySuggestion(control.dataset.suggestion);
  }

  onInput(event) {
    if (event.target?.matches?.("[data-input-text]")) {
      chatState.inputAnswers[event.target.dataset.inputText] = event.target.value;
      this.updateInputSubmitState();
      return;
    }
    if (event.target?.matches?.("[data-input-extra]")) {
      chatState.inputExtra = event.target.value;
      return;
    }
    if (event.target === this.refs.threadSearch) {
      this.renderThreads();
      return;
    }
    if (event.target !== this.refs.input) return;
    this.saveDraft();
    this.syncComposer();
    this.updateMentionMenu();
  }

  onKeydown(event) {
    if (event.isComposing || event.keyCode === 229) return;
    if (event.key === "Escape") {
      const addMenu = query("#composer-add-menu");
      const returnFocus = !this.refs.actionsMenu?.hidden
        ? this.refs.moreButton
        : !this.refs.modelMenu?.hidden
          ? this.refs.modelButton
          : !addMenu?.hidden
            ? query("#composer-add-button")
            : null;
      this.closeModelMenu();
      this.closeAddMenu();
      this.closeThreadMenu();
      if (returnFocus) {
        event.preventDefault();
        requestAnimationFrame(() => returnFocus.focus());
      }
    }
    if (event.target !== this.refs.input) return;
    if (!this.refs.mentionMenu?.hidden) {
      if (event.key === "Escape") {
        event.preventDefault();
        this.closeMentionMenu();
        return;
      }
      if (event.key === "Enter" && !event.shiftKey) {
        const active = query("[data-mention-id].is-active", this.refs.mentionMenu) || query("[data-mention-id]", this.refs.mentionMenu);
        if (active) {
          this.stopOwnedEvent(event);
          this.chooseMention(active.dataset.mentionId);
          return;
        }
      }
    }
    if (event.key === "Enter" && !event.shiftKey) {
      this.stopOwnedEvent(event);
      this.sendThreadMessage();
    }
  }

  onChange(event) {
    if (event.target === this.refs.fileInput) {
      event.stopImmediatePropagation();
      this.queueUploads(Array.from(event.target.files || []));
      event.target.value = "";
      return;
    }
    if (event.target?.matches?.("#api-base, #tenant-id, #user-id, #workspace-id")) {
      Promise.allSettled([this.loadThreads(), this.loadModelCatalog(), this.loadCapabilities()]);
    }
  }

  onDragOver(event) {
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
    this.refs.dropzone?.classList.add("is-dragging");
  }

  onDrop(event) {
    event.preventDefault();
    this.refs.dropzone?.classList.remove("is-dragging");
    this.queueUploads(Array.from(event.dataTransfer?.files || []));
  }

  async onAuthChanged(event) {
    if (!event.detail?.authenticated) {
      chatState.threads = [];
      await Promise.allSettled([this.loadModelCatalog(), this.loadCapabilities()]);
      this.startNewChat();
      this.renderThreadListNotice("Sign in to load threads", "Your local draft is still available.");
      return;
    }
    await Promise.allSettled([this.loadThreads(), this.loadModelCatalog(), this.loadCapabilities()]);
    this.network("Ready", "idle");
    const threadId = threadIdFromHash();
    if (threadId) await this.loadThread(threadId, false);
  }

  onWindowMessage(event) {
    const apiOrigin = new URL(this.api.settings().apiBase, window.location.href).origin;
    if (![window.location.origin, apiOrigin].includes(event.origin)) return;
    if (event.data?.type !== "taroai.connector.oauth.completed") return;
    this.network("Connector reconnected; resuming the paused action", "success");
    if (chatState.currentThreadId) {
      Promise.resolve(this.loadThread(chatState.currentThreadId, false)).catch(() => {});
    }
  }

  network(message, tone = "idle") {
    setText(this.refs.networkState, message);
    if (this.refs.networkState) this.refs.networkState.dataset.state = tone;
  }

  async loadThreads() {
    if (!this.api.settings().accessToken) {
      chatState.threads = [];
      this.renderThreadListNotice("Sign in to load threads", "Your conversations will appear here.");
      return;
    }
    const queryString = new URLSearchParams({ workspace_id: currentWorkspaceId(), include_archived: "false" });
    try {
      const payload = await this.api.get(`/api/threads?${queryString}`);
      chatState.threads = arrayFrom(payload, "threads", "data");
      this.renderThreads();
    } catch (error) {
      if (!this.api.settings().accessToken) {
        this.renderThreadListNotice("Sign in to load threads", "Your conversations will appear here.");
        return;
      }
      this.renderThreadListNotice("Threads are unavailable", error.message);
    }
  }

  renderThreadListNotice(title, detail = "") {
    if (!this.refs.threadList) return;
    setText(this.refs.threadStatus, title);
    this.refs.threadList.replaceChildren();
    const item = document.createElement("li");
    item.className = "thread-list-notice";
    const strong = document.createElement("strong");
    strong.textContent = title;
    const small = document.createElement("small");
    small.textContent = detail;
    item.append(strong, small);
    this.refs.threadList.append(item);
  }

  renderThreads() {
    if (!this.refs.threadList) return;
    const search = (this.refs.threadSearch?.value || "").trim().toLowerCase();
    const threads = chatState.threads
      .filter((thread) => !["archived", "deleted"].includes(String(thread.status || "").toLowerCase()))
      .filter((thread) => !search || `${thread.title || ""} ${thread.last_message || ""}`.toLowerCase().includes(search))
      .sort((a, b) => Number(Boolean(b.pinned)) - Number(Boolean(a.pinned)) || String(b.updated_at || "").localeCompare(String(a.updated_at || "")));
    setText(this.refs.threadStatus, `${threads.length} ${search ? "matching " : ""}thread${threads.length === 1 ? "" : "s"}`);
    this.refs.threadList.replaceChildren();
    if (!threads.length) {
      const empty = document.createElement("li");
      empty.className = "thread-list-notice";
      empty.innerHTML = `<strong>${search ? "No matching threads" : "No threads yet"}</strong><small>${search ? "Try a shorter search." : "Your conversations will stay here."}</small>`;
      this.refs.threadList.append(empty);
      return;
    }
    for (const thread of (search ? threads : threads.slice(0, 30))) {
      const item = document.createElement("li");
      item.className = "thread-list-item";
      item.classList.toggle("is-active", thread.id === chatState.currentThreadId);
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.threadId = thread.id;
      const title = document.createElement("strong");
      if (thread.pinned) title.append(iconElement("pin"));
      title.append(document.createTextNode(thread.title || "Untitled thread"));
      button.append(title);
      const runStatus = String(thread.run_status || "").toLowerCase();
      if (thread.running || ACTIVE_RUN_STATES.has(runStatus)) {
        const meta = document.createElement("small");
        meta.textContent = runStatus === "awaiting_approval"
          ? "Approval needed"
          : runStatus === "retrying"
            ? "Retrying…"
            : "Working…";
        button.append(meta);
      }
      item.append(button);
      this.refs.threadList.append(item);
    }
  }

  async createThread() {
    const model = chatState.selectedModel;
    const body = {
      workspace_id: currentWorkspaceId(),
      title: "New thread",
      provider_id: model?.provider_id || null,
      model_id: model?.model_id || null,
      reasoning_effort: model?.reasoning_effort || null,
    };
    let created;
    try {
      created = await this.api.post("/api/threads", body, { scope: "thread-create" });
    } catch (error) {
      if (error.body?.code === "model_policy_denied") await this.loadModelCatalog();
      throw error;
    }
    const thread = created.thread || created;
    chatState.currentThreadId = thread.id;
    chatState.thread = thread;
    chatState.threads = [thread, ...chatState.threads.filter((item) => item.id !== thread.id)];
    updateThreadHash(thread.id, true);
    publishChatContext();
    this.renderThreads();
    this.renderDetails();
    return thread;
  }

  async loadThread(threadId, updateHash = false) {
    if (!threadId) return this.startNewChat();
    this.closeArtifactSidecar();
    this.abortStream();
    chatState.loading = true;
    chatState.currentThreadId = threadId;
    chatState.messages = [];
    chatState.events = [];
    chatState.commandOutputs = new Map();
    chatState.disclosureOpen = new Map();
    chatState.queue = [];
    chatState.suggestions = [];
    chatState.inputRequest = null;
    chatState.inputAnswers = {};
    chatState.inputExtra = "";
    this.network("Opening thread…", "loading");
    this.renderConversation();
    try {
      const payload = await this.api.get(`/api/threads/${encodeURIComponent(threadId)}/bootstrap?event_limit=500`);
      const thread = payload.thread || payload;
      const activeRun = payload.active_run || null;
      if (thread.id && thread.id !== threadId) chatState.currentThreadId = thread.id;
      chatState.thread = thread;
      chatState.currentRunId = thread.current_run_id || thread.active_run_id || activeRun?.id || null;
      chatState.messages = await this.hydrateMessageAttachments(
        arrayFrom(payload, "messages", "chat_messages"),
      );
      const latestUserMessage = [...chatState.messages].reverse().find((message) => !isAssistant(message));
      chatState.currentRunMode = activeRun?.mode
        || (latestUserMessage?.kind === "agent" ? "autonomous" : latestUserMessage?.kind === "workflow" ? "workflow" : "chat");
      const hydratedEvents = arrayFrom(payload, "events", "timeline");
      const cachedEvents = this.restoreEventCache(threadId);
      chatState.events = hydratedEvents.length ? hydratedEvents : cachedEvents;
      await this.loadCommandOutputs(chatState.events);
      const latestEvent = chatState.events.at(-1);
      chatState.currentRunId = chatState.currentRunId || latestEvent?.run_id || null;
      const currentRunEvents = chatState.currentRunId
        ? chatState.events.filter((event) => (event.run_id || eventPayload(event).run_id) === chatState.currentRunId)
        : chatState.events;
      const completedAssistantEvent = [...currentRunEvents]
        .reverse()
        .find((event) => eventType(event) === "assistant.message.completed");
      const completedAssistant = eventPayload(completedAssistantEvent || {});
      const completedAssistantId = completedAssistant.message_id
        || (completedAssistantEvent ? `assistant:${chatState.currentRunId || threadId}` : null);
      if (
        completedAssistantId
        && completedAssistant.content
        && !chatState.messages.some((message) => message.id === completedAssistantId)
      ) {
        chatState.messages.push({
          id: completedAssistantId,
          role: "assistant",
          content: completedAssistant.content,
          dispatch_status: "completed",
          delivery_status: "delivered",
          created_at: completedAssistantEvent.created_at || new Date().toISOString(),
        });
      }
      const completedLoop = [...currentRunEvents].reverse().find((event) => eventType(event) === "agent.loop.completed");
      const terminalEvent = [...currentRunEvents].reverse().find((event) => {
        const type = eventType(event);
        return type === "agent.loop.completed"
          || (type === "run.status_changed" && TERMINAL_EVENT_WORDS.includes(String(eventPayload(event).status || "").toLowerCase()))
          || (type.startsWith("run.") && TERMINAL_EVENT_WORDS.some((word) => type.includes(word)));
      });
      const completedOutcome = String(eventPayload(completedLoop || {}).outcome || "").toLowerCase();
      const terminalOutcome = completedLoop
        ? (completedOutcome === "complete" ? "succeeded" : completedOutcome)
        : eventType(terminalEvent || {}) === "run.status_changed"
          ? String(eventPayload(terminalEvent).status || "").toLowerCase()
          : terminalEvent
            ? eventType(terminalEvent).split(".").at(-1) || null
            : null;
      chatState.queue = arrayFrom(payload, "queue", "queued_messages").length
        ? arrayFrom(payload, "queue", "queued_messages")
        : chatState.messages.filter((message) => ["queued", "steering", "ready"].includes(dispatchStatus(message)));
      chatState.artifacts = arrayFrom(payload, "artifacts", "outputs").length
        ? arrayFrom(payload, "artifacts", "outputs")
        : arrayFrom(thread, "artifacts", "outputs");
      chatState.codingWorkspace = null;
      chatState.activeCodingChange = null;
      chatState.running =
        !terminalOutcome && (
          Boolean(thread.running) ||
          ACTIVE_RUN_STATES.has(String(activeRun?.status || "").toLowerCase()) ||
          ACTIVE_RUN_STATES.has(String(thread.run_status || thread.current_run_status || "").toLowerCase()) ||
          chatState.messages.some((message) => dispatchStatus(message) === "inflight")
        );
      const waitingEvent = [...currentRunEvents]
        .reverse()
        .find((event) => eventType(event) === "agent.waiting_for_user");
      if (String(activeRun?.status || "").toLowerCase() === "waiting_for_user" && waitingEvent) {
        chatState.running = false;
        chatState.suggestions = arrayFrom(eventPayload(waitingEvent), "options").map(String).filter(Boolean);
        this.setInputRequest(eventPayload(waitingEvent));
      }
      if (chatState.running && chatState.currentRunId) {
        const streamEvents = chatState.events.filter((event) => event.run_id === chatState.currentRunId);
        const streamedText = streamEvents
          .slice(streamEvents.findLastIndex((event) => eventType(event) === "assistant.stream.reset") + 1)
          .filter((event) => eventType(event) === "assistant.delta")
          .map((event) => eventPayload(event).delta || "")
          .join("");
        if (streamedText) {
          chatState.messages.push({
            id: `stream:${chatState.currentRunId}`,
            role: "assistant",
            content: streamedText,
            dispatch_status: "streaming",
            created_at: latestEvent?.created_at || new Date().toISOString(),
          });
        }
      }
      const restoredSequence = chatState.events.length
        ? Number(localStorage.getItem(`taroai.threadSequence.${threadId}`) || 0)
        : 0;
      const hydratedSequence = chatState.events.reduce((highest, item) => Math.max(highest, eventSequence(item)), 0);
      chatState.lastThreadSequence = Math.max(restoredSequence, hydratedSequence);
      const threadModel = this.findModel(thread.provider_id, thread.model_id);
      if (thread.provider_id && thread.model_id) {
        chatState.selectedModel = threadModel
          ? { ...threadModel, reasoning_effort: thread.reasoning_effort || threadModel.reasoning_effort }
          : null;
        this.renderModelMenu();
        this.renderModelButton();
      }
      chatState.threads = [thread, ...chatState.threads.filter((item) => item.id !== thread.id)];
      if (updateHash) updateThreadHash(thread.id);
      publishChatContext();
      this.restoreDraft();
      this.renderAll();
      if (completedLoop) this.persistPendingAgent(completedOutcome || "complete");
      await this.loadCodingWorkspace();
      await this.maybePromoteManualMessage();
      if (
        !chatState.running
        && eventType(latestEvent || {}) !== "agent.waiting_for_user"
        && (!terminalOutcome || terminalOutcome === "succeeded")
      ) this.loadSuggestions();
      this.network(chatState.running ? workingStatus() : "Thread ready", chatState.running ? "active" : "idle");
      this.startEventStream();
    } catch (error) {
      if (error.status === 404) {
        chatState.loading = false;
        this.startNewChat();
        this.refs.shell.dataset.chatState = "thread";
        if (this.refs.emptyState) this.refs.emptyState.hidden = true;
        this.refs.conversation.replaceChildren();
        this.renderInlineNotice(
          "Thread unavailable",
          "It may have been deleted, archived, or belong to another workspace.",
          "warning",
        );
        this.network("Thread unavailable", "warning");
        return;
      }
      this.network("Could not open thread", "error");
      this.renderInlineNotice("Thread unavailable", error.message, "failure");
    } finally {
      chatState.loading = false;
    }
  }

  async hydrateMessageAttachments(messages) {
    const unresolved = new Set(
      messages.flatMap((message) => arrayFrom(message.attachments || [], "items"))
        .filter((attachment) => typeof attachment === "string" || !attachment.filename)
        .map((attachment) => typeof attachment === "string" ? attachment : attachment.id || attachment.storage_object_id)
        .filter(Boolean),
    );
    if (!unresolved.size) return messages;
    try {
      const payload = await this.api.get(
        `/api/workspaces/${encodeURIComponent(currentWorkspaceId())}/files?include_run_files=true`,
      );
      const files = new Map(
        arrayFrom(payload, "items", "files")
          .map((file) => [file.id || file.storage_object_id, file]),
      );
      return messages.map((message) => ({
        ...message,
        attachments: arrayFrom(message.attachments || [], "items").map((attachment) => {
          const id = typeof attachment === "string" ? attachment : attachment.id || attachment.storage_object_id;
          const file = files.get(id);
          return file ? { id, filename: file.filename || file.logical_path || id } : attachment;
        }),
      }));
    } catch {
      return messages;
    }
  }

  async restoreFromHash() {
    if (!this.api.settings().accessToken) return;
    const route = window.location.hash.replace(/^#/, "").split("/")[0].toLowerCase();
    const handoff = !route || route === "chat" ? takeAgentRunHandoff() : null;
    if (handoff) return this.runAgentHandoff(handoff);
    const threadId = threadIdFromHash();
    if (threadId && threadId !== chatState.currentThreadId) return this.loadThread(threadId, false);
    if (!threadId && chatState.currentThreadId) this.startNewChat(false);
  }

  async runAgentHandoff(handoff) {
    const agentId = String(handoff.agent_id || "").trim();
    this.startNewChat(false);
    chatState.currentRunMode = "autonomous";
    chatState.loading = true;
    this.refs.shell.dataset.chatState = "thread";
    if (this.refs.emptyState) this.refs.emptyState.hidden = true;
    this.refs.conversation.replaceChildren();
    this.renderInlineNotice("Starting agent…", "Opening a new Agent conversation.");
    this.network("Starting agent…", "loading");
    try {
      if (!agentId) throw new Error("Agent run is missing its Agent ID.");
      const version = Number(handoff.version);
      const result = await this.api.post(
        `/api/agents/${encodeURIComponent(agentId)}/runs`,
        {
          input: handoff.input && typeof handoff.input === "object" && !Array.isArray(handoff.input) ? handoff.input : {},
          ...(Number.isInteger(version) && version > 0 ? { version } : {}),
          ...(handoff.provider_id && handoff.model_id ? {
            provider_id: handoff.provider_id,
            model_id: handoff.model_id,
            reasoning_effort: handoff.reasoning_effort || null,
          } : {}),
        },
        { scope: "agent-run" },
      );
      const threadId = result.thread_id;
      if (!threadId) throw new Error("The Agent run did not return a thread.");
      window.dispatchEvent(new CustomEvent("taroai:agents-changed"));
      await this.loadThread(threadId, true);
    } catch (error) {
      chatState.loading = false;
      this.refs.conversation.replaceChildren();
      this.renderInlineNotice("Agent could not start", error.message, "failure");
      this.network("Agent could not start", "error");
      this.refs.input?.focus();
    }
  }

  startNewChat(updateHash = true) {
    this.closeArtifactSidecar();
    this.abortStream();
    const selectedModelKey = modelKey(chatState.selectedModel || {});
    chatState.selectedModel =
      chatState.modelCatalog.find((model) => modelKey(model) === selectedModelKey) ||
      chatState.modelCatalog.find((model) => model.configured !== false) ||
      null;
    chatState.currentThreadId = null;
    chatState.currentRunId = null;
    chatState.currentRunMode = "chat";
    chatState.thread = null;
    chatState.messages = [];
    chatState.queue = [];
    chatState.events = [];
    chatState.artifacts = [];
    chatState.commandOutputs = new Map();
    chatState.disclosureOpen = new Map();
    chatState.codingWorkspace = null;
    chatState.activeCodingChange = null;
    chatState.resourceRefs = [];
    chatState.createIntent = null;
    chatState.uploads = [];
    chatState.running = false;
    chatState.suggestions = [];
    chatState.inputRequest = null;
    chatState.inputAnswers = {};
    chatState.inputExtra = "";
    chatState.browserProfile = null;
    chatState.lastThreadSequence = 0;
    publishChatContext();
    if (updateHash) updateThreadHash(null);
    this.refs.input.value = localStorage.getItem("taroai.threadDraft.new") || "";
    this.network("Ready", "idle");
    this.closeThreadMenu();
    this.closeModelMenu();
    this.renderModelMenu();
    this.renderModelButton();
    this.renderAll();
    this.refs.input?.focus();
  }

  async updateThread(threadId, changes) {
    const updated = await this.api.patch(`/api/threads/${encodeURIComponent(threadId)}`, changes, { scope: "thread-update" });
    const thread = updated.thread || updated;
    chatState.threads = chatState.threads.map((item) => (item.id === threadId ? { ...item, ...thread, ...changes } : item));
    if (chatState.currentThreadId === threadId) chatState.thread = { ...chatState.thread, ...thread, ...changes };
    this.renderThreads();
    this.renderDetails();
    return thread;
  }

  async renameCurrentThread() {
    this.closeThreadMenu();
    if (!chatState.currentThreadId) return;
    const title = window.prompt("Rename thread", chatState.thread?.title || "Untitled thread")?.trim();
    if (title) await this.updateThread(chatState.currentThreadId, { title });
  }

  async pinCurrentThread() {
    this.closeThreadMenu();
    if (!chatState.currentThreadId) return;
    await this.updateThread(chatState.currentThreadId, { pinned: !chatState.thread?.pinned });
  }

  async archiveCurrentThread() {
    this.closeThreadMenu();
    if (!chatState.currentThreadId) return;
    await this.archiveThread(chatState.currentThreadId);
    this.startNewChat();
  }

  async archiveThread(threadId) {
    return this.updateThread(threadId, { status: "archived" });
  }

  async deleteCurrentThread() {
    this.closeThreadMenu();
    if (!chatState.currentThreadId || !window.confirm("Delete this thread and its messages?")) return;
    await this.api.delete(`/api/threads/${encodeURIComponent(chatState.currentThreadId)}`, { scope: "thread-delete" });
    chatState.threads = chatState.threads.filter((thread) => thread.id !== chatState.currentThreadId);
    this.startNewChat();
  }

  toggleThreadMenu() {
    if (!this.refs.actionsMenu) return;
    this.closeModelMenu();
    this.closeAddMenu();
    const open = this.refs.actionsMenu.hidden;
    this.refs.actionsMenu.hidden = !open;
    this.refs.moreButton?.setAttribute("aria-expanded", String(open));
    if (open) requestAnimationFrame(() => query('[role="menuitem"]:not(:disabled)', this.refs.actionsMenu)?.focus());
  }

  closeThreadMenu() {
    if (this.refs.actionsMenu) this.refs.actionsMenu.hidden = true;
    this.refs.moreButton?.setAttribute("aria-expanded", "false");
  }

  async loadModelCatalog() {
    if (!this.api.settings().accessToken) {
      chatState.modelCatalog = [];
      chatState.selectedModel = null;
      this.renderModelMenu();
      this.renderModelButton();
      return;
    }
    const queryString = new URLSearchParams({ workspace_id: currentWorkspaceId() });
    try {
      const payload = await this.api.get(`/api/model-catalog?${queryString}`, { cache: "no-store" });
      let models = arrayFrom(payload, "models", "items");
      if (!models.length && Array.isArray(payload.providers)) {
        models = payload.providers.flatMap((provider) =>
          arrayFrom(provider, "models").map((model) => ({
            ...model,
            provider_id: model.provider_id || provider.id || provider.provider_id || provider.name,
          })),
        );
      } else if (!models.length && payload.providers) {
        models = Object.entries(payload.providers).flatMap(([provider, providerModels]) =>
          arrayFrom(providerModels, "models").map((model) => ({ ...model, provider_id: provider })),
        );
      }
      chatState.modelCatalog = models.map((model) => normalizedModel(model)).filter((model) => model.enabled && model.model_id);
    } catch {
      chatState.modelCatalog = [];
      const signedIn = Boolean(this.api.settings().accessToken);
      this.network(signedIn ? "Models unavailable" : "Sign in to continue", signedIn ? "warning" : "idle");
    }
    const stored = localStorage.getItem("taroai.chatModel");
    const selectable = chatState.modelCatalog.filter((model) => model.configured !== false);
    const fallback = selectable[0] || null;
    const selectedKey = chatState.selectedModel ? modelKey(chatState.selectedModel) : null;
    const threadKey = chatState.currentThreadId && chatState.thread?.provider_id && chatState.thread?.model_id
      ? modelKey(chatState.thread)
      : null;
    chatState.selectedModel = threadKey
      ? selectable.find((model) => modelKey(model) === threadKey) || null
      : selectable.find((model) => modelKey(model) === selectedKey) ||
        selectable.find((model) => modelKey(model) === stored) ||
        fallback;
    this.renderModelMenu();
    this.renderModelButton();
    this.syncComposer();
  }

  findModel(providerId, modelId) {
    return chatState.modelCatalog.find((model) => model.provider_id === providerId && model.model_id === modelId) || null;
  }

  renderModelMenu() {
    if (!this.refs.modelMenu) return;
    this.refs.modelMenu.replaceChildren();
    if (!chatState.modelCatalog.length) {
      const signedIn = Boolean(this.api.settings().accessToken);
      const empty = document.createElement("p");
      empty.className = "model-menu-empty";
      empty.textContent = signedIn ? "No models available for this workspace." : "Sign in to load models.";
      this.refs.modelMenu.append(empty);
      if (!signedIn) {
        const signIn = document.createElement("button");
        signIn.type = "button";
        signIn.className = "model-menu-sign-in";
        signIn.dataset.authDialogOpen = "";
        signIn.setAttribute("role", "menuitem");
        signIn.textContent = "Sign in";
        this.refs.modelMenu.append(signIn);
      }
      return;
    }
    const groups = new Map();
    for (const model of chatState.modelCatalog) {
      const provider = displayProvider(model);
      if (!groups.has(provider)) groups.set(provider, []);
      groups.get(provider).push(model);
    }
    let firstGroup = true;
    for (const [provider, models] of groups) {
      const label = document.createElement("p");
      label.className = "menu-group-label";
      label.classList.toggle("menu-group-follow", !firstGroup);
      const providerName = document.createElement("span");
      providerName.textContent = providerLabel(provider);
      const modelCount = document.createElement("small");
      modelCount.textContent = `${models.length} model${models.length === 1 ? "" : "s"}`;
      label.append(providerName, modelCount);
      this.refs.modelMenu.append(label);
      firstGroup = false;
      for (const model of models) {
        const selected = modelKey(model) === modelKey(chatState.selectedModel || {});
        const locked = model.configured === false;
        const row = document.createElement("div");
        row.className = "model-option chat-model-option";
        row.classList.toggle("is-selected", selected);
        row.classList.toggle("is-locked", locked);
        const button = document.createElement("button");
        button.type = "button";
        button.className = "chat-model-main";
        button.setAttribute("role", "menuitem");
        button.dataset.chatModel = modelKey(model);
        if (locked) button.disabled = true;
        const mark = document.createElement("span");
        mark.className = "model-mark";
        mark.append(iconElement("bot"));
        const copy = document.createElement("span");
        const nameRow = document.createElement("span");
        nameRow.className = "model-name-row";
        const strong = document.createElement("strong");
        strong.textContent = model.display_name;
        nameRow.append(strong);
        if (locked) {
          const lock = document.createElement("span");
          lock.className = "model-lock";
          lock.setAttribute("aria-label", "Locked");
          lock.append(iconElement("lock"));
          nameRow.append(lock);
        } else if (model.is_new || (Array.isArray(model.tags) && model.tags.includes("new"))) {
          const badge = document.createElement("span");
          badge.className = "model-new-badge";
          badge.textContent = "NEW";
          nameRow.append(badge);
        }
        const small = document.createElement("small");
        small.textContent = locked ? "Requires paid plan" : model.description;
        copy.append(nameRow, small);
        button.append(mark, copy);
        row.append(button);
        if (model.reasoning_efforts.length > 1) {
          if (!locked) {
            const currentEffort =
              (selected ? chatState.selectedModel?.reasoning_effort : model.reasoning_effort) ||
              model.reasoning_efforts[0];
            const effort = document.createElement("button");
            effort.type = "button";
            effort.className = "effort-chip";
            effort.setAttribute("role", "menuitem");
            effort.dataset.modelEffort = currentEffort;
            effort.dataset.modelKey = modelKey(model);
            effort.append(document.createTextNode(currentEffort), iconElement("chevron-right"));
            effort.title = "Cycle reasoning effort";
            row.append(effort);
          }
        }
        const check = document.createElement("span");
        check.className = "model-check";
        check.setAttribute("aria-hidden", "true");
        if (selected) check.append(iconElement("check"));
        row.append(check);
        this.refs.modelMenu.append(row);
      }
    }
  }

  renderModelButton() {
    if (!chatState.selectedModel) {
      setIcon(this.refs.selectedModelGlyph, "sparkles");
      setText(this.refs.selectedModel, "Choose model");
      setText(this.refs.detailModel, "");
      return;
    }
    const effort = chatState.selectedModel.reasoning_effort;
    setIcon(this.refs.selectedModelGlyph, "sparkles");
    setText(this.refs.selectedModel, chatState.selectedModel.display_name);
    setText(this.refs.detailModel, [chatState.selectedModel.provider_id, chatState.selectedModel.model_id, effort].filter(Boolean).join(" / "));
  }

  toggleModelMenu() {
    if (!this.refs.modelMenu) return;
    this.closeAddMenu();
    this.closeThreadMenu();
    const open = this.refs.modelMenu.hidden;
    this.refs.modelMenu.hidden = !open;
    this.refs.modelButton?.setAttribute("aria-expanded", String(open));
    if (open) requestAnimationFrame(() => query('[role="menuitem"]:not(:disabled)', this.refs.modelMenu)?.focus());
  }

  closeModelMenu() {
    if (this.refs.modelMenu) this.refs.modelMenu.hidden = true;
    this.refs.modelButton?.setAttribute("aria-expanded", "false");
  }

  toggleAddMenu() {
    const menu = query("#composer-add-menu");
    const button = query("#composer-add-button");
    if (!menu) return;
    this.closeModelMenu();
    this.closeThreadMenu();
    const open = menu.hidden;
    menu.hidden = !open;
    button?.setAttribute("aria-expanded", String(open));
    if (open) requestAnimationFrame(() => query('[role="menuitem"]:not(:disabled)', menu)?.focus());
  }

  closeAddMenu() {
    const menu = query("#composer-add-menu");
    if (menu) menu.hidden = true;
    const browserMenu = query("#composer-browser-menu");
    if (browserMenu) browserMenu.hidden = true;
    query('[data-add-command="browser"]')?.setAttribute("aria-expanded", "false");
    query("#composer-add-button")?.setAttribute("aria-expanded", "false");
  }

  handleAddCommand(command) {
    if (command === "browser") return this.toggleBrowserProfileMenu();
    this.closeAddMenu();
    if (command === "agent") return this.openAgentBuilderDialog();
    if (command === "files") {
      this.refs.fileInput?.click();
      return;
    }
    if (command === "drive" || command === "connectors") return this.openComposerResourceDialog(command);
    if (CREATE_INTENTS[command] && chatState.creationCapabilities[command] === true) {
      this.selectCreateIntent(command);
    }
  }

  async selectModel(key) {
    const model = chatState.modelCatalog.find((item) => modelKey(item) === key);
    if (!model || model.configured === false) return;
    chatState.selectedModel = { ...model };
    localStorage.setItem("taroai.chatModel", key);
    this.closeModelMenu();
    this.renderModelMenu();
    this.renderModelButton();
    this.syncComposer();
    if (chatState.currentThreadId) {
      try {
        await this.updateThread(chatState.currentThreadId, {
          provider_id: model.provider_id,
          model_id: model.model_id,
          reasoning_effort: model.reasoning_effort,
        });
        this.network("Model updated", "success");
      } catch (error) {
        this.network(`Model update failed: ${error.message}`, "error");
      }
    }
  }

  async selectModelEffort(currentEffort, key) {
    const model = chatState.modelCatalog.find((item) => modelKey(item) === key);
    if (!model?.reasoning_efforts.length) return;
    const currentIndex = Math.max(0, model.reasoning_efforts.indexOf(currentEffort));
    model.reasoning_effort = model.reasoning_efforts[(currentIndex + 1) % model.reasoning_efforts.length];
    chatState.selectedModel = { ...model };
    this.renderModelMenu();
    this.renderModelButton();
    if (chatState.currentThreadId) {
      try {
        await this.updateThread(chatState.currentThreadId, { reasoning_effort: model.reasoning_effort });
      } catch (error) {
        this.network(`Effort update failed: ${error.message}`, "error");
      }
    }
  }

  async loadCapabilities() {
    if (!this.api.settings().accessToken) {
      chatState.capabilities = [];
      chatState.creationCapabilities = {};
      this.renderCreationCapabilities();
      return;
    }
    try {
      const payload = await this.api.get(`/api/workspaces/${encodeURIComponent(currentWorkspaceId())}/capabilities`);
      chatState.capabilities = normalizeCapabilities(payload);
      chatState.creationCapabilities = payload.composer_creation || {};
      const profiles = chatState.capabilities.filter((item) => item.type === "browser_profile" && item.enabled);
      chatState.browserProfile = profiles.find((item) => item.id === chatState.browserProfile?.id) || null;
    } catch {
      chatState.capabilities = [];
      chatState.creationCapabilities = {};
      chatState.browserProfile = null;
    }
    this.renderCreationCapabilities();
    this.renderResourceChips();
  }

  renderCreationCapabilities() {
    const capabilities = chatState.creationCapabilities;
    for (const intent of Object.keys(CREATE_INTENTS)) {
      const control = query(`[data-add-command="${intent}"]`);
      if (control) control.hidden = intent !== "agent" && capabilities[intent] !== true;
    }
    const browser = query('[data-add-command="browser"]');
    if (browser) browser.hidden = capabilities.browser !== true;
    const mediaAvailable = ["image", "video", "voice"].some((intent) => capabilities[intent] === true);
    const workflowAvailable = ["workflow", "slides"].some((intent) => capabilities[intent] === true);
    const mediaSeparator = query("[data-media-create-separator]");
    const workflowSeparator = query("[data-workflow-create-separator]");
    if (mediaSeparator) mediaSeparator.hidden = !mediaAvailable;
    if (workflowSeparator) workflowSeparator.hidden = !workflowAvailable;
  }

  updateMentionMenu() {
    const input = this.refs.input;
    const context = mentionQuery(input.value, input.selectionStart || input.value.length);
    chatState.mentionContext = context;
    if (!context) return this.closeMentionMenu();
    const candidates = filterMentionCandidates(chatState.capabilities, context.query);
    this.refs.mentionResults?.replaceChildren();
    if (!candidates.length) {
      const empty = document.createElement("p");
      empty.className = "mention-empty";
      empty.textContent = chatState.capabilities.length ? "No matching resources" : "No workspace resources available";
      this.refs.mentionResults?.append(empty);
    }
    candidates.forEach((candidate, index) => {
      const button = document.createElement("button");
      button.type = "button";
      button.role = "option";
      button.className = index === 0 ? "is-active" : "";
      button.dataset.mentionId = `${candidate.type}:${candidate.id}`;
      const mark = document.createElement("span");
      mark.className = `mention-icon mention-icon-${candidate.type}`;
      mark.append(iconElement(candidate.icon));
      const copy = document.createElement("span");
      const strong = document.createElement("strong");
      strong.textContent = candidate.name;
      const small = document.createElement("small");
      small.textContent = `${candidate.type}${candidate.description ? ` · ${candidate.description}` : ""}`;
      copy.append(strong, small);
      button.append(mark, copy);
      this.refs.mentionResults?.append(button);
    });
    if (this.refs.mentionMenu) this.refs.mentionMenu.hidden = false;
  }

  closeMentionMenu() {
    if (this.refs.mentionMenu) this.refs.mentionMenu.hidden = true;
    chatState.mentionContext = null;
  }

  chooseMention(compoundId) {
    const candidate = chatState.capabilities.find((item) => `${item.type}:${item.id}` === compoundId);
    if (!candidate || !chatState.mentionContext) return;
    const inserted = insertMention(this.refs.input.value, this.refs.input.selectionStart, chatState.mentionContext, candidate);
    this.refs.input.value = inserted.text;
    if (!chatState.resourceRefs.some((item) => item.type === candidate.type && item.id === candidate.id)) {
      chatState.resourceRefs.push({ ...resourceReference(candidate), name: candidate.name });
    }
    this.closeMentionMenu();
    this.renderResourceChips();
    this.syncComposer();
    this.refs.input.focus();
    this.refs.input.setSelectionRange(inserted.cursor, inserted.cursor);
  }

  removeResource(compoundId) {
    chatState.resourceRefs = chatState.resourceRefs.filter((item) => `${item.type}:${item.id}` !== compoundId);
    this.renderResourceChips();
  }

  selectCreateIntent(intent) {
    chatState.createIntent = intent && CREATE_INTENTS[intent] ? intent : null;
    this.closeAddMenu();
    this.renderResourceChips();
    this.syncComposer();
    this.refs.input?.focus();
  }

  selectBrowserProfile(profileId) {
    const profile = chatState.capabilities.find((item) => item.type === "browser_profile" && item.id === profileId) || null;
    chatState.browserProfile = profile;
    this.closeAddMenu();
    this.renderResourceChips();
    this.refs.input?.focus();
  }

  toggleBrowserProfileMenu() {
    const menu = query("#composer-browser-menu");
    if (!menu) return;
    const open = menu.hidden;
    this.renderBrowserProfileMenu();
    menu.hidden = !open;
    query('[data-add-command="browser"]')?.setAttribute("aria-expanded", String(open));
  }

  renderBrowserProfileMenu() {
    const menu = query("#composer-browser-menu");
    if (!menu) return;
    const profiles = chatState.capabilities.filter((item) => item.type === "browser_profile" && item.enabled);
    const profileButtons = profiles.map((profile) => `
      <button type="button" role="menuitemradio" aria-checked="${profile.id === chatState.browserProfile?.id}" data-browser-profile-id="${escapeHtml(profile.id)}">
        <span aria-hidden="true">${icon("globe")}</span><span>${escapeHtml(profile.name)}</span><span aria-hidden="true">${profile.id === chatState.browserProfile?.id ? icon("check") : ""}</span>
      </button>`).join("");
    menu.innerHTML = profiles.length ? `
      ${profiles.length > 1 ? `<button type="button" role="menuitemradio" aria-checked="${!chatState.browserProfile}" data-browser-profile-none><span aria-hidden="true">${icon("globe")}</span><span>No profile</span><span>${chatState.browserProfile ? "" : icon("check")}</span></button>` : ""}
      ${profileButtons}
      <div class="menu-separator"></div>
      <button type="button" role="menuitem" data-browser-profile-new><span aria-hidden="true">${icon("plus")}</span><span>Create profile</span></button>
      <form class="browser-profile-inline-form" data-browser-profile-form hidden>
        <input name="name" maxlength="120" placeholder="Profile name..." aria-label="Profile name" required />
        <button type="submit" aria-label="Create profile">${icon("check")}</button>
      </form>` : `
      <button type="button" role="menuitem" data-browser-profile-create-default><span aria-hidden="true">${icon("plus")}</span><span>Create default profile</span></button>`;
    query("[data-browser-profile-form]", menu)?.addEventListener("submit", (event) => {
      event.preventDefault();
      this.createBrowserProfile(new FormData(event.currentTarget).get("name"));
    });
  }

  showBrowserProfileForm() {
    const menu = query("#composer-browser-menu");
    const form = query("[data-browser-profile-form]", menu);
    if (!form) return;
    query("[data-browser-profile-new]", menu).hidden = true;
    form.hidden = false;
    form.elements.name.focus();
  }

  async createBrowserProfile(rawName, isDefault = false) {
    const name = String(rawName || "").trim();
    if (!name) return;
    try {
      const profile = await this.api.post("/api/browser/profiles", {
        workspace_id: currentWorkspaceId(),
        name,
        is_default: isDefault,
      }, { scope: "browser-profile-create" });
      await this.loadCapabilities();
      this.selectBrowserProfile(profile.id);
      this.network(`Browser profile “${name}” created`, "success");
    } catch (error) {
      this.network(`Browser profile failed: ${error.message}`, "error");
    }
  }

  openComposerResourceDialog(command) {
    const drive = command === "drive";
    const resources = chatState.capabilities.filter((item) => {
      if (item.type !== "connector" || !item.enabled) return false;
      return !drive || `${item.id} ${item.name} ${item.description}`.toLowerCase().includes("google drive");
    });
    const dialog = document.createElement("dialog");
    dialog.className = "chat-dialog composer-resource-dialog";
    const title = drive ? "Import from Google Drive" : "Add connectors";
    const empty = drive ? "Connect Google Drive to import files directly from your Drive." : "No connected connectors available.";
    dialog.innerHTML = `
      <div class="chat-dialog-card">
        <header><div><small>Workspace resources</small><h2>${title}</h2></div><button type="button" data-close aria-label="Close">${icon("x")}</button></header>
        <p>${drive ? "Choose a connected Google Drive account to use in this chat." : "Choose a connected service to reference in your message."}</p>
        <div class="composer-resource-options">
          ${resources.length ? resources.map((item) => `
            <button type="button" class="agent-builder-resource" data-composer-resource="${escapeHtml(item.id)}">
              <span aria-hidden="true">${icon(item.icon)}</span><span><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(item.description || "Connected integration")}</small></span>
            </button>`).join("") : `<p class="agent-builder-empty">${empty}</p>`}
        </div>
        <footer><button type="button" data-close>Cancel</button><button type="button" class="primary" data-manage-connectors>Manage connectors</button></footer>
      </div>`;
    document.body.append(dialog);
    dialog.addEventListener("click", (event) => {
      if (event.target.closest("[data-close]")) return dialog.close();
      if (event.target.closest("[data-manage-connectors]")) {
        dialog.close();
        window.location.hash = "brain/connectors";
        return;
      }
      const button = event.target.closest("[data-composer-resource]");
      const resource = button && resources.find((item) => item.id === button.dataset.composerResource);
      if (!resource) return;
      if (!chatState.resourceRefs.some((item) => item.type === resource.type && item.id === resource.id)) {
        chatState.resourceRefs.push({ ...resourceReference(resource), name: resource.name });
      }
      const mention = `@${resource.name.replace(/\s+/g, "-")}`;
      const suffix = drive ? " Find and attach a file from Google Drive: " : " ";
      this.refs.input.value = `${this.refs.input.value.trimEnd()}${this.refs.input.value.trim() ? " " : ""}${mention}${suffix}`;
      dialog.close();
      this.renderResourceChips();
      this.saveDraft();
      this.syncComposer();
      this.refs.input.focus();
    });
    dialog.addEventListener("close", () => dialog.remove());
    dialog.showModal();
  }

  renderResourceChips() {
    if (!this.refs.resourceChips) return;
    this.refs.resourceChips.replaceChildren();
    if (chatState.createIntent) {
      const intent = CREATE_INTENTS[chatState.createIntent];
      const chip = document.createElement("span");
      chip.className = "resource-chip create-intent-chip";
      chip.innerHTML = `<small>${escapeHtml(intent.chipLabel || "Create")}</small><strong>${escapeHtml(intent.label)}</strong>`;
      const remove = document.createElement("button");
      remove.type = "button";
      remove.dataset.removeCreateIntent = "";
      remove.setAttribute("aria-label", `Remove ${intent.label} intent`);
      remove.append(iconElement("x"));
      chip.append(remove);
      this.refs.resourceChips.append(chip);
    }
    const resources = chatState.browserProfile
      ? [{ ...resourceReference(chatState.browserProfile), name: chatState.browserProfile.name, persistentBrowserProfile: true }, ...chatState.resourceRefs]
      : chatState.resourceRefs;
    for (const resource of resources.filter((item, index, items) => items.findIndex((candidate) => candidate.type === item.type && candidate.id === item.id) === index)) {
      const chip = document.createElement("span");
      chip.className = `resource-chip resource-chip-${resource.type}`;
      const kind = document.createElement("small");
      kind.textContent = resource.type === "browser_profile" ? "browser" : resource.type;
      const name = document.createElement("strong");
      name.textContent = `@${resource.name || resource.id}`;
      const remove = document.createElement("button");
      remove.type = "button";
      if (resource.persistentBrowserProfile) remove.dataset.removeBrowserProfile = "";
      else remove.dataset.removeResource = `${resource.type}:${resource.id}`;
      remove.setAttribute("aria-label", `Remove ${resource.name || resource.id}`);
      remove.append(iconElement("x"));
      chip.append(kind, name, remove);
      this.refs.resourceChips.append(chip);
    }
  }

  async queueUploads(files) {
    for (const file of files) {
      const localId = `upload:${Date.now()}:${Math.random().toString(16).slice(2)}`;
      const upload = { id: localId, file, filename: file.name, size_bytes: file.size, progress: 0, status: "Reading" };
      chatState.uploads.push(upload);
      this.renderUploads();
      try {
        const result = await this.api.upload(file, (progress, status) => {
          upload.progress = progress;
          upload.status = status;
          this.renderUploads();
        });
        Object.assign(upload, result, { local_id: localId, id: result.id || result.storage_object_id || localId, progress: 1, status: "Ready", file: null });
      } catch (error) {
        upload.progress = 1;
        upload.status = "Failed";
        upload.error = error.message;
      }
      this.renderUploads();
      this.syncComposer();
    }
  }

  removeUpload(uploadId) {
    chatState.uploads = chatState.uploads.filter((upload) => upload.id !== uploadId && upload.local_id !== uploadId);
    this.renderUploads();
    this.syncComposer();
  }

  renderUploads() {
    if (!this.refs.uploadList) return;
    this.refs.uploadList.replaceChildren();
    for (const upload of chatState.uploads) {
      const chip = document.createElement("div");
      chip.className = "upload-chip";
      chip.dataset.status = upload.status.toLowerCase();
      const mark = document.createElement("span");
      mark.className = "upload-file-icon";
      mark.append(iconElement("file"));
      const copy = document.createElement("span");
      const strong = document.createElement("strong");
      strong.textContent = upload.filename || upload.name || "Upload";
      const small = document.createElement("small");
      small.textContent = upload.error || upload.status;
      const meter = document.createElement("i");
      meter.style.setProperty("--upload-progress", `${Math.round((upload.progress || 0) * 100)}%`);
      copy.append(strong, small, meter);
      const remove = document.createElement("button");
      remove.type = "button";
      remove.dataset.removeUpload = upload.id;
      remove.setAttribute("aria-label", `Remove ${strong.textContent}`);
      remove.append(iconElement("x"));
      chip.append(mark, copy, remove);
      this.refs.uploadList.append(chip);
    }
  }

  saveDraft() {
    const key = chatState.currentThreadId || "new";
    localStorage.setItem(`taroai.threadDraft.${key}`, this.refs.input?.value || "");
  }

  restoreDraft() {
    if (!this.refs.input) return;
    const key = chatState.currentThreadId || "new";
    this.refs.input.value = localStorage.getItem(`taroai.threadDraft.${key}`) || "";
    this.syncComposer();
  }

  clearDraft() {
    const key = chatState.currentThreadId || "new";
    localStorage.removeItem(`taroai.threadDraft.${key}`);
    if (this.refs.input) this.refs.input.value = "";
  }

  syncComposer() {
    if (!this.refs.input || !this.refs.send) return;
    this.refs.input.style.height = "auto";
    this.refs.input.style.height = `${Math.min(this.refs.input.scrollHeight, 150)}px`;
    const activeRun = chatState.running && !assistantResponseReady();
    const uploadBlocked = chatState.uploads.some((upload) => upload.status !== "Ready");
    const hasContent = Boolean(this.refs.input.value.trim() || chatState.uploads.some((upload) => upload.status === "Ready"));
    const placeholder = activeRun ? "Add a follow-up while Taroai is working..." : "Ask anything… Use @ to add skills, connectors, or agents";
    const sendLabel = activeRun ? "Queue follow-up" : "Send message";
    this.refs.input.placeholder = window.TaroaiI18n?.t(placeholder) || placeholder;
    this.refs.send.setAttribute("aria-label", window.TaroaiI18n?.t(sendLabel) || sendLabel);
    this.refs.send.title = window.TaroaiI18n?.t(sendLabel) || sendLabel;
    this.refs.dropzone.dataset.composerState = activeRun ? "running" : "idle";
    this.refs.send.disabled = !chatState.selectedModel || uploadBlocked || !hasContent;
    if (this.refs.stop) this.refs.stop.hidden = !activeRun;
    this.refs.send.hidden = activeRun && !hasContent;
  }

  async sendThreadMessage(contentOverride = null, deliveryOverride = null, modeOverride = null) {
    if (chatState.uploads.some((upload) => upload.status === "Failed")) {
      this.network("Remove failed files before sending", "warning");
      return null;
    }
    if (chatState.uploads.some((upload) => !["Ready", "Failed"].includes(upload.status))) {
      this.network("Wait for files to finish uploading and scanning", "warning");
      return null;
    }
    const content = (contentOverride ?? this.refs.input?.value ?? "").trim();
    const readyUploads = chatState.uploads.filter((upload) => upload.status === "Ready");
    const attachments = readyUploads.map((upload) => upload.id || upload.storage_object_id);
    const displayAttachments = readyUploads.map((upload) => ({
      id: upload.id || upload.storage_object_id,
      filename: upload.filename || upload.name,
    }));
    if (!content && !attachments.length) return null;
    const displayContent = content || "Review the attached files.";
    const createIntent = chatState.createIntent;
    const submittedContent = createIntent && createIntent !== "workflow" ? `${CREATE_INTENTS[createIntent].prefix}${displayContent}` : displayContent;
    const composerResources = chatState.browserProfile
      ? [{ ...resourceReference(chatState.browserProfile), name: chatState.browserProfile.name }, ...chatState.resourceRefs]
      : chatState.resourceRefs;
    const resourceRefs = composerResources.filter((item, index, items) => items.findIndex((candidate) => candidate.type === item.type && candidate.id === item.id) === index);
    const runMode = modeOverride || (createIntent === "workflow" ? "workflow" : createIntent ? "autonomous" : "chat");
    const startedWithoutThread = !chatState.currentThreadId;
    if (startedWithoutThread) {
      try {
        await this.createThread();
      } catch (error) {
        this.network(`Could not create thread: ${error.message}`, "error");
        return null;
      }
    }
    const deliveryMode = deliveryOverride || (chatState.running ? "queue" : "auto");
    if (!chatState.running && deliveryMode === "auto") chatState.currentRunMode = runMode;
    const optimisticId = `client:${Date.now()}`;
    const optimistic = {
      id: optimisticId,
      role: "user",
      content: displayContent,
      dispatch_status: deliveryMode === "auto" ? "sending" : deliveryMode === "steer" ? "steering" : deliveryMode === "manual" ? "ready" : "queued",
      kind: deliveryMode === "manual" ? "manual_queue" : runMode === "autonomous" ? "agent" : runMode === "workflow" ? "workflow" : "text",
      created_at: new Date().toISOString(),
      attachments: displayAttachments,
      resource_refs: resourceRefs,
      optimistic: true,
    };
    chatState.suggestions = [];
    chatState.inputRequest = null;
    chatState.inputAnswers = {};
    chatState.inputExtra = "";
    chatState.messages.push(optimistic);
    if (startedWithoutThread) localStorage.removeItem("taroai.threadDraft.new");
    this.clearDraft();
    chatState.resourceRefs = [];
    chatState.browserProfile = null;
    chatState.createIntent = null;
    chatState.uploads = [];
    this.renderAll();
    this.network(deliveryMode === "auto" ? (runMode === "chat" ? "Thinking…" : "Starting agent…") : deliveryMode === "steer" ? "Steering requested" : deliveryMode === "manual" ? "Queued for review after this turn" : "Message queued automatically", "loading");
    try {
      const result = await this.api.post(
        `/api/threads/${encodeURIComponent(chatState.currentThreadId)}/messages`,
        {
          content: submittedContent,
          display_content: displayContent,
          skill_ids: optimistic.resource_refs.filter(({ type }) => type === "skill").map(({ id }) => id),
          delivery_mode: deliveryMode,
          mode: runMode,
          timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
          resource_refs: optimistic.resource_refs.filter(({ type }) => type !== "skill").map(({ type, id, version }) => ({ type, id, version: version ?? null })),
          attachments,
        },
        { scope: "thread-message" },
      );
      const message = result.message || result.chat_message || result;
      const persistedMessage = {
        ...optimistic,
        ...message,
        id: message.id || result.message_id || optimisticId,
        dispatch_status: message.dispatch_status || result.dispatch_status || optimistic.dispatch_status,
        attachments: arrayFrom(message.attachments || attachments, "items").map((attachment) => {
          const id = typeof attachment === "string" ? attachment : attachment.id || attachment.storage_object_id;
          return displayAttachments.find((item) => item.id === id) || attachment;
        }),
        optimistic: false,
      };
      chatState.messages = chatState.messages.map((item) => (item.id === optimisticId ? persistedMessage : item));
      chatState.currentRunId = result.run_id || result.current_run_id || chatState.currentRunId;
      publishChatContext();
      const status = dispatchStatus(persistedMessage);
      if (["queued", "ready"].includes(status)) {
        chatState.queue = [persistedMessage, ...chatState.queue.filter((item) => item.id !== persistedMessage.id)];
      } else {
        chatState.currentRunMode = runMode;
        chatState.running = true;
      }
      if (!chatState.thread?.title || chatState.thread.title === "New thread") {
        const suggestedTitle = displayContent.replace(/\s+/g, " ").slice(0, 72).trim();
        if (suggestedTitle) {
          try {
            await this.updateThread(chatState.currentThreadId, { title: suggestedTitle });
          } catch {
            // A title is convenience state; the accepted message remains authoritative.
          }
        }
      }
      this.updateThreadPreview(displayContent);
      this.renderAll();
      this.startEventStream();
      return result;
    } catch (error) {
      if (error.body?.code === "model_policy_denied") await this.loadModelCatalog();
      chatState.messages = chatState.messages.map((item) => (item.id === optimisticId ? { ...item, dispatch_status: "failed", error: error.message } : item));
      this.network(`Message failed: ${error.message}`, "error");
      this.renderConversation();
      return null;
    }
  }

  updateThreadPreview(content) {
    chatState.thread = { ...chatState.thread, last_message: content, updated_at: new Date().toISOString() };
    chatState.threads = chatState.threads.map((thread) => (thread.id === chatState.currentThreadId ? { ...thread, ...chatState.thread } : thread));
    this.renderThreads();
  }

  async editQueuedMessage(messageId) {
    const message = chatState.queue.find((item) => item.id === messageId);
    if (!message) return;
    const content = window.prompt("Edit queued message", messageContent(message))?.trim();
    if (!content) return;
    const updated = await this.api.patch(
      `/api/threads/${encodeURIComponent(chatState.currentThreadId)}/messages/${encodeURIComponent(messageId)}`,
      { content },
      { scope: "queue-edit" },
    );
    chatState.queue = chatState.queue.map((item) => (item.id === messageId ? { ...item, ...(updated.message || updated), content } : item));
    chatState.messages = chatState.messages.map((item) => (item.id === messageId ? { ...item, ...(updated.message || updated), content } : item));
    this.renderAll();
  }

  async deleteQueuedMessage(messageId) {
    await this.api.delete(
      `/api/threads/${encodeURIComponent(chatState.currentThreadId)}/messages/${encodeURIComponent(messageId)}`,
      { scope: "queue-delete" },
    );
    chatState.queue = chatState.queue.filter((item) => item.id !== messageId);
    chatState.messages = chatState.messages.filter((item) => item.id !== messageId);
    this.renderAll();
  }

  async steerQueuedMessage(messageId) {
    const queued = chatState.queue.find((item) => item.id === messageId);
    if (!queued) return;
    const result = await this.api.post(
      `/api/threads/${encodeURIComponent(chatState.currentThreadId)}/steer`,
      {
        content: messageContent(queued),
        attachments: queued.attachments || [],
        resource_refs: queued.resource_refs || [],
      },
      { scope: "queue-steer" },
    );
    await this.deleteQueuedMessage(messageId);
    const status = result.dispatch_status || result.message?.dispatch_status || "steering";
    const nextMessageId = result.message_id || result.message?.id || messageId;
    const replacement = { ...queued, ...result.message, id: nextMessageId, dispatch_status: status };
    chatState.queue = [replacement, ...chatState.queue.filter((item) => item.id !== messageId && item.id !== nextMessageId)];
    chatState.messages = [
      ...chatState.messages.filter((item) => item.id !== messageId && item.id !== nextMessageId),
      replacement,
    ];
    this.network(status === "queued" ? "Steering unavailable; kept in queue" : "Will steer after the current action", status === "queued" ? "warning" : "success");
    this.renderAll();
  }

  addExistingAttachment(storageObject) {
    const id = storageObject?.storage_object_id || storageObject?.id;
    if (!id || chatState.uploads.some((upload) => (upload.id || upload.storage_object_id) === id)) return;
    chatState.uploads.push({
      ...storageObject,
      id,
      filename: storageObject.filename || storageObject.logical_path || id,
      status: "Ready",
      progress: 1,
    });
    this.renderAll();
    this.syncComposer();
    this.refs.input?.focus();
    this.network(`${storageObject.filename || "Workspace file"} attached`, "idle");
  }

  async promoteManualMessage(messageId) {
    const queued = chatState.queue.find((item) => item.id === messageId);
    if (!queued || queued.kind !== "manual_queue") return;
    const promoted = await this.api.post(
      `/api/threads/${encodeURIComponent(chatState.currentThreadId)}/messages/${encodeURIComponent(messageId)}/promote`,
      {},
      { scope: "queue-promote" },
    );
    chatState.queue = chatState.queue.filter((item) => item.id !== messageId);
    chatState.messages = chatState.messages.filter((item) => item.id !== messageId);
    chatState.resourceRefs = arrayFrom(promoted.resource_refs || [], "items");
    chatState.uploads = arrayFrom(promoted.attachments || [], "items").map((attachment) => ({
      id: typeof attachment === "string" ? attachment : attachment.id || attachment.storage_object_id,
      filename: typeof attachment === "string" ? attachment : attachment.filename || attachment.name || attachment.id,
      status: "Ready",
      progress: 1,
    }));
    if (this.refs.input) this.refs.input.value = messageContent(promoted);
    this.saveDraft();
    this.renderAll();
    this.syncComposer();
    this.refs.input?.focus();
    this.network("Manual queued message moved to the composer", "idle");
  }

  async maybePromoteManualMessage() {
    if (chatState.running || chatState.promotingManual || this.refs.input?.value.trim()) return;
    const pending = chatState.queue.find(
      (message) => message.kind === "manual_queue" && dispatchStatus(message) === "ready",
    );
    if (!pending) return;
    chatState.promotingManual = true;
    try {
      await this.promoteManualMessage(pending.id);
    } catch (error) {
      this.network(`Could not restore manual queue: ${error.message}`, "error");
    } finally {
      chatState.promotingManual = false;
    }
  }

  handleQueueAction(control) {
    const id = control.dataset.queueMessageId;
    if (control.dataset.queueAction === "promote") return this.promoteManualMessage(id);
    if (control.dataset.queueAction === "edit") return this.editQueuedMessage(id);
    if (control.dataset.queueAction === "delete") return this.deleteQueuedMessage(id);
    if (control.dataset.queueAction === "steer") return this.steerQueuedMessage(id);
  }

  async dispatchQueue() {
    if (!chatState.currentThreadId || !chatState.queue.length) return;
    try {
      const nextMessage = chatState.queue[0];
      chatState.currentRunMode = nextMessage?.kind === "agent" ? "autonomous" : nextMessage?.kind === "workflow" ? "workflow" : "chat";
      const result = await this.api.post(
        `/api/threads/${encodeURIComponent(chatState.currentThreadId)}/continue`,
        {},
        { scope: "queue-dispatch" },
      );
      chatState.currentRunId = result.run_id || chatState.currentRunId;
      publishChatContext();
      chatState.running = true;
      this.network("Queued message started", "active");
      await this.loadThread(chatState.currentThreadId, false);
    } catch (error) {
      this.network(`Could not start queue: ${error.message}`, "error");
    }
  }

  renderQueue() {
    const queue = chatState.queue.filter((message) => ["queued", "steering", "ready"].includes(dispatchStatus(message)));
    setText(this.refs.queueCount, String(queue.length));
    setText(this.refs.sidecarQueueCount, String(queue.length));
    if (!this.refs.queue) return;
    this.refs.queue.replaceChildren();
    if (!queue.length) {
      const empty = document.createElement("li");
      empty.className = "queue-empty";
      empty.textContent = "Nothing queued.";
      this.refs.queue.append(empty);
      return;
    }
    queue.forEach((message, index) => {
      const item = document.createElement("li");
      item.className = "queue-item";
      item.dataset.status = dispatchStatus(message);
      item.dataset.kind = message.kind || "text";
      const order = document.createElement("span");
      order.className = "queue-order";
      order.textContent = String(index + 1).padStart(2, "0");
      const copy = document.createElement("div");
      const status = document.createElement("small");
      status.textContent = message.kind === "manual_queue"
        ? "Manual - review before sending"
        : dispatchStatus(message) === "steering"
          ? "Steer after current action"
          : "Automatic queue";
      const content = document.createElement("p");
      content.textContent = messageContent(message);
      copy.append(status, content);
      const actions = document.createElement("div");
      actions.className = "queue-actions";
      const availableActions = message.kind === "manual_queue"
        ? [["promote", "Move to composer"], ["steer", "Steer now"], ["edit", "Edit"], ["delete", "Delete"]]
        : [["steer", "Steer now"], ["edit", "Edit"], ["delete", "Delete"]];
      for (const [action, label] of availableActions) {
        const button = document.createElement("button");
        button.type = "button";
        button.dataset.queueAction = action;
        button.dataset.queueMessageId = message.id;
        button.textContent = label;
        actions.append(button);
      }
      item.append(order, copy, actions);
      this.refs.queue.append(item);
    });
  }

  async stopThread() {
    if (!chatState.currentThreadId) return;
    if (this.refs.stop) this.refs.stop.disabled = true;
    this.network(`Stopping ${runSubject().toLowerCase()}…`, "active");
    try {
      if (!chatState.currentRunId) throw new Error("No active run to stop");
      await this.api.post(
        `/api/runs/${encodeURIComponent(chatState.currentRunId)}/cancel`,
        { reason_code: "user_requested" },
        { scope: "thread-stop" },
      );
      chatState.running = false;
      this.network(`${runSubject()} stopped`, "warning");
      this.renderAll();
    } catch (error) {
      this.network(`Stop failed: ${error.message}`, "error");
    } finally {
      if (this.refs.stop) this.refs.stop.disabled = false;
    }
  }

  abortStream() {
    chatState.streamAbort?.abort();
    chatState.streamAbort = null;
    if (chatState.streamRetry) clearTimeout(chatState.streamRetry);
    chatState.streamRetry = null;
    setText(this.refs.detailStream, "Disconnected");
  }

  restoreEventCache(threadId) {
    try {
      const cached = JSON.parse(sessionStorage.getItem(`taroai.threadEvents.${threadId}`) || "[]");
      return Array.isArray(cached) ? cached : [];
    } catch {
      return [];
    }
  }

  persistEventCache() {
    if (!chatState.currentThreadId) return;
    try {
      sessionStorage.setItem(
        `taroai.threadEvents.${chatState.currentThreadId}`,
        JSON.stringify(chatState.events.slice(-240)),
      );
    } catch {
      // The server stream remains authoritative if the browser storage quota is full.
    }
  }

  startEventStream() {
    if (!chatState.currentThreadId || !chatState.running || chatState.streamAbort) return;
    const controller = new AbortController();
    chatState.streamAbort = controller;
    const threadId = chatState.currentThreadId;
    this.api
      .streamThreadEvents(threadId, {
        afterSequence: chatState.lastThreadSequence,
        signal: controller.signal,
        onStatus: (status) => {
          if (threadId !== chatState.currentThreadId) return;
          setText(this.refs.detailStream, status === "connected" ? "Live" : chatState.running ? "Reconnecting" : "Complete");
          if (status === "connected") {
            chatState.reconnectAttempt = 0;
            this.network(chatState.running ? workingStatus(true) : "Live", chatState.running ? "active" : "success");
          }
        },
        onEvent: (frame) => this.applyStreamEvent(frame),
      })
      .catch((error) => {
        if (controller.signal.aborted || threadId !== chatState.currentThreadId || !chatState.running) return;
        this.network(`Connection interrupted · retrying`, "warning");
        this.renderReconnectCard(error.message);
      })
      .finally(() => {
        if (chatState.streamAbort === controller) chatState.streamAbort = null;
        if (!controller.signal.aborted && threadId === chatState.currentThreadId && chatState.running) {
          chatState.reconnectAttempt += 1;
          const delay = Math.min(30000, 1000 * 2 ** Math.min(chatState.reconnectAttempt, 5));
          chatState.streamRetry = window.setTimeout(() => {
            chatState.streamRetry = null;
            this.startEventStream();
          }, delay);
        }
      });
  }

  applyStreamEvent(frame) {
    const payload = frame.data || {};
    const event = payload.event || payload;
    chatState.currentRunId = event.run_id || eventPayload(event).run_id || chatState.currentRunId;
    publishChatContext();
    const sequence = Number(frame.id || eventSequence(event));
    if (sequence && sequence <= chatState.lastThreadSequence) return;
    if (sequence) {
      chatState.lastThreadSequence = sequence;
      localStorage.setItem(`taroai.threadSequence.${chatState.currentThreadId}`, String(sequence));
    }
    if (frame.event === "heartbeat" || eventType(event) === "heartbeat") {
      if (chatState.running) this.network(workingStatus(true), "active");
      return;
    }
    const type = eventType(event);
    const payloadDetail = eventPayload(event);
    const isTextDelta = ["assistant.delta", "text.delta", "message.delta"].some((name) => type.includes(name));
    let terminalStatus = null;
    if (type === "run.created" && ["chat", "autonomous", "workflow"].includes(payloadDetail.mode)) {
      chatState.currentRunMode = payloadDetail.mode;
    }
    if (isTextDelta) {
      const detail = payloadDetail;
      const messageId = detail.message_id || event.message_id || `stream:${chatState.currentRunId || chatState.currentThreadId}`;
      const delta = detail.delta || detail.text || detail.content || "";
      const existing = chatState.messages.find((item) => item.id === messageId);
      if (existing) existing.content = `${existing.content || ""}${delta}`;
      else chatState.messages.push({ id: messageId, role: "assistant", content: delta, status: "streaming", created_at: new Date().toISOString() });
    }
    if (type === "classifier_refusal") {
      const streamId = `stream:${chatState.currentRunId || chatState.currentThreadId}`;
      chatState.messages = chatState.messages.filter((message) => message.id !== streamId);
    }
    if (type === "assistant.stream.reset") {
      const streamId = `stream:${chatState.currentRunId || chatState.currentThreadId}`;
      chatState.messages = chatState.messages.filter((message) => message.id !== streamId);
    }
    if (type === "assistant.message.completed") {
      const finalId = payloadDetail.message_id || `assistant:${chatState.currentRunId || Date.now()}`;
      const streamId = `stream:${chatState.currentRunId || chatState.currentThreadId}`;
      const streamed = chatState.messages.find((item) => item.id === streamId);
      const completed = chatState.messages.find((item) => item.id === finalId);
      if (!streamed && !completed) chatState.enteringMessageId = finalId;
      const finalMessage = {
        ...(streamed || completed || {}),
        id: finalId,
        role: "assistant",
        content: payloadDetail.content || streamed?.content || completed?.content || "",
        dispatch_status: "completed",
        delivery_status: "delivered",
        created_at: streamed?.created_at || completed?.created_at || event.created_at || new Date().toISOString(),
      };
      chatState.messages = [...chatState.messages.filter((item) => ![streamId, finalId].includes(item.id)), finalMessage];
      this.network("Response ready", "success");
    }
    const streamedMessage = event.message || eventPayload(event).message || eventPayload(event).chat_message;
    if (type.includes("message") && streamedMessage && typeof streamedMessage === "object") {
      const message = streamedMessage;
      chatState.messages = [...chatState.messages.filter((item) => item.id !== message.id), message];
      const status = dispatchStatus(message);
      if (["queued", "steering", "ready"].includes(status)) {
        chatState.queue = [...chatState.queue.filter((item) => item.id !== message.id), message];
      } else {
        chatState.queue = chatState.queue.filter((item) => item.id !== message.id);
      }
    }
    if (type === "agent.steering.applied") {
      const messageId = payloadDetail.message_id;
      chatState.messages = chatState.messages.map((item) => item.id === messageId ? { ...item, dispatch_status: "completed" } : item);
      chatState.queue = chatState.queue.filter((item) => item.id !== messageId);
    }
    if (type === "artifact.created") this.captureArtifactFromEvent(event);
    if (type.startsWith("coding.")) this.loadCodingWorkspace();
    if (!chatState.events.some((item) => eventSequence(item) && eventSequence(item) === sequence)) {
      chatState.events.push({ ...event, thread_sequence: sequence || eventSequence(event) });
      this.persistEventCache();
    }
    if (type === "sandbox.command.executed") {
      this.loadCommandOutput(event).then(() => this.renderConversation());
    }
    if (type.includes("run.started") || type.includes("cycle.started") || type.includes("action.requested")) chatState.running = true;
    if (type === "run.status_changed") {
      const status = String(payloadDetail.status || "").toLowerCase();
      chatState.running = ACTIVE_RUN_STATES.has(status);
      if (status === "retrying") this.network(`${runSubject()} hit a temporary error · retrying`, "warning");
      else if (status === "running") this.network(workingStatus(true), "active");
      if (["succeeded", "failed", "cancelled", "timed_out"].includes(status)) terminalStatus = status;
    }
    if (type === "agent.loop.completed") {
      chatState.running = false;
      const outcome = String(payloadDetail.outcome || "complete").toLowerCase();
      terminalStatus = outcome === "complete" ? "succeeded" : outcome;
      this.persistPendingAgent(outcome);
      if (chatState.currentThreadId) {
        if (terminalStatus === "succeeded") this.loadSuggestions();
        this.api
          .get(`/api/threads/${encodeURIComponent(chatState.currentThreadId)}/messages`)
          .then(async (messages) => {
            chatState.messages = await this.hydrateMessageAttachments(
              arrayFrom(messages, "messages", "chat_messages"),
            );
            chatState.queue = chatState.messages.filter((message) => ["queued", "steering", "ready"].includes(dispatchStatus(message)));
            this.renderAll();
            this.maybePromoteManualMessage();
          })
          .catch(() => {});
      }
    }
    if (type === "agent.waiting_for_user") {
      chatState.running = false;
      chatState.suggestions = arrayFrom(payloadDetail, "options").map(String).filter(Boolean);
      this.setInputRequest(payloadDetail);
      this.network("Waiting for your reply", "idle");
    }
    if (type === "assistant.suggestions.generated") {
      chatState.suggestions = arrayFrom(payloadDetail, "options").map(String).filter(Boolean);
    }
    if (type === "approval.requested") this.network("Awaiting approval", "idle");
    terminalStatus ||= {
      "run.succeeded": "succeeded",
      "run.failed": "failed",
      "run.cancelled": "cancelled",
      "run.timed_out": "timed_out",
    }[type] || null;
    if (terminalStatus) {
      chatState.running = false;
      chatState.inputRequest = null;
      chatState.inputAnswers = {};
      chatState.inputExtra = "";
      const stopped = terminalStatus === "cancelled";
      this.network(
        stopped
          ? `${runSubject()} stopped`
          : terminalStatus === "succeeded"
            ? `${runSubject()} finished`
            : terminalStatus === "timed_out"
              ? `${runSubject()} timed out`
              : `${runSubject()} finished with issues`,
        terminalStatus === "succeeded" ? "success" : stopped ? "warning" : "error",
      );
    }
    if (isTextDelta) this.scheduleConversationRender();
    else this.renderAll();
  }

  captureArtifactFromEvent(event) {
    const payload = eventPayload(event);
    const artifact = payload.artifact || payload;
    const id = artifact.id || artifact.artifact_id || artifact.storage_object_id;
    if (!id) return;
    chatState.artifacts = [
      { ...artifact, id, run_id: artifact.run_id || event.run_id || payload.run_id },
      ...chatState.artifacts.filter((item) => item.id !== id),
    ];
  }

  async loadCommandOutputs(events) {
    await Promise.all(
      events
        .filter((event) => eventType(event) === "sandbox.command.executed")
        .map((event) => this.loadCommandOutput(event)),
    );
  }

  async loadCommandOutput(event) {
    const payload = eventPayload(event);
    const key = payload.step_id || payload.storage_object_id;
    const storageObjectId = payload.storage_object_id;
    const cache = chatState.commandOutputs;
    if (!key || !storageObjectId || cache.has(key)) return;
    cache.set(key, {});
    try {
      const output = await this.api.get(`/api/storage/objects/${encodeURIComponent(storageObjectId)}/content`);
      if (cache !== chatState.commandOutputs) return;
      cache.set(key, {
        stdout: safeCommandStream(output.stdout),
        stderr: safeCommandStream(output.stderr),
      });
    } catch {
      if (cache === chatState.commandOutputs) cache.delete(key);
    }
  }

  renderApprovalCard(runId = chatState.currentRunId) {
    const runEvents = this.runActivityEvents(runId);
    const approvalEvents = runEvents.filter((item) => [
        "approval.requested",
        "approval.resolved",
        "approval.rejected",
        "approval.cancelled",
        "action_approval",
      ].includes(eventType(item)));
    const resolvedApprovalIds = new Set(approvalEvents.flatMap((item) => {
      const type = eventType(item);
      const payload = eventPayload(item);
      const approvalId = payload.approval_id || payload.manifestId;
      const resolved = ["approval.resolved", "approval.rejected"].includes(type)
        || type === "approval.cancelled"
        || (type === "action_approval" && payload.status !== "approval_required");
      return resolved && approvalId ? [approvalId] : [];
    }));
    const pendingEvent = [...approvalEvents].reverse().find((item) => {
      const type = eventType(item);
      const payload = eventPayload(item);
      const approvalId = payload.approval_id || payload.manifestId;
      const pending = type === "approval.requested"
        || (type === "action_approval" && payload.status === "approval_required");
      return pending && approvalId && !resolvedApprovalIds.has(approvalId);
    });
    const event = pendingEvent || approvalEvents.at(-1);
    if (!event) return null;
    const type = eventType(event);
    const resolution = ["approval.resolved", "approval.rejected"].includes(type)
      || type === "approval.cancelled";
    const resolutionPayload = eventPayload(event);
    const resolutionId = resolutionPayload.approval_id;
    const request = resolution
      ? [...runEvents].reverse().find((item) => (
        eventType(item) === "approval.requested"
        && eventPayload(item).approval_id === resolutionId
      ))
      : null;
    const payload = resolution
      ? { ...eventPayload(request || {}), ...resolutionPayload }
      : resolutionPayload;
    const connectorAction = type === "action_approval" || payload.kind === "connector_action";
    if (
      !["approval.requested", "approval.resolved", "approval.rejected", "approval.cancelled", "action_approval"].includes(type)
    ) return null;
    const actionStatus = resolution
      ? String(payload.status || (type === "approval.rejected" ? "rejected" : "approved"))
      : connectorAction
        ? String(payload.status || "approval_required")
        : "approval_required";
    const actionComplete = resolution || (type === "action_approval" && actionStatus !== "approval_required");
    const approvalId = payload.approval_id || payload.manifestId;
    if (!approvalId) return null;
    const workflowPreview = payload.kind === "workflow" || String(payload.reason || "").startsWith("Approve workflow:")
      ? [...runEvents].reverse().find((item) => eventType(item) === "workflow_preview")
      : null;
    const workflowSpec = eventPayload(workflowPreview || {}).spec || null;
    const workflowId = eventPayload(workflowPreview || {}).workflowId
      || eventPayload(workflowPreview || {}).previewId;
    const workflowSteps = arrayFrom(workflowSpec || {}, "phases")
      .flatMap((phase) => arrayFrom(phase, "tasks"));
    const card = document.createElement("section");
    card.className = "chat-approval";
    if (actionComplete) card.classList.add(`is-${actionStatus}`);
    card.setAttribute("role", actionComplete ? "status" : "alert");
    const marker = document.createElement("span");
    marker.append(iconElement(
      ["approved", "applied"].includes(actionStatus)
        ? "check"
        : ["rejected", "apply_failed", "blocked_by_validation"].includes(actionStatus)
          ? "x"
          : actionComplete
            ? "ellipsis"
            : "triangle-alert",
    ));
    const copy = document.createElement("div");
    const strong = document.createElement("strong");
    const actionTitles = {
      approved: "Action approved",
      applying: "Applying action",
      applied: "Action applied",
      rejected: "Action rejected",
      apply_failed: "Action failed",
      blocked_by_validation: "Action blocked",
      superseded: "Action superseded",
    };
    strong.textContent = actionComplete
      ? actionTitles[actionStatus] || "Action updated"
      : workflowSpec
        ? "Review workflow"
        : connectorAction
          ? "Review action"
          : "Approval required";
    const reason = document.createElement("p");
    reason.textContent = actionComplete
      ? payload.error || ({
        approved: "Approved. The agent continued.",
        applied: "The connector action finished successfully.",
        rejected: "Rejected. The action was not run.",
      }[actionStatus] || "The action is no longer pending.")
      : workflowSpec
      ? `${workflowSteps.length} steps will run after approval. No task has run yet.`
      : payload.reason || "Review this action before the agent continues.";
    copy.append(strong, reason);
    const toolName = payload.toolName || payload.preview?.toolName;
    if (!workflowSpec && toolName) {
      const preview = document.createElement("code");
      preview.className = "chat-approval-tool";
      preview.textContent = toolName;
      copy.append(preview);
    }
    if (connectorAction && payload.preview) {
      const summary = document.createElement("dl");
      summary.className = "chat-approval-preview";
      const details = [
        ["Provider", payload.provider || payload.preview.provider],
        ["Capability", payload.preview.capability],
        ["Risk", payload.preview.riskLevel],
      ];
      for (const [label, value] of details) {
        if (value == null || value === "") continue;
        const term = document.createElement("dt");
        const description = document.createElement("dd");
        term.textContent = label;
        description.textContent = String(value);
        summary.append(term, description);
      }
      const input = payload.preview.input;
      if (input && typeof input === "object" && !Array.isArray(input)) {
        const term = document.createElement("dt");
        const description = document.createElement("dd");
        term.textContent = "Input";
        description.textContent = Object.entries(input)
          .map(([key, value]) => `${key}: ${typeof value === "string" ? value : JSON.stringify(value)}`)
          .join(" · ")
          .slice(0, 320);
        summary.append(term, description);
      }
      if (summary.childElementCount) copy.append(summary);
    }
    if (workflowSteps.length) {
      const list = document.createElement("ol");
      list.className = "chat-workflow-preview";
      for (const step of workflowSteps) {
        const item = document.createElement("li");
        item.textContent = step.title || step.tool || step.id || "Workflow task";
        list.append(item);
      }
      copy.append(list);
    }
    const actions = document.createElement("div");
    if (!actionComplete && workflowSpec && workflowId) {
      const edit = document.createElement("button");
      edit.type = "button";
      edit.textContent = "Edit plan";
      edit.addEventListener("click", () => {
        this.editWorkflowPreview(workflowId, workflowSpec, runId);
      });
      actions.append(edit);
    }
    for (const decision of actionComplete ? [] : ["approve", "reject"]) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = decision === "approve" && connectorAction ? "Approve & run" : decision === "approve" ? "Approve" : "Reject";
      button.addEventListener("click", () => {
        this.resolveApproval(approvalId, decision, connectorAction ? "connector_action" : payload.kind, runId);
      });
      actions.append(button);
    }
    card.append(marker, copy);
    if (actions.childElementCount) card.append(actions);
    return card;
  }

  editWorkflowPreview(workflowId, spec, runId = chatState.currentRunId) {
    const dialog = document.createElement("dialog");
    dialog.className = "chat-dialog";
    dialog.innerHTML = `<form class="chat-dialog-card"><header><div><small>Review before approval</small><h2>Edit workflow plan</h2></div><button type="button" data-close aria-label="Close">${icon("x")}</button></header><p>Edit task titles, dependencies, tools, inputs, concurrency, or the final synthesis prompt. Task IDs and phases stay fixed once previewed.</p><label><span>Workflow JSON</span><textarea name="spec" rows="18" data-json-field></textarea></label><footer><button type="button" data-close>Cancel</button><button class="primary" type="submit">Save revised plan</button></footer></form>`;
    document.body.append(dialog);
    query('[name="spec"]', dialog).value = JSON.stringify(spec, null, 2);
    queryAll("[data-close]", dialog).forEach((button) => button.addEventListener("click", () => dialog.close()));
    query("form", dialog).addEventListener("submit", async (event) => {
      event.preventDefault();
      const submit = query('[type="submit"]', dialog);
      let revised;
      try {
        revised = JSON.parse(query('[name="spec"]', dialog).value);
      } catch {
        this.network("Workflow plan must be valid JSON", "error");
        return;
      }
      submit.disabled = true;
      try {
        await this.api.patch(
          `/api/workflows/${encodeURIComponent(workflowId)}/preview`,
          { spec: revised },
          { scope: "workflow-preview-edit" },
        );
        dialog.close();
        this.network("Workflow plan updated. Review the new approval.", "success");
        if (chatState.currentThreadId) await this.loadThread(chatState.currentThreadId, false);
      } catch (error) {
        submit.disabled = false;
        this.network(`Could not update workflow plan: ${error.message}`, "error");
      }
    });
    dialog.addEventListener("close", () => dialog.remove());
    dialog.showModal();
  }

  async resolveApproval(approvalId, decision, kind = "action", runId = chatState.currentRunId) {
    try {
      if (kind === "connector_action" && chatState.currentThreadId) {
        const base = `/api/threads/${encodeURIComponent(chatState.currentThreadId)}/action-manifests/${encodeURIComponent(approvalId)}`;
        await this.api.post(`${base}/${decision}`, {}, { scope: `action-${decision}` });
        if (decision === "approve") await this.api.post(`${base}/apply`, {}, { scope: "action-apply" });
      } else {
        const suffix = decision === "reject" ? "/reject" : "";
        await this.api.post(
          `/api/runs/${encodeURIComponent(runId)}/approvals${suffix}`,
          { approval_id: approvalId },
          { scope: `approval-${decision}` },
        );
      }
      this.network(decision === "approve" ? (kind === "connector_action" ? "Action applied" : "Approved") : "Rejected", decision === "approve" ? "success" : "warning");
      if (chatState.currentThreadId) {
        await this.loadThread(chatState.currentThreadId, false);
        if (kind === "connector_action") {
          this.abortStream();
          chatState.running = false;
          this.renderAll();
          return;
        }
        const latestType = eventType(chatState.events.at(-1) || {});
        if (
          decision === "approve"
          && !["run.succeeded", "run.failed", "run.cancelled", "run.timed_out", "agent.loop.completed"].includes(latestType)
        ) {
          chatState.running = true;
          this.renderAll();
          this.startEventStream();
        }
      }
    } catch (error) {
      this.network(`${kind === "connector_action" ? "Action" : "Approval"} failed: ${error.message}`, "error");
    }
  }

  renderWorkflowProgress(runId = chatState.currentRunId) {
    const runEvents = this.runActivityEvents(runId);
    const preview = [...runEvents].reverse().find((event) => eventType(event) === "workflow_preview");
    if (!preview) return null;
    const executionStarted = runEvents.some((event) => [
      "workflow.started",
      "workflow_started",
      "workflow.resumed",
      "workflow.paused",
      "workflow.task.updated",
      "workflow.completed",
      "workflow.failed",
      "workflow.cancelled",
    ].includes(eventType(event)));
    if (!executionStarted) return null;
    const payload = eventPayload(preview);
    const workflowId = payload.workflowId || payload.previewId;
    const spec = payload.spec || {};
    const tasks = arrayFrom(spec, "phases").flatMap((phase) =>
      arrayFrom(phase, "tasks").map((task) => ({ ...task, phaseTitle: phase.title })),
    );
    const updates = new Map();
    for (const event of runEvents) {
      if (eventType(event) !== "workflow.task.updated") continue;
      const detail = eventPayload(event);
      if (!workflowId || detail.workflowId === workflowId) updates.set(detail.taskId, detail);
    }
    const completed = tasks.filter((task) => updates.get(task.id)?.status === "succeeded").length;
    const failed = tasks.find((task) => ["failed", "cancelled", "blocked"].includes(updates.get(task.id)?.status));
    let status = "ready";
    for (const event of runEvents) {
      const type = eventType(event);
      const detail = eventPayload(event);
      if (detail.workflowId && workflowId && detail.workflowId !== workflowId) continue;
      if (["workflow.started", "workflow_started", "workflow.resumed"].includes(type)) status = "running";
      else if (type === "workflow.paused") status = "paused";
      else if (type === "workflow.completed") status = "succeeded";
      else if (type === "workflow.failed") status = "failed";
      else if (type === "workflow.cancelled") status = "cancelled";
      else if (
        type === "workflow.task.updated"
        && ["pending", "queued", "running"].includes(detail.status)
        && !["paused", "cancelled", "succeeded"].includes(status)
      ) status = "running";
    }
    const details = document.createElement("details");
    details.className = "chat-workflow-progress";
    const summary = document.createElement("summary");
    const title = document.createElement("span");
    const statusTitle = failed || status === "failed"
      ? "Workflow needs attention"
      : status === "succeeded"
        ? "Workflow completed"
        : status === "paused"
          ? "Workflow paused"
          : status === "cancelled"
            ? "Workflow cancelled"
            : "Workflow";
    title.innerHTML = `<strong>${statusTitle}</strong><small>${completed} of ${tasks.length} tasks complete</small>`;
    const meter = document.createElement("span");
    meter.className = "chat-workflow-meter";
    meter.style.setProperty("--workflow-progress", `${tasks.length ? (completed / tasks.length) * 100 : 0}%`);
    summary.append(title, meter);
    details.append(summary);
    const list = document.createElement("ol");
    for (const task of tasks) {
      const update = updates.get(task.id) || {};
      const item = document.createElement("li");
      item.dataset.state = update.status || "pending";
      const copy = document.createElement("span");
      const strong = document.createElement("strong");
      const small = document.createElement("small");
      strong.textContent = task.title || task.id;
      small.textContent = update.error || update.status || "Waiting";
      copy.append(strong, small);
      item.append(copy);
      const actions = document.createElement("span");
      actions.className = "chat-workflow-task-actions";
      if (update.childRunId && workflowId) {
        const inspect = document.createElement("button");
        inspect.type = "button";
        inspect.textContent = "View worker";
        inspect.setAttribute("aria-expanded", "false");
        inspect.addEventListener("click", () => this.toggleWorkflowTaskMessages(workflowId, task.id, item, inspect));
        actions.append(inspect);
      }
      if (["failed", "cancelled", "blocked"].includes(update.status) && workflowId) {
        const retry = document.createElement("button");
        retry.type = "button";
        retry.textContent = "Retry";
        retry.addEventListener("click", () => this.retryWorkflowTask(workflowId, task.id));
        actions.append(retry);
      }
      if (actions.childElementCount) item.append(actions);
      list.append(item);
    }
    details.append(list);
    if (workflowId && ["running", "paused"].includes(status)) {
      const controls = document.createElement("footer");
      controls.className = "chat-workflow-actions";
      const toggle = document.createElement("button");
      toggle.type = "button";
      toggle.textContent = status === "paused" ? "Resume" : "Pause";
      toggle.addEventListener("click", () => this.controlWorkflow(workflowId, status === "paused" ? "resume" : "pause"));
      const cancel = document.createElement("button");
      cancel.type = "button";
      cancel.textContent = "Cancel";
      cancel.addEventListener("click", () => this.controlWorkflow(workflowId, "cancel"));
      controls.append(toggle, cancel);
      details.append(controls);
    }
    return details;
  }

  async toggleWorkflowTaskMessages(workflowId, taskId, item, button) {
    const existing = query(".chat-workflow-transcript", item);
    if (existing) {
      existing.hidden = !existing.hidden;
      button.textContent = existing.hidden ? "View worker" : "Hide worker";
      button.setAttribute("aria-expanded", String(!existing.hidden));
      return;
    }
    button.disabled = true;
    button.textContent = "Loading…";
    try {
      const payload = await this.api.get(
        `/api/workflows/${encodeURIComponent(workflowId)}/tasks/${encodeURIComponent(taskId)}/messages`,
      );
      const transcript = document.createElement("div");
      transcript.className = "chat-workflow-transcript";
      const messages = arrayFrom(payload, "messages")
        .filter((message) => ["user", "assistant"].includes(message.role))
        .slice(-8);
      if (!messages.length) {
        transcript.textContent = "The worker has not produced a message yet.";
      } else {
        for (const message of messages) {
          const row = document.createElement("p");
          row.dataset.role = message.role;
          row.textContent = String(message.content || "").slice(0, 4000);
          transcript.append(row);
        }
      }
      item.append(transcript);
      button.textContent = "Hide worker";
      button.setAttribute("aria-expanded", "true");
    } catch (error) {
      button.textContent = "View worker";
      this.network(`Could not load worker: ${error.message}`, "error");
    } finally {
      button.disabled = false;
    }
  }

  async controlWorkflow(workflowId, action) {
    if (action === "cancel" && !window.confirm("Cancel this workflow?")) return;
    try {
      await this.api.post(
        `/api/workflows/${encodeURIComponent(workflowId)}/${action}`,
        {},
        { scope: `workflow-${action}` },
      );
      this.network(action === "resume" ? "Workflow resumed" : action === "pause" ? "Workflow paused" : "Workflow cancelled", action === "cancel" ? "warning" : "success");
    } catch (error) {
      this.network(`Could not ${action} workflow: ${error.message}`, "error");
    }
  }

  async retryWorkflowTask(workflowId, taskId) {
    try {
      await this.api.post(
        `/api/workflows/${encodeURIComponent(workflowId)}/tasks/${encodeURIComponent(taskId)}/retry`,
        {},
        { scope: "workflow-task-retry" },
      );
      this.network("Workflow task queued", "success");
    } catch (error) {
      this.network(`Could not retry task: ${error.message}`, "error");
    }
  }

  renderSecretCapture() {
    const requested = [...chatState.events].reverse().find((event) => eventType(event) === "secret_capture.requested");
    if (!requested) return null;
    const payload = eventPayload(requested);
    const requestId = payload.requestId;
    const resolved = chatState.events.some((event) =>
      eventType(event) === "secret_capture.resolved" && eventPayload(event).requestId === requestId,
    );
    if (!requestId || resolved) return null;
    const form = document.createElement("form");
    form.className = "chat-secret-capture";
    form.innerHTML = `<div><strong>Credential required</strong><p></p></div><label><span class="sr-only">Credential value</span><input type="password" name="value" autocomplete="new-password" required placeholder="Enter credential" /></label><button type="submit">Save securely</button>`;
    form.querySelector("p").textContent = `${payload.name || "This tool"} is needed to continue. The value will not appear in the conversation.`;
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const threadId = chatState.currentThreadId;
      const input = form.elements.value;
      const button = form.querySelector("button");
      button.disabled = true;
      try {
        await this.api.post(
          `/api/secret-captures/${encodeURIComponent(requestId)}`,
          { value: input.value },
          { scope: "secret-capture" },
        );
        input.value = "";
        this.network("Credential saved; the task is resuming", "success");
        if (threadId) await this.loadThread(threadId, false);
      } catch (error) {
        button.disabled = false;
        this.network(`Could not save credential: ${error.message}`, "error");
      }
    });
    return form;
  }

  renderAgentAppResult() {
    const event = [...chatState.events].reverse().find((item) =>
      ["app_created", "app_updated"].includes(eventType(item)),
    );
    if (!event) return null;
    const payload = eventPayload(event);
    if (!payload.agentId) return null;
    const card = document.createElement("section");
    card.className = "chat-agent-app-result";
    const mark = document.createElement("span");
    mark.append(iconElement(payload.appKind === "workflow" ? "workflow" : "bot"));
    const copy = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = payload.name || "Agent draft";
    const detail = document.createElement("p");
    detail.textContent = `${payload.appKind === "workflow" ? "Workflow app" : "Agent"} · Draft v${payload.version || 1}`;
    copy.append(title, detail);
    const open = document.createElement("button");
    open.type = "button";
    open.textContent = eventType(event) === "app_created" ? "Review draft" : "Review update";
    open.addEventListener("click", () => {
      window.location.hash = `agents/${encodeURIComponent(payload.agentId)}`;
    });
    card.append(mark, copy, open);
    return card;
  }

  toggleMessageMenu(control) {
    const menu = query(".message-more-menu", control.closest(".message-meta"));
    if (menu) menu.hidden = !menu.hidden;
  }

  async submitMessageFeedback(control) {
    const messageId = control.dataset.messageId;
    const rating = Number(control.dataset.rating);
    if (!messageId || !chatState.currentRunId || ![-1, 1].includes(rating)) return;
    try {
      await this.api.post("/api/customer-success/feedback", {
        submitted_by_user_id: this.api.settings().userId,
        feedback_type: "thumbs_rating",
        target_type: "run",
        target_id: chatState.currentRunId,
        run_id: chatState.currentRunId,
        rating,
        metadata: { message_id: messageId, thread_id: chatState.currentThreadId },
      }, { scope: "message-feedback" });
      chatState.feedbackByMessage.set(messageId, rating);
      this.renderConversation();
      this.network("Feedback recorded", "success");
    } catch (error) {
      this.network(`Could not record feedback: ${error.message}`, "error");
    }
  }

  renderMessage(message) {
    const assistant = isAssistant(message);
    const article = document.createElement("article");
    article.className = `message ${assistant ? "message-agent" : "message-user"}`;
    article.dataset.messageId = message.id || "";
    if (message.id && chatState.enteringMessageId === message.id) {
      article.classList.add("is-chat-entering");
      chatState.enteringMessageId = null;
    }
    const statusValue = dispatchStatus(message);
    if (statusValue === "streaming") article.classList.add("is-streaming");
    const body = document.createElement("div");
    body.className = "message-body";
    if (assistant) {
      appendMarkdown(body, visibleMessageContent(message));
    } else {
      const content = document.createElement("p");
      visibleMessageContent(message).split("\n").forEach((line, index) => {
        if (index) content.append(document.createElement("br"));
        content.append(line);
      });
      body.append(content);
    }
    article.append(body);
    const refs = arrayFrom(message.resource_refs || [], "items");
    const attachments = arrayFrom(message.attachments || [], "items");
    if (refs.length || attachments.length) {
      const evidence = document.createElement("div");
      evidence.className = "message-evidence-chips";
      refs.forEach((item) => {
        const chip = document.createElement("span");
        const kind = { browser_profile: "Browser", connector: "Connector", agent: "Agent", skill: "Skill" }[item.type] || "Context";
        const name = item.name && item.name !== item.id ? item.name : "";
        chip.textContent = name ? `${kind}: ${name}` : kind;
        evidence.append(chip);
      });
      attachments.forEach((item) => {
        const chip = document.createElement("span");
        chip.append(iconElement("file"), document.createTextNode(item.filename || item.name || item.id || item));
        evidence.append(chip);
      });
      article.append(evidence);
    }
    if (!assistant && message.id) {
      const copyButton = document.createElement("button");
      copyButton.type = "button";
      copyButton.className = "message-user-copy";
      copyButton.dataset.messageCopy = message.id;
      copyButton.title = "Copy message";
      copyButton.setAttribute("aria-label", "Copy message");
      copyButton.append(iconElement("copy"));
      article.append(copyButton);
    }
    const meta = document.createElement("footer");
    meta.className = "message-meta";
    if (!["completed", "sent", "succeeded", "inflight", "streaming"].includes(statusValue)) {
      const status = document.createElement("span");
      status.className = `message-dispatch status-${statusValue}`;
      status.textContent = statusValue === "steering" ? "Steering" : statusValue;
      meta.append(status);
    }
    if (assistant && statusValue !== "streaming") {
      const actions = [
        ["messageCopy", "Copy", '<rect x="9" y="9" width="11" height="11" rx="2"></rect><path d="M5 15V5a1 1 0 0 1 1-1h10"></path>'],
        ["messageFeedback", "Good response", '<path d="M7 10v10H4V10h3Zm3 10V9l4-6 1 1v5h4a1 1 0 0 1 1 1l-1 8a2 2 0 0 1-2 2h-7Z"></path>', "1"],
        ["messageFeedback", "Bad response", '<path d="M7 14V4H4v10h3Zm3-10v11l4 6 1-1v-5h4a1 1 0 0 0 1-1l-1-8a2 2 0 0 0-2-2h-7Z"></path>', "-1"],
        ["messageMore", "More options", '<circle cx="5" cy="12" r="1"></circle><circle cx="12" cy="12" r="1"></circle><circle cx="19" cy="12" r="1"></circle>'],
      ];
      for (const [dataset, label, path, rating] of actions) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "message-icon-action";
        button.dataset[dataset] = rating || message.id;
        button.dataset.messageId = message.id;
        if (rating) button.dataset.rating = rating;
        button.title = label;
        button.setAttribute("aria-label", label);
        if (rating) button.setAttribute("aria-pressed", String(chatState.feedbackByMessage.get(message.id) === Number(rating)));
        button.innerHTML = `<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true">${path}</svg>`;
        meta.append(button);
      }
      const menu = document.createElement("div");
      menu.className = "message-more-menu";
      menu.hidden = true;
      const summarize = document.createElement("button");
      summarize.type = "button";
      summarize.dataset.messageSummarize = message.id;
      summarize.textContent = "Summarize";
      const speak = document.createElement("button");
      speak.type = "button";
      speak.dataset.messageSpeak = message.id;
      speak.textContent = "Read aloud";
      menu.append(summarize, speak);
      meta.append(menu);
    }
    if (statusValue === "failed" && message.optimistic) {
      const retry = document.createElement("button");
      retry.type = "button";
      retry.dataset.messageRetry = message.id;
      retry.textContent = "Retry";
      meta.append(retry);
      if (message.error) meta.title = message.error;
    }
    if (meta.childElementCount) article.append(meta);
    return article;
  }

  runActivityEvents(runId = chatState.currentRunId) {
    const skip = ["assistant.delta", "heartbeat", "text.delta", "message.delta"];
    const events = chatState.events.filter((event) => !skip.some((type) => eventType(event).includes(type)));
    if (!events.length) return [];
    const lastRunId = runId || events.at(-1)?.run_id || null;
    const scoped = lastRunId ? events.filter((event) => (event.run_id || eventPayload(event).run_id) === lastRunId) : [];
    return [...(scoped.length ? scoped : events)].sort((left, right) => {
      const leftSequence = eventSequence(left);
      const rightSequence = eventSequence(right);
      if (leftSequence && rightSequence) return leftSequence - rightSequence;
      const leftTime = new Date(left.created_at || 0).valueOf();
      const rightTime = new Date(right.created_at || 0).valueOf();
      return leftTime - rightTime;
    });
  }

  describeActivityEvent(event) {
    const type = eventType(event);
    const p = eventPayload(event);
    const clip = (value, max = 200) => {
      const text = String(value ?? "").trim();
      return text.length > max ? `${text.slice(0, max)}…` : text;
    };
    if (type === "context.loaded") {
      const references = Number(p.knowledge_result_count) || 0;
      const memories = Number(p.memory_record_count) || 0;
      const reviewed = [
        references && `${references} workspace reference${references === 1 ? "" : "s"}`,
        memories && `${memories} saved memor${memories === 1 ? "y" : "ies"}`,
      ].filter(Boolean);
      return reviewed.length ? { text: `Reviewed ${reviewed.join(" and ")}.` } : null;
    }
    if (["agent.conversation.loaded", "agent.loop.started", "agent.cycle.started"].includes(type)) return null;
    if (type === "run.attachments.materialized") {
      const count = Number(p.count) || (Array.isArray(p.files) ? p.files.length : 0);
      const filename = clip(p.files?.[0]?.filename, 80);
      if (!count) return null;
      return { text: count === 1 && filename ? `Prepared uploaded file · ${filename}` : `Prepared ${count} uploaded files.` };
    }
    if (["model.operation.started", "model.operation.completed", "model.operation.failed", "model.operation.recorded"].includes(type)) {
      const status = type.slice("model.operation.".length);
      const operation = String(p.operation || "model").toLowerCase();
      const key = operation === "verify"
        ? "verification:current"
        : `model:${p.operation_id || eventSequence(event)}`;
      if (status === "failed") return { key, text: "Model step failed.", tone: "warn" };
      return status === "started" && ["decide", "respond_or_act", "respond"].includes(operation)
        ? { key, text: "Thinking", kind: "thinking", transient: true }
        : null;
    }
    if (type === "model.operation.retrying") return { text: "Model connection interrupted · retrying", tone: "warn" };
    if (type === "agent.verification.started") {
      return { key: `verification:${p.cycle_id || "current"}`, text: "Checking the result", transient: true };
    }
    if (type === "agent.verification.completed") {
      const complete = p.outcome === "complete";
      const checks = Array.isArray(p.evidence) ? p.evidence.length : 0;
      if (complete && !checks) return null;
      return {
        key: `verification:${p.cycle_id || "current"}`,
        text: complete && checks
          ? `Verified the result against ${checks} check${checks === 1 ? "" : "s"}.`
          : complete
            ? "Checked the result."
            : "Checked the result · refining the answer.",
        tone: p.outcome === "fail" ? "warn" : null,
      };
    }
    if (type === "agent.verification.skipped") return null;
    if (type === "agent.decision.created") {
      return null;
    }
    if (type.startsWith("tool_call.")) {
      const status = String(p.status || type.slice("tool_call.".length));
      const tool = String(p.tool_name || "tool");
      if (["tool.search", "ui.render"].includes(tool)) return null;
      const label = toolLabel(tool);
      const command = commandActivity(p);
      const fallback = tool === "sandbox.command"
        ? status === "started"
          ? command.started
          : status === "completed"
            ? command.completed
            : status === "awaiting_approval"
              ? `${command.noun} is waiting for approval`
              : status === "cancelled"
                ? `${command.noun} cancelled`
                : `${command.noun} failed`
        : status === "started"
          ? `${label} started`
          : status === "completed"
            ? `${label} completed`
          : status === "awaiting_approval"
            ? `${label} is waiting for approval`
            : status === "cancelled"
              ? `${label} cancelled`
              : `${label} failed`;
      return {
        key: `tool:${toolActivityKey(event) || eventSequence(event)}`,
        text: clip(tool === "sandbox.command" ? fallback : p.summary || fallback),
        tone: status === "failed" ? "warn" : null,
        tool,
        actionKey: toolActivityKey(event),
      };
    }
    if (type === "tool.failed") return null;
    return null;
  }

  renderSearchCard(runId = chatState.currentRunId, actionKey = null) {
    const orderedEvents = this.runActivityEvents(runId);
    const events = [...orderedEvents].reverse();
    const lifecycleEvent = events.find((item) => (
      ["tool_call.started", "tool_call.completed", "tool_call.failed", "tool_call.cancelled", "tool_call.approval_required"].includes(eventType(item))
      && eventPayload(item).tool_name === "web.search"
      && (!actionKey || toolActivityKey(item) === actionKey)
    ) || (
      eventType(item) === "agent.observation.recorded"
      && eventPayload(item).success === false
      && eventPayload(item).result?.tool_name === "web.search"
      && (!actionKey || eventPayload(item).action_id === actionKey)
    ));
    const lifecycleIndex = lifecycleEvent ? orderedEvents.indexOf(lifecycleEvent) : orderedEvents.length - 1;
    const decision = [...orderedEvents.slice(0, lifecycleIndex + 1)].reverse().find((item) =>
      eventType(item) === "agent.decision.created"
      && eventPayload(item).decision?.tool_name === "web.search",
    );
    const event = lifecycleEvent || (!actionKey ? decision : null);
    if (!event) return null;
    const running = event === decision || eventType(event) === "tool_call.started";
    const failed = ["agent.observation.recorded", "tool_call.failed"].includes(eventType(event));
    const cancelled = eventType(event) === "tool_call.cancelled";
    const waiting = eventType(event) === "tool_call.approval_required";
    const output = eventType(event) === "tool_call.completed" ? eventPayload(event).result?.output || {} : {};
    const results = Array.isArray(output?.results) ? output.results : [];
    const details = document.createElement("details");
    details.className = `chat-search-card${running ? " is-running" : failed ? " is-error" : ""}`;
    bindDisclosure(
      details,
      `tool:${runId}:web.search:${actionKey || toolActivityKey(lifecycleEvent || {}) || eventSequence(decision || event)}`,
      false,
    );
    if (running) details.setAttribute("aria-busy", "true");
    const summary = document.createElement("summary");
    const mark = document.createElement("span");
    mark.className = "chat-tool-mark";
    mark.setAttribute("aria-hidden", "true");
    mark.append(iconElement("search"));
    const title = document.createElement("strong");
    title.textContent = running ? "Searching the web" : waiting ? "Search needs approval" : cancelled ? "Search cancelled" : failed ? "Search failed" : "Searched the web";
    const count = document.createElement("span");
    count.className = "chat-tool-state";
    count.textContent = running
      ? "Working…"
      : waiting
      ? "Approval needed"
      : cancelled
      ? "Cancelled"
      : failed
      ? "Failed"
      : results.length
      ? `${results.length} result${results.length === 1 ? "" : "s"}`
      : "Completed";
    summary.append(mark, title, count);
    details.append(summary);
    const queryText = output.query || eventPayload(decision || {}).decision?.tool_input?.query;
    const body = document.createElement("div");
    body.className = "chat-search-results";
    if (queryText) {
      const query = document.createElement("div");
      query.className = "chat-search-query";
      const queryMark = document.createElement("span");
      queryMark.setAttribute("aria-hidden", "true");
      queryMark.append(iconElement("search"));
      const queryValue = document.createElement("span");
      queryValue.textContent = String(queryText);
      query.append(queryMark, queryValue);
      body.append(query);
    }
    if (running || waiting || cancelled || failed) {
      if (body.childElementCount) details.append(body);
      return details;
    }
    const list = document.createElement("ul");
    for (const result of results) {
      try {
        const url = new URL(result.url);
        if (!["https:", "http:"].includes(url.protocol)) continue;
        const item = document.createElement("li");
        const sourceIcon = document.createElement("span");
        sourceIcon.className = "chat-search-source-icon";
        sourceIcon.setAttribute("aria-hidden", "true");
        sourceIcon.textContent = url.hostname.replace(/^www\./, "").charAt(0).toUpperCase();
        const copy = document.createElement("div");
        copy.className = "chat-search-source-copy";
        const link = document.createElement("a");
        link.href = url.href;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent = result.title || url.hostname;
        const meta = document.createElement("small");
        meta.textContent = [url.hostname.replace(/^www\./, ""), result.published_date].filter(Boolean).join(" · ");
        copy.append(link, meta);
        item.append(sourceIcon, copy);
        list.append(item);
      } catch { /* Ignore malformed provider URLs. */ }
    }
    if (list.childElementCount) body.append(list);
    else {
      const note = document.createElement("p");
      note.textContent = "Source details were not retained for this run.";
      body.append(note);
    }
    details.append(body);
    return details;
  }

  renderCodeCard(runId = chatState.currentRunId, actionKey = null) {
    const events = this.runActivityEvents(runId);
    const decisions = events.filter((item) => {
      const value = eventPayload(item).decision || {};
      return eventType(item) === "agent.decision.created" && value.tool_name === "sandbox.command";
    });
    if (!decisions.length) return null;
    const fragment = document.createDocumentFragment();
    for (const [index, decision] of decisions.entries()) {
      const start = events.indexOf(decision);
      const next = decisions[index + 1];
      const executionEvents = events.slice(start, next ? events.indexOf(next) : events.length);
      if (actionKey && !executionEvents.some((item) => toolActivityKey(item) === actionKey)) continue;
      const outcome = [...executionEvents].reverse().find((item) => (
        ["tool_call.started", "tool_call.completed", "tool_call.failed", "tool_call.cancelled", "tool_call.approval_required"].includes(eventType(item))
        && eventPayload(item).tool_name === "sandbox.command"
        && (!actionKey || toolActivityKey(item) === actionKey)
      ) || (
        eventType(item) === "agent.observation.recorded"
        && eventPayload(item).result?.tool_name === "sandbox.command"
        && eventPayload(item).success === false
        && (!actionKey || eventPayload(item).action_id === actionKey)
      ) || item === decision);
      const running = outcome === decision || eventType(outcome || {}) === "tool_call.started";
      const completed = eventType(outcome || {}) === "tool_call.completed" ? outcome : null;
      const failed = ["agent.observation.recorded", "tool_call.failed"].includes(eventType(outcome || {}));
      const cancelled = eventType(outcome || {}) === "tool_call.cancelled";
      const waiting = eventType(outcome || {}) === "tool_call.approval_required";
      const command = String(eventPayload(decision).decision?.tool_input?.command || "").trim();
      const commandPayload = eventPayload(outcome || {});
      const commandCopy = commandActivity(commandPayload);
      const subject = commandSubject(command, commandPayload.command_kind);
      const execution = [...executionEvents].reverse().find((item) => eventType(item) === "sandbox.command.executed");
      const executionPayload = eventPayload(execution || {});
      const streams = chatState.commandOutputs.get(executionPayload.step_id || executionPayload.storage_object_id) || {};
      const details = document.createElement("details");
      details.className = `chat-search-card chat-code-card${running ? " is-running" : failed ? " is-error" : ""}`;
      bindDisclosure(
        details,
        `tool:${runId}:sandbox.command:${actionKey || toolActivityKey(outcome || {}) || eventPayload(decision).decision?.action_key || eventSequence(decision)}`,
        failed || waiting,
      );
      if (running) details.setAttribute("aria-busy", "true");
      const summary = document.createElement("summary");
      const mark = document.createElement("span");
      mark.className = "chat-tool-mark";
      mark.setAttribute("aria-hidden", "true");
      mark.append(iconElement({
        read_file: "file",
        list_files: "list",
        search_files: "file-search",
      }[commandPayload.command_kind] || "terminal"));
      const title = document.createElement("strong");
      const activity = running
        ? commandCopy.started
        : waiting
          ? `${commandCopy.noun} needs approval`
          : cancelled
            ? `${commandCopy.noun} cancelled`
            : failed
              ? `${commandCopy.noun} failed`
              : commandCopy.completed;
      title.textContent = activity;
      summary.append(mark, title);
      if (subject) {
        const detail = document.createElement("span");
        detail.className = "chat-tool-summary-detail";
        detail.textContent = subject;
        summary.append(detail);
      }
      details.append(summary);
      if (command) {
        const code = document.createElement("pre");
        code.textContent = command;
        details.append(code);
      }
      for (const [name, value] of [["stdout", streams.stdout], ["stderr", streams.stderr]]) {
        if (!value) continue;
        const label = document.createElement("small");
        label.className = "chat-code-output-label";
        label.textContent = name;
        const stream = document.createElement("pre");
        stream.className = `chat-code-output is-${name}`;
        stream.textContent = value;
        details.append(label, stream);
      }
      fragment.append(details);
    }
    return fragment;
  }

  renderToolCards(runId = chatState.currentRunId, actionKey = null) {
    const events = this.runActivityEvents(runId);
    const calls = new Map();
    for (const event of events) {
      const type = eventType(event);
      if (!type.startsWith("tool_call.")) continue;
      const p = eventPayload(event);
      const tool = String(p.tool_name || "tool");
      if (["web.search", "sandbox.command", "tool.search", "ui.render"].includes(tool)) continue;
      const key = p.action_id || p.step_id || `${tool}:${eventSequence(event)}`;
      if (actionKey && key !== actionKey) continue;
      calls.set(key, { key, tool, payload: p, event });
    }
    if (!calls.size) return null;

    const assistantEvent = [...events].reverse().find((item) => (
      eventType(item) === "assistant.message.completed"
    ));
    const assistantIndex = chatState.messages.findIndex((item) => (
      item.id === eventPayload(assistantEvent || {}).message_id
    ));
    const messagesBeforeAnswer = assistantIndex < 0
      ? chatState.messages
      : chatState.messages.slice(0, assistantIndex);
    const triggerMessage = [...messagesBeforeAnswer]
      .reverse()
      .find((item) => !isAssistant(item));
    const fragment = document.createDocumentFragment();
    for (const { key, tool, payload, event } of calls.values()) {
      const type = eventType(event);
      const eventIndex = events.indexOf(event);
      const decisionEvent = [...events.slice(0, eventIndex + 1)].reverse().find((item) => {
        if (eventType(item) !== "agent.decision.created") return false;
        const decision = eventPayload(item).decision || {};
        return decision.tool_name === tool
          || (tool === "skill.load" && decision.skill_id === payload.skill_id);
      });
      const input = payload.input || eventPayload(decisionEvent || {}).decision?.tool_input;
      const observation = payload.action_id
        ? [...events].reverse().find((item) => (
          eventType(item) === "agent.observation.recorded"
          && eventPayload(item).action_id === payload.action_id
        ))
        : null;
      const observationPayload = eventPayload(observation || {});
      const failed = type === "tool_call.failed";
      const waiting = type === "tool_call.approval_required";
      const running = type === "tool_call.started";
      const skillOutput = tool === "skill.load" && payload.result?.output && typeof payload.result.output === "object"
        ? payload.result.output
        : {};
      const skillId = String(payload.skill_id || skillOutput.skill_id || "");
      const skillName = String(payload.skill_name || skillOutput.skill_name || skillId.split(".").at(-1) || "Skill")
        .split(/[-_]/)
        .map((part) => part.length <= 3 ? part.toUpperCase() : `${part[0]?.toUpperCase() || ""}${part.slice(1)}`)
        .join(" ");
      const details = document.createElement("details");
      details.className = `chat-search-card chat-code-card chat-tool-card${running ? " is-running" : failed ? " is-error" : ""}`;
      bindDisclosure(details, `tool:${runId}:${tool}:${key}`, failed || waiting);
      if (running) details.setAttribute("aria-busy", "true");
      const summary = document.createElement("summary");
      const mark = document.createElement("span");
      mark.className = "chat-tool-mark";
      mark.setAttribute("aria-hidden", "true");
      mark.append(iconElement(toolIcon(tool)));
      const title = document.createElement("strong");
      title.textContent = tool === "skill.load"
        ? running
          ? `Loading ${skillName}`
          : waiting
            ? `${skillName} needs approval`
          : failed
            ? `Could not load ${skillName}`
            : type === "tool_call.cancelled"
              ? `Cancelled loading ${skillName}`
              : `Used ${skillName}`
        : String(payload.summary || toolLabel(tool));
      summary.append(mark, title);
      details.append(summary);

      const name = document.createElement("p");
      const skillVersion = payload.skill_version || skillOutput.version;
      const skillFileCount = Number(skillOutput.file_count);
      name.textContent = tool === "skill.load"
        ? [skillVersion && `Version ${skillVersion}`, skillFileCount > 0 && `${skillFileCount} file${skillFileCount === 1 ? "" : "s"} ready`].filter(Boolean).join(" · ")
        : [tool, payload.skill_id].filter(Boolean).join(" · ");
      details.append(name);
      const result = payload.result || observationPayload.result;
      const failureClass = observationPayload.failure_class || payload.failure_class;
      const error = payload.safe_error || observationPayload.safe_error || (failed
        ? {
          connector_reconnect_required: "Connector authorization expired. Reconnect it before retrying.",
          policy_blocked: "This tool call was blocked by workspace policy.",
        }[failureClass] || "The tool could not complete. Unsafe error details were hidden."
        : null);
      const fields = tool === "skill.load" ? [["Error", error]] : [["Input", input], ["Result", result], ["Error", error]];
      for (const [label, value] of fields) {
        if (value === null || value === undefined || value === "") continue;
        const caption = document.createElement("small");
        caption.className = "chat-code-output-label";
        caption.textContent = label;
        const output = document.createElement("pre");
        output.className = "chat-code-output";
        output.textContent = safeCommandStream(label === "Input" ? safeToolInput(value, tool) : text(value));
        details.append(caption, output);
      }
      if (failed) {
        const actions = document.createElement("footer");
        actions.className = "chat-workflow-actions";
        const terminalFailure = [...events].reverse().find((item) => (
          ["run.failed", "run.cancelled", "run.timed_out"].includes(eventType(item))
        ));
        if (terminalFailure && runId) {
          const retry = document.createElement("button");
          retry.type = "button";
          retry.dataset.runRetry = runId;
          retry.textContent = "Retry";
          actions.append(retry);
        } else if (triggerMessage) {
          const retry = document.createElement("button");
          retry.type = "button";
          retry.dataset.messageRetry = triggerMessage.id;
          retry.textContent = "Retry";
          actions.append(retry);
        }
        if (triggerMessage) {
          const continueButton = document.createElement("button");
          continueButton.type = "button";
          continueButton.dataset.runContinue = triggerMessage.id;
          continueButton.textContent = "Continue";
          actions.append(continueButton);
        }
        const newChat = document.createElement("button");
        newChat.type = "button";
        newChat.dataset.newChat = "";
        newChat.textContent = "New chat";
        actions.append(newChat);
        details.append(actions);
      }
      fragment.append(details);
    }
    return fragment;
  }

  renderUiElement(spec, elementId, seen = new Set()) {
    const elements = spec?.elements;
    const element = elements?.[elementId];
    if (!element || seen.has(elementId) || seen.size >= 40) return null;
    const branch = new Set(seen).add(elementId);
    const props = element.props && typeof element.props === "object" ? element.props : element;
    const children = Array.isArray(element.children) ? element.children : [];
    let node;
    if (element.type === "Card") {
      node = document.createElement("section");
      node.className = "chat-ui-card";
      if (props.centered) node.classList.add("is-centered");
      if (props.title) {
        const title = document.createElement("strong");
        title.textContent = String(props.title);
        node.append(title);
      }
      if (props.description) {
        const description = document.createElement("p");
        description.className = "chat-ui-description";
        description.textContent = String(props.description);
        node.append(description);
      }
    } else if (element.type === "Stack") {
      node = document.createElement("div");
      node.className = "chat-ui-stack";
      if (props.direction === "horizontal") node.classList.add("is-horizontal");
      if (["sm", "md", "lg"].includes(props.gap)) node.classList.add(`gap-${props.gap}`);
      if (["start", "center", "end", "stretch"].includes(props.align)) {
        node.classList.add(`align-${props.align}`);
      }
    } else if (element.type === "Heading") {
      const level = Math.min(4, Math.max(1, Number(props.level) || 2));
      node = document.createElement(`h${level}`);
      node.className = "chat-ui-heading";
      node.textContent = String(props.text || "");
    } else if (element.type === "Text") {
      node = document.createElement("div");
      node.className = "chat-ui-text";
      if (["body", "caption", "muted", "lead", "code"].includes(props.variant)) {
        node.classList.add(`is-${props.variant}`);
      }
      appendMarkdown(node, String(props.text || ""));
    } else if (element.type === "Badge") {
      node = document.createElement("span");
      node.className = "chat-ui-badge";
      const variant = props.variant || { success: "default", error: "destructive", neutral: "secondary" }[props.tone];
      if (["default", "secondary", "destructive", "outline"].includes(variant)) {
        node.classList.add(`is-${variant}`);
      }
      node.textContent = String(props.text || "");
    } else if (element.type === "Separator") {
      node = document.createElement("hr");
      node.className = "chat-ui-separator";
    } else if (element.type === "Form") {
      node = document.createElement("form");
      node.className = "chat-ui-form";
      node.addEventListener("submit", (event) => {
        event.preventDefault();
        this.submitUiCard(event.submitter || query("[data-ui-submit]", node));
      });
    } else if (element.type === "Input") {
      node = document.createElement("label");
      node.className = "chat-ui-field";
      const label = document.createElement("span");
      label.textContent = String(props.label || props.name || "");
      const input = props.type === "select" ? document.createElement("select") : document.createElement("input");
      input.name = String(props.name || elementId);
      input.required = Boolean(props.required);
      if (input instanceof HTMLInputElement) {
        input.type = ["text", "email", "number", "date"].includes(props.type) ? props.type : "text";
        input.placeholder = String(props.placeholder || "");
      } else {
        for (const value of Array.isArray(props.options) ? props.options.slice(0, 12) : []) {
          const option = document.createElement("option");
          option.value = String(value);
          option.textContent = String(value);
          input.append(option);
        }
      }
      node.append(label, input);
    } else if (element.type === "Button") {
      node = document.createElement("button");
      node.type = props.submit ? "submit" : "button";
      node.className = "chat-ui-button";
      node.textContent = String(props.label || "继续");
      if (props.submit) node.dataset.uiSubmit = "";
      else {
        node.dataset.uiAction = "";
        node.dataset.uiMessage = String(props.message || props.label || "");
      }
    } else if (element.type === "BarChart") {
      node = document.createElement("figure");
      node.className = "chat-ui-chart";
      const labels = Array.isArray(props.labels) ? props.labels.slice(0, 12) : [];
      const values = (Array.isArray(props.values) ? props.values : []).slice(0, labels.length).map(Number);
      const maximum = Math.max(1, ...values.filter(Number.isFinite));
      for (let index = 0; index < values.length; index += 1) {
        if (!Number.isFinite(values[index])) continue;
        const row = document.createElement("div");
        const label = document.createElement("span");
        label.textContent = String(labels[index] || "");
        const meter = document.createElement("meter");
        meter.min = 0;
        meter.max = maximum;
        meter.value = Math.max(0, values[index]);
        meter.setAttribute("aria-label", `${label.textContent}: ${values[index]}`);
        const value = document.createElement("strong");
        value.textContent = String(values[index]);
        row.append(label, meter, value);
        node.append(row);
      }
    } else {
      return null;
    }
    for (const childId of children.slice(0, 40)) {
      const child = this.renderUiElement(spec, String(childId), branch);
      if (child) node.append(child);
    }
    return node;
  }

  submitUiCard(control) {
    const form = control?.closest?.("form");
    if (!form || !form.reportValidity()) return false;
    const answers = [...new FormData(form)].map(([name, value]) => `${name}: ${value}`);
    return answers.length ? this.sendThreadMessage(answers.join("\n")) : false;
  }

  renderUiCards(runId = chatState.currentRunId) {
    const blocks = new Map();
    for (const event of this.runActivityEvents(runId)) {
      if (eventType(event) !== "ui_render") continue;
      const payload = eventPayload(event);
      if (payload.blockId && payload.spec) blocks.set(payload.blockId, payload);
    }
    return [...blocks.values()].flatMap((payload) => {
      const root = this.renderUiElement(payload.spec, payload.spec.root);
      if (!root) return [];
      const section = document.createElement("section");
      section.className = "chat-ui-render";
      section.dataset.uiBlockId = payload.blockId;
      section.setAttribute("aria-label", payload.title || "Structured result");
      section.append(root);
      return [section];
    });
  }

  renderArtifactGroup(artifacts) {
    if (!artifacts.length) return null;
    const outputs = document.createElement("section");
    outputs.className = "chat-inline-artifacts";
    outputs.setAttribute("aria-label", "Created files");
    const title = document.createElement("strong");
    title.textContent = "Created files";
    outputs.append(title);
    for (const artifact of [...artifacts].reverse()) {
      outputs.append(this.renderArtifactButton(artifact));
    }
    return outputs;
  }

  renderThoughtCard(runId = chatState.currentRunId) {
    const events = this.runActivityEvents(runId);
    if (!events.length) return null;
    const steps = [];
    const keyedSteps = new Map();
    const hasModelLifecycle = events.some((event) => (
      ["model.operation.started", "model.operation.completed", "model.operation.failed"].includes(eventType(event))
    ));
    let retrying = false;
    let currentCycleId = null;
    let latestStep = null;
    for (const event of events) {
      if (eventType(event) === "agent.cycle.started") {
        currentCycleId = eventPayload(event).cycle_id || eventSequence(event);
      }
      if (
        eventType(event) === "model.operation.recorded"
        && hasModelLifecycle
      ) continue;
      const status = eventType(event) === "run.status_changed"
        ? String(eventPayload(event).status || "").toLowerCase()
        : "";
      if (status === "retrying") {
        retrying = true;
        latestStep = { text: "Temporary execution error · retrying", tone: "warn" };
        steps.push(latestStep);
        continue;
      }
      if (retrying && status === "running") {
        retrying = false;
        latestStep = { text: "Retry resumed" };
        steps.push(latestStep);
        continue;
      }
      const step = this.describeActivityEvent(event);
      if (!step) continue;
      if (step.key === "verification:current") step.key = `verification:${currentCycleId || eventSequence(event)}`;
      latestStep = step;
      if (step.transient) continue;
      if (step.key && keyedSteps.has(step.key)) {
        steps[keyedSteps.get(step.key)] = step;
      } else {
        if (step.key) keyedSteps.set(step.key, steps.length);
        steps.push(step);
      }
    }
    const lastMessage = chatState.messages.at(-1);
    const responseReady = runId !== chatState.currentRunId
      || !chatState.running
      || (isAssistant(lastMessage || {}) && dispatchStatus(lastMessage) === "completed");
    if (!responseReady && latestStep?.transient) steps.push(latestStep);
    else if (!steps.length && !responseReady && latestStep) steps.push(latestStep);
    if (!steps.length) return null;
    const live = chatState.running && runId === chatState.currentRunId && !responseReady;
    const card = document.createElement("div");
    card.className = "chat-thought";
    card.classList.toggle("is-live", live);
    if (live) card.setAttribute("aria-live", "polite");
    const detail = document.createElement("div");
    detail.className = "chat-thought-detail";
    detail.hidden = false;
    for (const step of steps) {
      const row = document.createElement("div");
      row.className = "chat-thought-step";
      if (step.key) row.dataset.activityKey = step.key;
      if (step.tone === "warn") row.classList.add("is-warn");
      if (step.kind === "thinking") row.classList.add("is-thinking");
      const activityCard = step.tool === "web.search"
        ? this.renderSearchCard(runId, step.actionKey)
        : step.tool === "sandbox.command"
          ? this.renderCodeCard(runId, step.actionKey)
          : step.tool && !["tool.search", "ui.render"].includes(step.tool)
            ? this.renderToolCards(runId, step.actionKey)
            : null;
      if (activityCard && activityCard.childNodes.length) {
        row.classList.add("is-tool");
        row.append(activityCard);
        detail.append(row);
        continue;
      }
      const line = document.createElement("p");
      line.className = "chat-thought-line";
      line.textContent = step.text;
      row.append(line);
      if (step.code) {
        const code = document.createElement("pre");
        code.className = "chat-thought-code";
        code.textContent = step.code;
        row.append(code);
      }
      for (const note of step.notes || []) {
        const noteEl = document.createElement("p");
        noteEl.className = "chat-thought-note";
        noteEl.textContent = note;
        row.append(noteEl);
      }
      detail.append(row);
    }
    card.append(detail);
    return card;
  }

  setInputRequest(payload) {
    const entering = !chatState.inputRequest;
    const questions = arrayFrom(payload, "questions")
      .filter((item) => item && typeof item === "object" && String(item.question || "").trim())
      .map((item) => ({
        question: String(item.question).trim(),
        options: arrayFrom(item, "options").map(String).filter(Boolean).slice(0, 6),
        required: item.required !== false,
      }));
    const options = arrayFrom(payload, "options").map(String).filter(Boolean).slice(0, 6);
    if (!questions.length && options.length) questions.push({ question: "", options, required: true });
    chatState.inputRequest = questions.length ? { questions } : null;
    chatState.inputRequestEntering = entering && Boolean(chatState.inputRequest);
    chatState.inputAnswers = {};
    chatState.inputExtra = "";
  }

  inputRequestComplete() {
    const questions = chatState.inputRequest?.questions || [];
    return questions.every((question, index) => question.required === false || String(chatState.inputAnswers[index] || "").trim());
  }

  selectInputOption(control) {
    chatState.inputAnswers[control.dataset.inputQuestion] = control.dataset.inputOption;
    this.renderConversation();
  }

  updateInputSubmitState() {
    const submit = query("[data-input-submit]", this.refs.conversation);
    if (submit) submit.disabled = !this.inputRequestComplete();
  }

  submitInputRequest() {
    const questions = chatState.inputRequest?.questions || [];
    if (!questions.length || !this.inputRequestComplete()) return;
    const answers = questions.map((question, index) => {
      const answer = String(chatState.inputAnswers[index] || "").trim();
      if (!answer) return "";
      const prompt = question.question.replace(/[?？:]$/, "").trim();
      return prompt ? `${prompt}：${answer}` : answer;
    }).filter(Boolean);
    if (chatState.inputExtra.trim()) answers.push(chatState.inputExtra.trim());
    return this.sendThreadMessage(answers.join(" · "));
  }

  renderInputRequest() {
    const questions = chatState.inputRequest?.questions || [];
    if (!questions.length) return null;
    const section = document.createElement("section");
    section.className = "chat-input-request";
    if (chatState.inputRequestEntering) {
      section.classList.add("is-chat-entering");
      chatState.inputRequestEntering = false;
    }
    section.setAttribute("aria-label", "Answer the follow-up questions");
    questions.forEach((question, index) => {
      const field = document.createElement("fieldset");
      const legend = document.createElement("legend");
      legend.textContent = question.question || "Choose one option";
      field.append(legend);
      if (question.options.length) {
        const options = document.createElement("div");
        options.className = "chat-input-options";
        for (const option of question.options) {
          const button = document.createElement("button");
          button.type = "button";
          button.dataset.inputQuestion = String(index);
          button.dataset.inputOption = option;
          button.textContent = option;
          const selected = chatState.inputAnswers[index] === option;
          button.classList.toggle("is-selected", selected);
          button.setAttribute("aria-pressed", String(selected));
          options.append(button);
        }
        field.append(options);
      } else {
        const input = document.createElement("textarea");
        input.rows = 2;
        input.dataset.inputText = String(index);
        input.placeholder = "Type your answer";
        input.value = chatState.inputAnswers[index] || "";
        field.append(input);
      }
      section.append(field);
    });
    if (questions.every((question) => question.options.length)) {
      const extra = document.createElement("textarea");
      extra.rows = 2;
      extra.dataset.inputExtra = "";
      extra.placeholder = "Anything else you'd like to add? (optional)";
      extra.value = chatState.inputExtra;
      section.append(extra);
    }
    const footer = document.createElement("footer");
    const submit = document.createElement("button");
    submit.type = "button";
    submit.dataset.inputSubmit = "";
    submit.textContent = "Submit";
    submit.disabled = !this.inputRequestComplete();
    footer.append(submit);
    section.append(footer);
    return section;
  }

  renderConversation() {
    if (!this.refs.conversation) return;
    const shouldFollowOutput = this.refs.conversation.scrollHeight
      - this.refs.conversation.scrollTop
      - this.refs.conversation.clientHeight <= 80;
    this.refs.conversation.replaceChildren();
    const hasContent = chatState.messages.length || chatState.events.length;
    this.refs.shell.dataset.chatState = hasContent || chatState.currentThreadId ? "thread" : "empty";
    if (this.refs.emptyState) this.refs.emptyState.hidden = Boolean(hasContent || chatState.currentThreadId);
    if (!hasContent) {
      const intro = document.createElement("article");
      intro.className = "message message-agent chat-intro-message";
      const title = document.createElement("strong");
      title.textContent = chatState.loading ? "Opening your thread…" : "Tell me what you want to accomplish.";
      const body = document.createElement("p");
      body.textContent = chatState.loading
        ? "Restoring messages, queue, and execution evidence."
        : "I’ll keep the plan, tools, verification, and artifacts together in this thread.";
      intro.append(title, body);
      this.refs.conversation.append(intro);
      this.renderSuggestions([
        "Review a document and produce an evidence-backed brief",
        "Research a decision and compare the best options",
        "Build a reusable workflow from this task",
      ]);
      return;
    }

    let renderedDay = "";
    const lastAssistantId = [...chatState.messages].reverse().find((message) => isAssistant(message))?.id;
    const liveAssistantId = chatState.running
      ? [...chatState.messages].reverse().find((message) => isAssistant(message) && dispatchStatus(message) === "streaming")?.id
      : null;
    const assistantRunIds = new Map();
    const activityMessageIds = new Map();
    for (const event of chatState.events) {
      if (eventType(event) !== "assistant.message.completed") continue;
      const messageId = eventPayload(event).message_id;
      const runId = event.run_id || eventPayload(event).run_id;
      if (!messageId || !runId) continue;
      assistantRunIds.set(messageId, runId);
      activityMessageIds.set(runId, messageId);
    }
    if (chatState.running && chatState.currentRunId) {
      activityMessageIds.set(chatState.currentRunId, liveAssistantId);
    }
    const renderedRunIds = new Set();
    const displayableArtifacts = chatState.artifacts.filter(isDisplayableArtifact);
    let currentApprovalCard = null;
    let thoughtRendered = false;
    for (const message of chatState.messages) {
      const createdAt = new Date(message.created_at || message.updated_at || "");
      const day = Number.isNaN(createdAt.valueOf()) ? "" : createdAt.toDateString();
      if (day && day !== renderedDay) {
        renderedDay = day;
        const divider = document.createElement("div");
        divider.className = "conversation-day";
        const label = document.createElement("span");
        label.textContent = day === new Date().toDateString()
          ? "Today"
          : new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(createdAt);
        divider.append(label);
        this.refs.conversation.append(divider);
      }
      const fallbackRunId = message.id && ((!chatState.running && message.id === lastAssistantId) || message.id === liveAssistantId)
        ? chatState.currentRunId
        : null;
      const messageRunId = assistantRunIds.get(message.id) || fallbackRunId;
      const activityMessageForRun = Boolean(
        messageRunId
        && !renderedRunIds.has(messageRunId)
        && (
          message.id === activityMessageIds.get(messageRunId)
          || (!chatState.running && message.id === lastAssistantId)
        )
      );
      if (activityMessageForRun) {
        renderedRunIds.add(messageRunId);
        const currentRun = messageRunId === chatState.currentRunId;
        const thought = this.renderThoughtCard(messageRunId);
        if (thought) {
          this.refs.conversation.append(thought);
          if (currentRun) thoughtRendered = true;
        }
        const approvalCard = this.renderApprovalCard(messageRunId);
        if (approvalCard) this.refs.conversation.append(approvalCard);
        const workflowProgress = this.renderWorkflowProgress(messageRunId);
        if (workflowProgress) this.refs.conversation.append(workflowProgress);
        if (currentRun) currentApprovalCard = approvalCard;
      }
      this.refs.conversation.append(this.renderMessage(message));
      if (activityMessageForRun) {
        for (const card of this.renderUiCards(messageRunId)) this.refs.conversation.append(card);
        const outputs = this.renderArtifactGroup(
          displayableArtifacts.filter((artifact) => artifact.run_id === messageRunId),
        );
        if (outputs) this.refs.conversation.append(outputs);
      }
    }
    if (!renderedRunIds.has(chatState.currentRunId)) {
      const thought = this.renderThoughtCard();
      if (thought) {
        this.refs.conversation.append(thought);
        thoughtRendered = true;
      }
      for (const card of this.renderUiCards()) this.refs.conversation.append(card);
    }
    const unattachedArtifacts = displayableArtifacts.filter(
      (artifact) => !artifact.run_id || !renderedRunIds.has(artifact.run_id),
    );
    const unattachedOutputs = this.renderArtifactGroup(unattachedArtifacts);
    if (unattachedOutputs) this.refs.conversation.append(unattachedOutputs);
    if (!renderedRunIds.has(chatState.currentRunId)) {
      currentApprovalCard = this.renderApprovalCard();
      if (currentApprovalCard) this.refs.conversation.append(currentApprovalCard);
      const workflowProgress = this.renderWorkflowProgress();
      if (workflowProgress) this.refs.conversation.append(workflowProgress);
    }
    const secretCapture = this.renderSecretCapture();
    if (secretCapture) this.refs.conversation.append(secretCapture);
    const agentAppResult = this.renderAgentAppResult();
    if (agentAppResult) this.refs.conversation.append(agentAppResult);
    const runEvents = this.runActivityEvents();
    const responseReady = assistantResponseReady();
    const failedRun = [...runEvents]
      .reverse()
      .find((event) => ["run.failed", "run.timed_out"].includes(eventType(event)));
    const classifierRefusal = [...runEvents]
      .reverse()
      .find((event) => eventType(event) === "classifier_refusal");
    const policyBlock = [...runEvents]
      .reverse()
      .find((event) => eventType(event) === "policy.blocked");
    const completedRun = [...runEvents]
      .reverse()
      .find((event) => eventType(event) === "agent.loop.completed");
    if (!chatState.running && classifierRefusal) {
      this.renderInlineNotice(
        "Safety filter declined this request",
        "The model's safety filter flagged this request. Try rephrasing it or choose another model.",
        "warning",
      );
    } else if (!chatState.running && policyBlock) {
      this.renderInlineNotice(
        "Request blocked",
        eventPayload(policyBlock).reason || "This request cannot be completed under the current policy.",
        "failure",
      );
    } else if (
      !chatState.running
      && failedRun
      && String(eventPayload(completedRun || {}).outcome || "failed") !== "complete"
    ) {
      const reason = String(eventPayload(failedRun).reason || "");
      const failure = {
        model_budget_exceeded: ["Usage limit reached", "This workspace reached its model usage limit. Choose another model or ask an owner to update the limit."],
        cost_budget_exhausted: ["Usage limit reached", "This run reached its cost limit. Any completed output is preserved."],
        model_policy_denied: ["Model not allowed", "The selected model is not allowed for this request. Choose another model."],
        model_gateway_error: ["Model provider error", "The selected model provider did not complete the response. Retry or choose another model."],
        workflow_task_failed: ["Workflow task failed", "A workflow task failed. Completed task output is preserved; review the task and retry."],
      }[reason] || (eventType(failedRun) === "run.timed_out"
        ? ["The run timed out", "Any completed output is preserved. Retry with a smaller request or a longer run limit."]
        : ["The run ended with an error", "Any completed output is preserved. Retry the message or choose another model."]);
      const notice = this.renderInlineNotice(
        failure[0],
        failure[1],
        "failure",
      );
      const actions = document.createElement("footer");
      actions.className = "chat-workflow-actions";
      if (!["model_budget_exceeded", "cost_budget_exhausted", "model_policy_denied"].includes(reason)) {
        const retry = document.createElement("button");
        retry.type = "button";
        retry.dataset.runRetry = failedRun.run_id || eventPayload(failedRun).run_id || chatState.currentRunId;
        retry.textContent = "Retry";
        const continueButton = document.createElement("button");
        continueButton.type = "button";
        continueButton.dataset.runContinue = "";
        continueButton.textContent = "Continue";
        actions.append(retry, continueButton);
      }
      const newChat = document.createElement("button");
      newChat.type = "button";
      newChat.dataset.newChat = "";
      newChat.textContent = "New chat";
      actions.append(newChat);
      notice.append(actions);
    }
    if (chatState.running && !responseReady && !currentApprovalCard) {
      if (!thoughtRendered && !liveAssistantId) {
        const thinking = document.createElement("div");
        thinking.className = "chat-thinking";
        thinking.setAttribute("role", "status");
        const marker = document.createElement("span");
        marker.setAttribute("aria-hidden", "true");
        marker.append(iconElement("brain-circuit"));
        const label = document.createElement("span");
        label.textContent = "Thinking";
        if (chatState.currentRunMode !== "chat") label.textContent = `${runSubject()} is working`;
        thinking.append(marker, label);
        this.refs.conversation.append(thinking);
      }
    }
    const inputRequest = !chatState.running ? this.renderInputRequest() : null;
    if (inputRequest) this.refs.conversation.append(inputRequest);
    else if (!chatState.running && chatState.messages.length && chatState.suggestions.length) this.renderSuggestions(chatState.suggestions);
    if (shouldFollowOutput) {
      requestAnimationFrame(() => {
        this.refs.conversation.scrollTop = this.refs.conversation.scrollHeight;
      });
    }
  }

  renderSuggestions(suggestions) {
    const row = document.createElement("div");
    row.className = "quick-row thread-suggestions";
    if (chatState.suggestionsEntering) {
      row.classList.add("is-chat-entering");
      chatState.suggestionsEntering = false;
    }
    row.setAttribute("aria-label", "Suggested follow-ups");
    for (const suggestion of suggestions) {
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.suggestion = suggestion;
      const arrow = document.createElement("span");
      arrow.className = "suggestion-arrow";
      arrow.setAttribute("aria-hidden", "true");
      arrow.append(iconElement("arrow-right"));
      const label = document.createElement("span");
      label.textContent = suggestion;
      button.append(arrow, label);
      row.append(button);
    }
    this.refs.conversation.append(row);
  }

  renderInlineNotice(title, detail, kind = "notice") {
    if (!this.refs.conversation) return;
    const card = document.createElement("article");
    card.className = `inline-system-card inline-system-${kind}`;
    const strong = document.createElement("strong");
    strong.textContent = title;
    const body = document.createElement("p");
    body.textContent = detail;
    card.append(strong, body);
    this.refs.conversation.append(card);
    return card;
  }

  renderReconnectCard(detail) {
    const existing = query("[data-reconnect-card]", this.refs.conversation);
    if (existing) {
      setText(query("p", existing), detail);
      return;
    }
    const card = document.createElement("article");
    card.className = "inline-system-card reconnect-card";
    card.dataset.reconnectCard = "true";
    const pulse = document.createElement("span");
    pulse.className = "reconnect-pulse";
    const copy = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = "Reconnecting to this thread";
    const body = document.createElement("p");
    body.textContent = detail;
    copy.append(title, body);
    card.append(pulse, copy);
    this.refs.conversation?.append(card);
  }

  renderArtifacts() {
    if (!this.refs.artifactList) return;
    this.refs.artifactList.replaceChildren();
    const artifacts = chatState.artifacts.filter(isDisplayableArtifact);
    if (this.refs.artifactEmpty) this.refs.artifactEmpty.hidden = Boolean(artifacts.length);
    for (const artifact of artifacts) {
      this.refs.artifactList.append(this.renderArtifactButton(artifact));
    }
  }

  renderArtifactButton(artifact) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "thread-artifact-item";
    button.dataset.threadArtifact = artifact.id || artifact.artifact_id;
    const mark = document.createElement("span");
    mark.append(iconElement(artifact.kind === "dashboard" ? "grid-2x2" : "file"));
    const copy = document.createElement("span");
    const title = document.createElement("strong");
    title.textContent = artifact.name || artifact.title || artifact.filename || "Artifact";
    const meta = document.createElement("small");
    meta.textContent = artifact.media_type || artifact.kind || artifact.status || "Output";
    copy.append(title, meta);
    button.append(mark, copy);
    return button;
  }

  async openArtifact(artifactId) {
    let artifact = chatState.artifacts.find((item) => (item.id || item.artifact_id) === artifactId);
    if (!artifact) return;
    this.openSidecar("artifacts");
    if (!artifact.content && !artifact.text && artifactId) {
      try {
        const loaded = await this.api.get(`/api/artifacts/${encodeURIComponent(artifactId)}`);
        artifact = { ...artifact, ...(loaded.artifact || loaded) };
        chatState.artifacts = chatState.artifacts.map((item) => ((item.id || item.artifact_id) === artifactId ? artifact : item));
      } catch (error) {
        artifact = { ...artifact, content: `Preview unavailable\n\n${error.message}` };
      }
    }
    chatState.activeArtifact = artifact;
    if (window.taroaiArtifacts?.open) {
      await window.taroaiArtifacts.open(artifact);
      return;
    }
    if (this.refs.artifactStage) this.refs.artifactStage.hidden = false;
    setText(this.refs.artifactStageTitle, artifact.name || artifact.title || artifact.filename || "Artifact");
    setText(this.refs.artifactStageContent, artifact.content || artifact.text || artifact.markdown || text(artifact.data || artifact));
  }

  async copyArtifact() {
    const content = this.refs.artifactStageContent?.textContent || "";
    if (!content) return;
    await navigator.clipboard?.writeText(content);
    this.network("Artifact copied", "success");
  }

  downloadArtifact() {
    const artifact = chatState.activeArtifact;
    if (!artifact) return;
    if (artifact.download_url || artifact.url) {
      window.open(artifact.download_url || artifact.url, "_blank", "noopener");
      return;
    }
    const blob = new Blob([this.refs.artifactStageContent?.textContent || ""], { type: artifact.media_type || "text/plain" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = escapeFilename(artifact.filename || artifact.name || "artifact.txt");
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 0);
  }

  async loadCodingWorkspace() {
    if (!chatState.currentRunId) {
      chatState.codingWorkspace = null;
      this.renderCodingWorkspace();
      return;
    }
    try {
      const payload = await this.api.get(`/api/runs/${encodeURIComponent(chatState.currentRunId)}/coding-workspace`);
      chatState.codingWorkspace = payload.available ? payload.detail : null;
      const changes = chatState.codingWorkspace?.changes || [];
      if (!chatState.activeCodingChange || !changes.some((item) => item.id === chatState.activeCodingChange.id)) chatState.activeCodingChange = changes[0] || null;
    } catch {
      chatState.codingWorkspace = null;
      chatState.activeCodingChange = null;
    }
    this.renderCodingWorkspace();
  }

  renderCodingWorkspace() {
    if (!this.refs.codingRoot) return;
    const detail = chatState.codingWorkspace;
    this.refs.codingRoot.hidden = !detail;
    if (this.refs.codingEmpty) this.refs.codingEmpty.hidden = Boolean(detail);
    if (!detail) return;
    const workspace = detail.coding_workspace || {};
    const repository = detail.repository || {};
    const changes = arrayFrom(detail, "changes");
    const tests = arrayFrom(detail, "tests");
    const checkpoints = arrayFrom(detail, "checkpoints");
    const deliveries = arrayFrom(detail, "deliveries");
    setText(query("[data-coding-repository]"), repository.name || repository.repository_url || "Repository");
    setText(query("[data-coding-branch]"), workspace.branch || "Branch pending");
    setText(query("[data-coding-status]"), workspace.status || "preparing");
    queryAll("[data-coding-action]").forEach((button) => { button.disabled = !workspace.engine_session_id; button.title = workspace.engine_session_id ? "" : "Attach an external Agent Engine to request this action"; });
    setText(query("[data-coding-files]"), `${changes.length} file${changes.length === 1 ? "" : "s"}`);
    setText(query("[data-coding-additions]"), `+${changes.reduce((sum, item) => sum + Number(item.additions || 0), 0)}`);
    setText(query("[data-coding-deletions]"), `−${changes.reduce((sum, item) => sum + Number(item.deletions || 0), 0)}`);
    this.refs.codingChanges.replaceChildren();
    for (const change of changes) {
      const button = document.createElement("button"); button.type = "button"; button.dataset.codingChange = change.id; button.className = "coding-change-row"; button.classList.toggle("is-active", change.id === chatState.activeCodingChange?.id);
      const status = document.createElement("span"); status.textContent = { added: "A", modified: "M", deleted: "D", renamed: "R", untracked: "?" }[change.status] || "M"; status.dataset.status = change.status;
      const path = document.createElement("strong"); path.textContent = change.path;
      const stats = document.createElement("small"); stats.textContent = `+${change.additions || 0} −${change.deletions || 0}`;
      button.append(status, path, stats); this.refs.codingChanges.append(button);
    }
    if (!changes.length) this.refs.codingChanges.innerHTML = `<p class="route-note">No file changes reported by the Runner.</p>`;
    setText(this.refs.codingDiff, chatState.activeCodingChange?.binary ? "Binary file changed" : chatState.activeCodingChange?.patch || "Select a changed file.");
    this.renderCodingEvidence(this.refs.codingTests, tests, (item) => [item.command, `${item.status} · ${Number(item.duration_seconds || 0).toFixed(2)}s`, item.summary]);
    this.renderCodingEvidence(this.refs.codingCheckpoints, checkpoints, (item) => [item.label, item.revision, item.snapshot_id ? `Snapshot ${item.snapshot_id}` : "Revision checkpoint"]);
    this.renderCodingEvidence(this.refs.codingDeliveries, deliveries, (item) => [item.status, item.commit_sha || item.pull_request_number || "Delivery", item.pull_request_url || item.commit_message || ""]);
  }

  renderCodingEvidence(root, values, describe) {
    if (!root) return; root.replaceChildren();
    for (const item of values) {
      const row = document.createElement("article"); row.className = "coding-evidence-row";
      const [title, meta, body] = describe(item); const strong = document.createElement("strong"); strong.textContent = title; const small = document.createElement("small"); small.textContent = meta; const paragraph = document.createElement("p"); paragraph.textContent = body; row.append(strong, small, paragraph); root.append(row);
    }
    if (!values.length) root.innerHTML = `<p class="route-note">No evidence reported yet.</p>`;
  }

  switchCodingTab(tab) {
    queryAll("[data-coding-tab]").forEach((button) => button.classList.toggle("is-active", button.dataset.codingTab === tab));
    queryAll("[data-coding-panel]").forEach((panel) => { panel.hidden = panel.dataset.codingPanel !== tab; });
  }

  selectCodingChange(changeId) {
    chatState.activeCodingChange = (chatState.codingWorkspace?.changes || []).find((item) => item.id === changeId) || null;
    this.renderCodingWorkspace();
  }

  async requestCodingAction(action) {
    const workspace = chatState.codingWorkspace?.coding_workspace;
    if (!workspace) return;
    let message = null; let command = null;
    if (action === "test") command = window.prompt("Test command", "npm test") || null;
    if (["checkpoint", "commit", "pull_request"].includes(action)) message = window.prompt(action === "pull_request" ? "Pull request title" : action === "commit" ? "Commit message" : "Checkpoint label") || null;
    if ((action === "test" && !command) || (["checkpoint", "commit", "pull_request"].includes(action) && !message)) return;
    try {
      await this.api.post(`/api/coding-workspaces/${encodeURIComponent(workspace.id)}/actions`, { action, message, command }, { scope: `coding-${action}` });
      this.network(`Coding action requested · ${action.replaceAll("_", " ")}`, "success");
      window.setTimeout(() => this.loadCodingWorkspace(), 800);
    } catch (error) { this.network(error.message, "error"); }
  }

  openSidecar(view = "artifacts") {
    this.closeThreadMenu();
    chatState.activeSidecar = view;
    this.refs.sidecar?.classList.remove("is-operations-open");
    this.refs.sidecar?.classList.add("is-artifact-open", "is-chat-sidecar-open");
    setText(this.refs.sidecarState, "artifact");
    queryAll("[data-sidecar-tab]").forEach((button) => {
      button.classList.toggle("is-active", button.dataset.sidecarTab === view);
    });
    queryAll("[data-sidecar-view]").forEach((section) => {
      const active = section.dataset.sidecarView === view;
      section.hidden = !active;
      section.classList.toggle("is-active", active);
    });
    setText(this.refs.sidecarTitle, { artifacts: "Artifacts", code: "Coding Workspace", queue: "Queue", details: "Thread details" }[view] || "Thread");
    query("[data-open-queue]")?.setAttribute("aria-expanded", String(view === "queue"));
    if (view === "code") this.loadCodingWorkspace();
  }

  closeArtifactSidecar() {
    window.taroaiArtifacts?.close?.();
    chatState.activeArtifact = null;
    this.refs.sidecar?.classList.remove("is-artifact-open", "is-chat-sidecar-open");
    if (this.refs.artifactStage) {
      this.refs.artifactStage.replaceChildren();
      this.refs.artifactStage.hidden = true;
    }
    setText(this.refs.sidecarState, "closed");
  }

  renderDetails() {
    setText(this.refs.detailId, chatState.currentThreadId || "Not started");
    setText(this.refs.detailRun, chatState.currentRunId || (chatState.running ? "Starting" : "Idle"));
    this.renderModelButton();
    const terminalRun = [...chatState.events]
      .reverse()
      .find((event) => eventType(event).startsWith("run.") && TERMINAL_EVENT_WORDS.some((word) => eventType(event).includes(word)));
    const terminalStatus = terminalRun ? eventType(terminalRun).split(".").at(-1) : null;
    const latestApproval = [...chatState.events]
      .reverse()
      .find((event) => eventType(event).startsWith("approval.") || eventType(event) === "action_approval");
    const awaitingApproval = eventType(latestApproval || {}) === "approval.requested"
      || (eventType(latestApproval || {}) === "action_approval" && eventPayload(latestApproval).status === "approval_required");
    const responseReady = assistantResponseReady();
    setText(
      this.refs.threadPresence,
      awaitingApproval
        ? "Awaiting approval"
        : responseReady && chatState.running
          ? "Response ready"
        : chatState.running
        ? "Working"
        : terminalStatus
          ? `${terminalStatus[0].toUpperCase()}${terminalStatus.slice(1)}`
          : chatState.currentThreadId
            ? "Saved"
            : "Ready",
    );
    this.refs.threadPresence?.classList.toggle("running", chatState.running && !responseReady);
    if (this.refs.moreButton) this.refs.moreButton.hidden = !chatState.currentThreadId;
    if (this.refs.shareButton) this.refs.shareButton.disabled = !chatState.currentThreadId;
    this.refs.createAgentButtons.forEach((button) => {
      button.disabled = !chatState.currentThreadId || chatState.running;
      button.title = button.disabled ? "Finish a thread before creating an agent" : "Create a reusable agent from this thread";
    });
  }

  scheduleConversationRender() {
    if (this.conversationFrame !== null) return;
    this.conversationFrame = requestAnimationFrame(() => {
      this.conversationFrame = null;
      this.renderConversation();
    });
  }

  renderAll() {
    if (this.conversationFrame !== null) cancelAnimationFrame(this.conversationFrame);
    this.conversationFrame = null;
    this.renderThreads();
    this.renderConversation();
    this.renderQueue();
    this.renderArtifacts();
    this.renderCodingWorkspace();
    this.renderResourceChips();
    this.renderUploads();
    this.renderDetails();
    this.syncComposer();
  }

  async shareThread() {
    this.closeThreadMenu();
    if (!chatState.currentThreadId) return;
    try {
      const share = await this.api.post(`/api/threads/${encodeURIComponent(chatState.currentThreadId)}/shares`, {}, { scope: "thread-share" });
      chatState.share = share;
      this.openShareDialog(share);
    } catch (error) {
      this.network(`Could not share: ${error.message}`, "error");
    }
  }

  async loadSuggestions() {
    if (!chatState.currentThreadId) return;
    try {
      const payload = await this.api.get(`/api/threads/${encodeURIComponent(chatState.currentThreadId)}/suggestions`);
      chatState.suggestions = arrayFrom(payload, "suggestions", "items").map((item) => typeof item === "string" ? item : item.label || item.prompt).filter(Boolean);
      chatState.suggestionsEntering = Boolean(chatState.suggestions.length);
      this.renderConversation();
    } catch {
      chatState.suggestions = [];
    }
  }

  async reconnectConnector(connectionId, actionId = null) {
    try {
      const result = await this.api.post(`/api/connectors/${encodeURIComponent(connectionId)}/reconnect`, { thread_id: chatState.currentThreadId, run_id: chatState.currentRunId, action_id: actionId }, { scope: "connector-reconnect" });
      if (!result.authorization_url) throw new Error("Authorization URL was not returned");
      const popup = window.open(result.authorization_url, "connector-reconnect", "width=620,height=760");
      if (!popup) throw new Error("Allow popups to reconnect this connector");
      this.network("Complete authorization in the popup; this action will resume automatically", "loading");
    } catch (error) { this.network(`Reconnect failed: ${error.message}`, "error"); }
  }

  openShareDialog(share) {
    const dialog = document.createElement("dialog");
    dialog.className = "chat-dialog share-dialog";
    const threadId = chatState.currentThreadId;
    const sharePath = share.url || share.share_url;
    const shareUrl = sharePath ? new URL(sharePath, `${this.api.settings().apiBase}/`).href : "";
    dialog.innerHTML = `
      <form method="dialog" class="chat-dialog-card">
        <header><div><small>Read-only link</small><h2>Share this thread</h2></div><button value="close" aria-label="Close">${icon("x")}</button></header>
        <p>Anyone with this link can view the published conversation and artifacts. Private Operations data stays hidden.</p>
        <div class="share-link-row"><input value="${escapeHtml(shareUrl)}" readonly /><button type="button" data-share-copy>Copy link</button></div>
        <footer><button type="button" class="danger-text" data-share-revoke>Revoke link</button><button value="close">Done</button></footer>
      </form>`;
    document.body.append(dialog);
    query("[data-share-copy]", dialog).addEventListener("click", async () => {
      await navigator.clipboard?.writeText(shareUrl);
      setText(query("[data-share-copy]", dialog), "Copied");
    });
    query("[data-share-revoke]", dialog).addEventListener("click", async () => {
      await this.api.delete(`/api/threads/${encodeURIComponent(threadId)}/shares/${encodeURIComponent(share.id)}`, { scope: "share-revoke" });
      chatState.share = null;
      dialog.close();
    });
    dialog.addEventListener("close", () => dialog.remove());
    dialog.showModal();
  }

  openAgentBuilderDialog() {
    const dialog = document.createElement("dialog");
    dialog.className = "chat-dialog agent-builder-dialog";
    const resources = (type) => chatState.capabilities.filter((item) => item.type === type && item.enabled);
    const resourceList = (type, emptyText) => {
      const items = resources(type);
      if (!items.length) return `
        <div class="agent-builder-empty">
          <p>${emptyText}</p>
          <button type="button" data-agent-builder-manage="${type}s">${type === "connector" ? "Manage connectors" : "Manage skills"}</button>
        </div>`;
      return items.map((item) => `
        <button type="button" class="agent-builder-resource" data-agent-builder-resource="${escapeHtml(`${item.type}:${item.id}`)}">
          <span aria-hidden="true">${icon(item.icon)}</span>
          <span><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(item.description || item.type)}</small></span>
        </button>`).join("");
    };
    dialog.innerHTML = `
      <div class="agent-builder-card">
        <header>
          <button type="button" class="agent-builder-back" data-agent-builder-back aria-label="Back" hidden>${icon("arrow-left")}</button>
          <div><h2 data-agent-builder-title>Create an agent</h2><p data-agent-builder-subtitle>Choose how to build your agent</p></div>
          <button type="button" class="agent-builder-close" data-dialog-close aria-label="Close">${icon("x")}</button>
        </header>
        <section data-agent-builder-view="choose">
          <div class="agent-builder-options">
            <button type="button" class="agent-builder-option" data-agent-builder-go="form" data-agent-builder-kind="agent">
              <span class="agent-builder-option-art is-scratch" aria-hidden="true">${icon("plus")}</span><strong>Start from scratch</strong><small>Describe your agent</small>
            </button>
            <button type="button" class="agent-builder-option" data-agent-builder-go="form" data-agent-builder-kind="workflow">
              <span class="agent-builder-option-art" aria-hidden="true">${icon("workflow")}</span><strong>Workflow Agent</strong><small>Build a multi-step workflow</small>
            </button>
            <button type="button" class="agent-builder-option" data-agent-builder-go="connectors">
              <span class="agent-builder-option-art" aria-hidden="true">${icon("plug")}</span><strong>Add a connector</strong><small>Connect your services</small>
            </button>
            <button type="button" class="agent-builder-option" data-agent-builder-go="skills">
              <span class="agent-builder-option-art" aria-hidden="true">${icon("blocks")}</span><strong>Choose a skill</strong><small>Start with an installed skill</small>
            </button>
          </div>
        </section>
        <form class="agent-builder-form" data-agent-builder-form data-agent-builder-view="form" hidden>
          <input type="hidden" name="agent_kind" value="agent" />
          <label><span>Agent name</span><input name="name" placeholder="e.g. Stock Analyzer" /></label>
          <label><span data-agent-builder-goal-label>What should this agent do?</span><textarea name="goal" rows="3" placeholder="Describe the agent's task..." required></textarea></label>
          <fieldset>
            <legend>Output format</legend>
            <input type="hidden" name="output_format" value="dashboard" />
            <div class="agent-builder-formats">
              <button type="button" data-agent-output="dashboard" class="is-selected" aria-pressed="true">Dashboard</button>
              <button type="button" data-agent-output="markdown" aria-pressed="false">Markdown</button>
              <button type="button" data-agent-output="html" aria-pressed="false">HTML</button>
            </div>
          </fieldset>
          <button type="submit" class="agent-builder-submit">Create</button>
        </form>
        <section class="agent-builder-resources" data-agent-builder-view="skills" hidden>
          <input type="search" data-agent-builder-search placeholder="Search skills..." aria-label="Search skills" />
          <div data-agent-builder-resource-list>${resourceList("skill", "No installed skills available.")}</div>
        </section>
        <section class="agent-builder-resources" data-agent-builder-view="connectors" hidden>
          <div data-agent-builder-resource-list>${resourceList("connector", "No connected connectors available.")}</div>
        </section>
      </div>`;
    document.body.append(dialog);
    const form = query("[data-agent-builder-form]", dialog);
    const showView = (view, kind = "agent") => {
      queryAll("[data-agent-builder-view]", dialog).forEach((section) => { section.hidden = section.dataset.agentBuilderView !== view; });
      const choice = view === "choose";
      const title = view === "skills" ? "Choose a skill" : view === "connectors" ? "Add a connector" : view === "form" ? (kind === "workflow" ? "Workflow Agent" : "Start from scratch") : "Create an agent";
      setText(query("[data-agent-builder-title]", dialog), title);
      setText(query("[data-agent-builder-subtitle]", dialog), choice ? "Choose how to build your agent" : "");
      query("[data-agent-builder-back]", dialog).hidden = choice;
      if (view === "form") {
        form.elements.agent_kind.value = kind;
        const workflow = kind === "workflow";
        setText(query("[data-agent-builder-goal-label]", dialog), workflow ? "What should this workflow do?" : "What should this agent do?");
        form.elements.goal.placeholder = workflow ? "Describe the steps and the final result..." : "Describe the agent's task...";
        setText(query(".agent-builder-submit", dialog), workflow ? "Create workflow agent" : "Create");
      }
    };
    const sendPrompt = async (prompt, resource = null, pendingAgent = {}) => {
      if (resource && !chatState.resourceRefs.some((item) => item.type === resource.type && item.id === resource.id)) {
        chatState.resourceRefs.push({ ...resourceReference(resource), name: resource.name });
      }
      if (!chatState.currentThreadId) {
        try {
          await this.createThread();
        } catch (error) {
          this.network(`Could not create thread: ${error.message}`, "error");
          return;
        }
      }
      const pendingKey = `taroai.pendingAgent.${chatState.currentThreadId}`;
      localStorage.setItem(pendingKey, JSON.stringify(pendingAgent));
      dialog.close();
      this.renderResourceChips();
      this.refs.input.value = prompt;
      this.saveDraft();
      this.syncComposer();
      if (!await this.sendThreadMessage(null, null, "autonomous")) localStorage.removeItem(pendingKey);
    };
    dialog.addEventListener("click", (event) => {
      const close = event.target.closest("[data-dialog-close]");
      if (close) return dialog.close();
      if (event.target.closest("[data-agent-builder-back]")) return showView("choose");
      const next = event.target.closest("[data-agent-builder-go]");
      if (next) return showView(next.dataset.agentBuilderGo, next.dataset.agentBuilderKind || "agent");
      const manage = event.target.closest("[data-agent-builder-manage]");
      if (manage) {
        dialog.close();
        window.location.hash = `brain/${manage.dataset.agentBuilderManage}`;
        return;
      }
      const output = event.target.closest("[data-agent-output]");
      if (output) {
        form.elements.output_format.value = output.dataset.agentOutput;
        queryAll("[data-agent-output]", dialog).forEach((button) => {
          const selected = button === output;
          button.classList.toggle("is-selected", selected);
          button.setAttribute("aria-pressed", String(selected));
        });
        return;
      }
      const resourceButton = event.target.closest("[data-agent-builder-resource]");
      if (!resourceButton) return;
      const resource = chatState.capabilities.find((item) => `${item.type}:${item.id}` === resourceButton.dataset.agentBuilderResource);
      if (!resource) return;
      const prompt = resource.type === "skill"
        ? `@${resource.name} Create an agent using this skill. Use it as the foundation and configure the agent based on the skill's capabilities.`
        : `Create an agent that uses my connected ${resource.name} integration. Build an agent that automates a useful workflow with this connector.`;
      sendPrompt(prompt, resource, {
        name: `${resource.name} Agent`,
        description: `Reusable agent built with ${resource.name}.`,
        format: "markdown",
        kind: "agent",
      });
    });
    query("[data-agent-builder-search]", dialog).addEventListener("input", (event) => {
      const search = event.target.value.trim().toLowerCase();
      queryAll("[data-agent-builder-resource]", query('[data-agent-builder-view="skills"]', dialog)).forEach((button) => {
        button.hidden = Boolean(search) && !button.textContent.toLowerCase().includes(search);
      });
    });
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const data = new FormData(form);
      const name = String(data.get("name") || "").trim();
      const goal = String(data.get("goal") || "").trim();
      const format = String(data.get("output_format") || "dashboard");
      const workflow = data.get("agent_kind") === "workflow";
      if (!goal) return;
      const prompt = workflow
        ? `${name ? `Create a workflow agent named "${name}" that ${goal}.` : `Create a workflow agent that ${goal}.`} Design a minimal multi-step workflow.spec DAG and define only necessary input fields; do not add pass-through input or output-only nodes. Save exactly one reviewable draft with the native agent.create_draft tool (app_kind: workflow); its required instructions field must contain the complete reusable workflow behavior. Do not execute the workflow during creation. The final output should be in ${format} format.`
        : `${name ? `Create an agent named "${name}" that ${goal}.` : `Create an agent that ${goal}.`} The agent output should be in ${format} format.`;
      sendPrompt(prompt, null, {
        name: name || (workflow ? "Workflow Agent" : "Reusable Agent"),
        description: goal,
        format,
        kind: workflow ? "workflow" : "agent",
      });
    });
    dialog.addEventListener("close", () => dialog.remove());
    dialog.showModal();
  }

  async persistPendingAgent(outcome) {
    const threadId = chatState.currentThreadId;
    if (!threadId) return;
    const key = `taroai.pendingAgent.${threadId}`;
    const raw = localStorage.getItem(key);
    if (!raw) return;
    const createdEvent = chatState.events.find(
      (event) => event.run_id === chatState.currentRunId && eventType(event) === "app_created",
    );
    localStorage.removeItem(key);
    if (createdEvent) {
      this.network(`Agent draft “${eventPayload(createdEvent).name || "Agent"}” created`, "success");
      window.dispatchEvent(new CustomEvent("taroai:agents-changed"));
      return;
    }
    if (outcome !== "complete") {
      this.network("Agent was not created because the builder run did not complete", "warning");
      return;
    }
    try {
      const pending = JSON.parse(raw);
      const extracted = await this.api.post(
        `/api/threads/${encodeURIComponent(threadId)}/extract-agent`,
        { name: pending.name },
        { scope: "agent-builder-extract" },
      );
      const version = {
        ...extracted.version,
        instructions: pending.description || extracted.version.instructions,
        output_contract: {
          ...(extracted.version.output_contract || {}),
          type: "string",
          format: pending.format || "markdown",
        },
        runtime_snapshot: {
          ...(extracted.version.runtime_snapshot || {}),
          autonomy_mode: pending.kind === "workflow" ? "workflow" : "autonomous",
        },
        change_note: "Created from the Chat agent builder",
      };
      const created = await this.api.post("/api/agents", {
        workspace_id: extracted.workspace_id,
        name: pending.name || extracted.name,
        description: pending.description || extracted.description,
        version,
      }, { scope: "agent-builder-create" });
      this.network(`Agent draft “${created.agent?.name || pending.name}” created`, "success");
      window.dispatchEvent(new CustomEvent("taroai:agents-changed"));
    } catch (error) {
      this.network(`Could not save agent draft: ${error.message}`, "error");
    }
  }

  openCreateAgentDialog() {
    if (!chatState.currentThreadId) {
      this.network("Start a thread before creating an agent", "warning");
      return;
    }
    const dialog = document.createElement("dialog");
    dialog.className = "chat-dialog agent-draft-dialog";
    const suggestedName = chatState.thread?.title && chatState.thread.title !== "New thread" ? chatState.thread.title : "Reusable agent";
    dialog.innerHTML = `
      <form class="chat-dialog-card" data-agent-draft-form>
        <header><div><small>From successful thread</small><h2>Create an agent</h2></div><button type="button" data-dialog-close aria-label="Close">${icon("x")}</button></header>
        <p>Review what should become reusable. The draft keeps this thread's model, skills, references, and output contract.</p>
        <label><span>Name</span><input name="name" value="${escapeHtml(suggestedName)}" required /></label>
        <label><span>Description</span><textarea name="description" rows="2" placeholder="What this agent reliably accomplishes"></textarea></label>
        <label><span>Instructions</span><textarea name="instructions" rows="5" placeholder="The repeatable approach, constraints, and verification expectations"></textarea></label>
        <label><span>Output format</span><input name="output_format" placeholder="Report, spreadsheet, artifact set…" /></label>
        <label class="agent-checkbox"><input type="checkbox" name="compile_playbook" /><span><strong>Compile deterministic Playbook</strong><small>Replay one successful sandbox action without model calls. The command remains reviewable before publishing.</small></span></label>
        <div class="agent-draft-bindings"><span>${chatState.resourceRefs.length || arrayFrom(chatState.thread?.resource_refs || []).length} references</span><span>${chatState.artifacts.length} artifacts</span><span>${chatState.selectedModel?.display_name || "Default model"}</span></div>
        <footer><button type="button" data-dialog-close>Cancel</button><button type="submit" class="primary">Create draft</button></footer>
      </form>`;
    document.body.append(dialog);
    queryAll("[data-dialog-close]", dialog).forEach((button) => button.addEventListener("click", () => dialog.close()));
    query("[data-agent-draft-form]", dialog).addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      const submit = query("[type='submit']", dialog);
      submit.disabled = true;
      submit.textContent = "Creating…";
      try {
        const extracted = await this.api.post(
          `/api/threads/${encodeURIComponent(chatState.currentThreadId)}/extract-agent`,
          {
            name: form.get("name"),
            compile_playbook: form.has("compile_playbook"),
          },
          { scope: "agent-extract" },
        );
        const version = {
          ...extracted.version,
          instructions: String(form.get("instructions") || "").trim() || extracted.version.instructions,
          output_contract: String(form.get("output_format") || "").trim()
            ? { ...(extracted.version.output_contract || {}), type: "string", format: String(form.get("output_format")).trim() }
            : extracted.version.output_contract,
          change_note: "Reviewed and created from a successful Chat thread",
        };
        const created = await this.api.post("/api/agents", {
          workspace_id: extracted.workspace_id,
          name: String(form.get("name") || extracted.name).trim(),
          description: String(form.get("description") || "").trim() || extracted.description,
          version,
        }, { scope: "agent-create-from-thread" });
        dialog.close();
        this.renderInlineNotice("Agent draft created", `${created.agent?.name || form.get("name")} is ready for review in Agents.`, "success");
        this.network("Agent draft created", "success");
      } catch (error) {
        submit.disabled = false;
        submit.textContent = "Create draft";
        this.network(`Could not create agent: ${error.message}`, "error");
      }
    });
    dialog.addEventListener("close", () => dialog.remove());
    dialog.showModal();
  }

  startVoiceInput(control) {
    if (window.taroaiSpeech?.toggleRecording) return window.taroaiSpeech.toggleRecording(control);
    const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!Recognition) {
      this.network("Voice input is not supported by this browser", "warning");
      return;
    }
    const recognition = new Recognition();
    recognition.interimResults = true;
    recognition.continuous = false;
    recognition.onstart = () => {
      control.classList.add("is-recording");
      this.network("Listening… click the microphone to finish", "active");
    };
    recognition.onresult = (event) => {
      const transcript = Array.from(event.results).map((result) => result[0].transcript).join("");
      this.refs.input.value = `${this.refs.input.value}${this.refs.input.value ? " " : ""}${transcript}`;
      this.syncComposer();
    };
    recognition.onerror = (event) => this.network(`Voice input stopped: ${event.error}`, "warning");
    recognition.onend = () => {
      control.classList.remove("is-recording");
      this.network("Transcript ready to edit", "success");
      this.refs.input.focus();
    };
    recognition.start();
  }

  async copyMessage(messageId, control = null) {
    const message = chatState.messages.find((item) => item.id === messageId);
    if (!message) return;
    await navigator.clipboard?.writeText(messageContent(message));
    if (control) {
      const markup = control.innerHTML;
      control.dataset.copied = "true";
      control.title = "Copied";
      control.setAttribute("aria-label", "Copied");
      control.innerHTML = '<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12 4 4L19 6"></path></svg>';
      window.setTimeout(() => {
        delete control.dataset.copied;
        control.title = "Copy message";
        control.setAttribute("aria-label", "Copy message");
        control.innerHTML = markup;
      }, 1200);
    }
    this.network("Message copied", "success");
    window.setTimeout(() => {
      if (this.refs.networkState?.dataset.state === "success" && this.refs.networkState.textContent === "Message copied") {
        this.network("Ready", "idle");
      }
    }, 1200);
  }

  restoreMessageToComposer(message) {
    const refs = arrayFrom(message.resource_refs || [], "items");
    chatState.browserProfile = refs.find((item) => item.type === "browser_profile") || null;
    chatState.resourceRefs = refs.filter((item) => item.type !== "browser_profile");
    chatState.uploads = arrayFrom(message.attachments || [], "items").map((attachment) => ({
      ...(typeof attachment === "string" ? {} : attachment),
      id: typeof attachment === "string" ? attachment : attachment.id || attachment.storage_object_id,
      status: "Ready",
      progress: 1,
    }));
    chatState.createIntent = message.kind === "workflow" ? "workflow" : message.kind === "agent" ? "agent" : null;
    if (this.refs.input) this.refs.input.value = messageContent(message);
    this.saveDraft();
    this.renderAll();
    this.refs.input?.focus();
  }

  retryMessage(messageId) {
    const message = chatState.messages.find((item) => item.id === messageId);
    if (!message) return;
    chatState.messages = chatState.messages.filter((item) => item.id !== messageId);
    this.restoreMessageToComposer(message);
    this.sendThreadMessage(null, chatState.running ? "queue" : "auto");
  }

  async retryRun(runId) {
    if (!runId || chatState.running) return;
    this.abortStream();
    chatState.running = true;
    this.network("Retrying run…", "loading");
    this.renderAll();
    try {
      await this.api.post(
        `/api/runs/${encodeURIComponent(runId)}/retry`,
        { reason_code: "operator_retry" },
        { scope: "run-retry" },
      );
      chatState.currentRunId = runId;
      publishChatContext();
      this.startEventStream();
    } catch (error) {
      chatState.running = false;
      this.network(`Retry failed: ${error.message}`, "error");
      this.renderAll();
    }
  }

  continueFailedRun(messageId = null) {
    const userMessages = [...chatState.messages].reverse().filter((item) => !isAssistant(item));
    const message = userMessages.find((item) => item.id === messageId)
      || userMessages.find((item) => dispatchStatus(item) === "failed")
      || userMessages[0];
    if (!message) return;
    this.restoreMessageToComposer(message);
    this.network("Request restored to the composer", "success");
  }

  speakMessage(messageId, control) {
    const message = chatState.messages.find((item) => item.id === messageId);
    if (!message) return;
    if (window.taroaiSpeech?.toggleReadAloud) return window.taroaiSpeech.toggleReadAloud(message, control);
    if (!window.speechSynthesis) return;
    if (speechSynthesis.speaking) {
      speechSynthesis.cancel();
      control.textContent = "Read aloud";
      return;
    }
    const utterance = new SpeechSynthesisUtterance(messageContent(message));
    utterance.onend = () => { control.textContent = "Read aloud"; };
    control.textContent = "Stop audio";
    speechSynthesis.speak(utterance);
  }

  summarizeMessage(messageId) {
    const message = chatState.messages.find((item) => item.id === messageId);
    if (!message) return;
    if (window.taroaiSpeech?.summarizeMessage) return window.taroaiSpeech.summarizeMessage(message);
  }

  applySuggestion(suggestion) {
    if (suggestion === "Turn this into an agent") return this.openCreateAgentDialog();
    this.refs.input.value = suggestion;
    this.syncComposer();
    this.refs.input.focus();
  }
}

let singleton = null;

export function createChatController() {
  if (singleton) return singleton;
  singleton = new ChatController();
  singleton.init();
  window.taroaiChat = singleton;
  return singleton;
}

export async function sendThreadMessage(content, deliveryMode = "auto", resourceRefs = [], attachments = [], mode = null) {
  if (!singleton) createChatController();
  chatState.resourceRefs = resourceRefs;
  chatState.uploads = attachments.map((attachment) => ({ ...attachment, id: attachment.id || attachment, status: "Ready", progress: 1 }));
  return singleton.sendThreadMessage(content, deliveryMode, mode);
}
