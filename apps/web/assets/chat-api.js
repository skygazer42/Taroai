const JSON_HEADERS = { "Content-Type": "application/json" };

function readValue(selector, storageKey, fallback = "") {
  const control = document.querySelector(selector);
  return (control?.value || localStorage.getItem(storageKey) || fallback).trim();
}

function responseBody(text) {
  if (!text) return {};
  try {
    return JSON.parse(text);
  } catch {
    return { message: text };
  }
}

function apiError(response, body) {
  const detail = body?.detail || body?.message || response.statusText || "Request failed";
  const error = new Error(`${response.status} ${detail}`);
  error.status = response.status;
  error.body = body;
  return error;
}

function notifyAuthExpired(status) {
  if (status !== 401) return;
  window.dispatchEvent(new CustomEvent("taroai:auth-expired"));
}

function fileAsBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error || new Error("Unable to read file"));
    reader.onload = () => resolve(String(reader.result || "").split(",").pop() || "");
    reader.readAsDataURL(file);
  });
}

function parseSseFrame(frame) {
  const parsed = { event: "message", id: "", data: "" };
  for (const line of frame.split(/\r?\n/)) {
    if (!line || line.startsWith(":")) continue;
    const separator = line.indexOf(":");
    const field = separator === -1 ? line : line.slice(0, separator);
    let value = separator === -1 ? "" : line.slice(separator + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") parsed.event = value;
    if (field === "id") parsed.id = value;
    if (field === "data") parsed.data += `${parsed.data ? "\n" : ""}${value}`;
  }
  if (parsed.data) {
    try {
      parsed.data = JSON.parse(parsed.data);
    } catch {
      parsed.data = { message: parsed.data };
    }
  } else {
    parsed.data = {};
  }
  return parsed;
}

export class ChatApi {
  constructor() {
    this.requestSerial = 0;
  }

  settings() {
    const apiBase = readValue("#api-base", "taroai.apiBase", window.location.origin).replace(/\/+$/, "");
    return {
      apiBase,
      tenantId: readValue("#tenant-id", "taroai.tenantId", "tenant_acme"),
      userId: readValue("#user-id", "taroai.userId", "user_owner"),
      workspaceId: readValue("#workspace-id", "taroai.workspaceId", "workspace_main"),
      accessToken: sessionStorage.getItem("taroai.accessToken") || localStorage.getItem("taroai.accessToken") || "",
    };
  }

  headers(extra = {}, includeJson = true) {
    const settings = this.settings();
    const headers = {
      ...(includeJson ? JSON_HEADERS : {}),
      "X-Tenant-ID": settings.tenantId,
      "X-User-ID": settings.userId,
      "X-Workspace-ID": settings.workspaceId,
      ...extra,
    };
    if (settings.accessToken) headers.Authorization = `Bearer ${settings.accessToken}`;
    return headers;
  }

  idempotencyKey(scope = "chat") {
    if (globalThis.crypto?.randomUUID) return `${scope}:${crypto.randomUUID()}`;
    this.requestSerial += 1;
    return `${scope}:${Date.now()}:${this.requestSerial}`;
  }

  async request(path, options = {}) {
    const { apiBase } = this.settings();
    const response = await fetch(`${apiBase}${path}`, {
      ...options,
      headers: this.headers(options.headers || {}, options.body !== undefined),
    });
    const text = await response.text();
    const body = responseBody(text);
    if (!response.ok) {
      notifyAuthExpired(response.status);
      throw apiError(response, body);
    }
    return body;
  }

  get(path, options = {}) {
    return this.request(path, { ...options, method: "GET" });
  }

  post(path, body = {}, options = {}) {
    const { scope = "post", ...requestOptions } = options;
    return this.request(path, {
      ...requestOptions,
      method: "POST",
      body: JSON.stringify(body),
      headers: {
        "Idempotency-Key": this.idempotencyKey(scope),
        ...(requestOptions.headers || {}),
      },
    });
  }

  patch(path, body = {}, options = {}) {
    const { scope = "patch", ...requestOptions } = options;
    return this.request(path, {
      ...requestOptions,
      method: "PATCH",
      body: JSON.stringify(body),
      headers: {
        "Idempotency-Key": this.idempotencyKey(scope),
        ...(requestOptions.headers || {}),
      },
    });
  }

  delete(path, options = {}) {
    const { scope = "delete", ...requestOptions } = options;
    return this.request(path, {
      ...requestOptions,
      method: "DELETE",
      headers: {
        "Idempotency-Key": this.idempotencyKey(scope),
        ...(requestOptions.headers || {}),
      },
    });
  }

  async blob(path) {
    const { apiBase } = this.settings();
    const response = await fetch(`${apiBase}${path}`, {
      method: "GET",
      headers: this.headers({}, false),
    });
    if (!response.ok) {
      const body = responseBody(await response.text());
      notifyAuthExpired(response.status);
      throw apiError(response, body);
    }
    return response.blob();
  }

  async upload(file, onProgress = () => {}) {
    onProgress(0.08, "Reading");
    const contentBase64 = await fileAsBase64(file);
    onProgress(0.42, "Scanning");
    const body = await this.post(
      "/api/uploads",
      {
        workspace_id: this.settings().workspaceId,
        filename: file.name,
        content_type: file.type || "application/octet-stream",
        content_base64: contentBase64,
      },
      { scope: "upload" },
    );
    onProgress(1, "Ready");
    return { ...(body.storage_object || body), upload: body.upload || null };
  }

  async streamThreadEvents(threadId, options = {}) {
    const { apiBase } = this.settings();
    const afterSequence = Number(options.afterSequence || 0);
    const query = new URLSearchParams({ after_sequence: String(afterSequence), follow: "true" });
    const response = await fetch(`${apiBase}/api/threads/${encodeURIComponent(threadId)}/events?${query}`, {
      method: "GET",
      headers: this.headers(
        {
          Accept: "text/event-stream",
          "Cache-Control": "no-cache",
          ...(afterSequence ? { "Last-Event-ID": String(afterSequence) } : {}),
        },
        false,
      ),
      signal: options.signal,
    });
    if (!response.ok) {
      const body = responseBody(await response.text());
      notifyAuthExpired(response.status);
      throw apiError(response, body);
    }
    if (!response.body) throw new Error("This browser cannot read the event stream");

    options.onStatus?.("connected");
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let framesSinceYield = 0;
    try {
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let boundary = buffer.search(/\r?\n\r?\n/);
        while (boundary !== -1) {
          const frame = buffer.slice(0, boundary);
          const separatorLength = buffer.slice(boundary).startsWith("\r\n\r\n") ? 4 : 2;
          buffer = buffer.slice(boundary + separatorLength);
          if (frame.trim()) {
            options.onEvent?.(parseSseFrame(frame));
            framesSinceYield += 1;
            if (framesSinceYield === 24) {
              framesSinceYield = 0;
              await new Promise((resolve) => setTimeout(resolve, 0));
            }
          }
          boundary = buffer.search(/\r?\n\r?\n/);
        }
      }
    } finally {
      reader.releaseLock();
      options.onStatus?.("disconnected");
    }
  }
}

export const chatApi = new ChatApi();
