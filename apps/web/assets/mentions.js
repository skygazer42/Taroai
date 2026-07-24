const TYPE_ORDER = ["skill", "connector", "agent", "knowledge", "file", "browser_profile", "repository"];

function asArray(value) {
  if (Array.isArray(value)) return value;
  if (Array.isArray(value?.items)) return value.items;
  return [];
}

function candidateType(candidate, fallback) {
  const raw = fallback || candidate.type || candidate.kind || candidate.resource_type || "resource";
  return String(raw).replace(/_binding$/, "").replace(/_base$/, "").toLowerCase();
}

function normalizeOne(candidate, fallbackType) {
  const type = candidateType(candidate, fallbackType);
  const id = candidate.id || candidate.skill_id || candidate.connector_id || candidate.agent_id || candidate.knowledge_id || candidate.file_id || candidate.storage_object_id;
  if (!id) return null;
  return {
    type,
    id: String(id),
    version: candidate.version || candidate.installed_version
      ? String(candidate.version || candidate.installed_version)
      : null,
    name: candidate.name || candidate.display_name || candidate.title || String(id),
    description: candidate.description || candidate.summary || candidate.status || "",
    enabled: candidate.enabled !== false && candidate.status !== "disabled",
    is_default: Boolean(candidate.is_default),
    icon: {
      skill: "blocks",
      connector: "plug",
      agent: "bot",
      knowledge: "database",
      file: "file",
      browser_profile: "globe",
      repository: "code-xml",
    }[type] || "app-window",
  };
}

export function normalizeCapabilities(payload = {}) {
  const explicit = asArray(payload);
  const grouped = explicit.length
    ? explicit
    : [
        ...asArray(payload.skills).map((item) => ({ ...item, __type: "skill" })),
        ...asArray(payload.connectors).map((item) => ({ ...item, __type: "connector" })),
        ...asArray(payload.agents).map((item) => ({ ...item, __type: "agent" })),
        ...asArray(payload.knowledge).map((item) => ({ ...item, __type: "knowledge" })),
        ...asArray(payload.knowledge_bases).map((item) => ({ ...item, __type: "knowledge" })),
        ...asArray(payload.files).map((item) => ({ ...item, __type: "file" })),
        ...asArray(payload.browser_profiles).map((item) => ({ ...item, __type: "browser_profile" })),
        ...asArray(payload.repositories).map((item) => ({ ...item, __type: "repository" })),
      ];
  return grouped
    .map((candidate) => normalizeOne(candidate, candidate.__type))
    .filter(Boolean)
    .sort((left, right) => {
      const typeDelta = TYPE_ORDER.indexOf(left.type) - TYPE_ORDER.indexOf(right.type);
      return typeDelta || left.name.localeCompare(right.name);
    });
}

export function mentionQuery(text, cursor = text.length) {
  const beforeCursor = text.slice(0, cursor);
  const match = beforeCursor.match(/(?:^|\s)@([\w.-]*)$/u);
  if (!match) return null;
  const query = match[1] || "";
  return {
    query,
    start: beforeCursor.length - query.length - 1,
    end: cursor,
  };
}

export function filterMentionCandidates(candidates, query = "") {
  const normalized = query.trim().toLowerCase();
  return candidates
    .filter((candidate) => candidate.enabled)
    .filter((candidate) => {
      if (!normalized) return true;
      return `${candidate.name} ${candidate.id} ${candidate.type} ${candidate.description}`
        .toLowerCase()
        .includes(normalized);
    })
    .slice(0, 12);
}

export function resourceReference(candidate) {
  return {
    type: candidate.type,
    id: candidate.id,
    version: candidate.version ?? null,
  };
}

export function insertMention(text, cursor, query, candidate) {
  const visible = `@${candidate.name.replace(/\s+/g, "-")}`;
  const next = `${text.slice(0, query.start)}${visible} ${text.slice(query.end)}`;
  return { text: next, cursor: query.start + visible.length + 1 };
}
