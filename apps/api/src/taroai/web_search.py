import json
from collections.abc import Callable
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from taroai.tool_gateway import (
    ToolExecutionError,
    ToolGateway,
    ToolGatewayRequest,
    ToolPolicy,
    ToolResult,
    ToolRiskLevel,
)
from taroai.domain import utc_now


TAVILY_SEARCH_URL = "https://api.tavily.com/search"
TAVILY_EXTRACT_URL = "https://api.tavily.com/extract"


def _safe_text(value: Any, limit: int) -> str:
    return (
        str(value)
        .replace("\x00", "")
        .encode("utf-8", errors="replace")
        .decode("utf-8")[:limit]
    )


def register_web_search_tool_handler(
    gateway: ToolGateway,
    api_key: str,
    timeout_seconds: int = 15,
    requester: Callable[..., Any] = urlopen,
) -> None:
    gateway.register_tool(
        ToolPolicy(
            tool_name="web.search",
            description=(
                "Search the live web for current or externally verifiable information. "
                "Do not use this tool when the request is missing a required location or "
                "other essential value; ask the user first and never infer a location from "
                "a timezone. "
                "Returns compact source titles, URLs, excerpts, and publication dates. "
                "Use include_domains for a requested site or official source, and prefer "
                "primary sources over aggregators. For current or latest facts, use the "
                "current date stated in this tool description and set time_range to year."
            ),
            risk_level=ToolRiskLevel.LOW,
            input_schema={
                "type": "object",
                "required": ["query"],
                "properties": {
                    "query": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 1000,
                        "description": (
                            "For current/latest facts, include the current date stated in "
                            "the tool description."
                        ),
                    },
                    "topic": {
                        "type": "string",
                        "enum": ["general", "news", "finance"],
                    },
                    "time_range": {
                        "type": "string",
                        "enum": ["day", "week", "month", "year"],
                    },
                    "include_domains": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                        "maxItems": 10,
                        "description": "Hostnames that search results must come from.",
                    },
                },
                "additionalProperties": False,
            },
        ),
        lambda request: _search(api_key, timeout_seconds, requester, request),
    )
    gateway.register_tool(
        ToolPolicy(
            tool_name="web.fetch",
            description=(
                "Read one web page through Tavily Extract. Use only when search excerpts "
                "do not directly support the requested claim, materially conflict, or the "
                "user asks for page-level detail. An explicit request to open, read, or "
                "fetch a page always requires this tool. Prefer a canonical current "
                "download, status, or release index over a history or archive page."
            ),
            risk_level=ToolRiskLevel.LOW,
            input_schema={
                "type": "object",
                "required": ["url"],
                "properties": {
                    "url": {
                        "type": "string",
                        "minLength": 8,
                        "maxLength": 2000,
                    },
                    "query": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 1000,
                    },
                },
                "additionalProperties": False,
            },
        ),
        lambda request: _fetch(api_key, timeout_seconds, requester, request),
    )


def _search(
    api_key: str,
    timeout_seconds: int,
    requester: Callable[..., Any],
    request: ToolGatewayRequest,
) -> ToolResult:
    query = str(request.tool_input["query"]).strip()
    if not query:
        raise ToolExecutionError("web search query cannot be blank")
    payload: dict[str, Any] = {
        "query": query,
        "max_results": 5,
        "topic": str(request.tool_input.get("topic", "general")),
        "search_depth": "advanced",
        "include_answer": False,
        "include_raw_content": False,
        "include_images": False,
    }
    if time_range := request.tool_input.get("time_range"):
        payload["time_range"] = str(time_range)
    if include_domains := request.tool_input.get("include_domains"):
        payload["include_domains"] = [str(item) for item in include_domains]
    http_request = Request(
        TAVILY_SEARCH_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with requester(http_request, timeout=timeout_seconds) as response:
            body = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        raise ToolExecutionError(
            f"web search request failed with HTTP {error.code}"
        ) from error
    except (URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ToolExecutionError("web search provider is unavailable") from error
    if not isinstance(body, dict) or not isinstance(body.get("results"), list):
        raise ToolExecutionError("web search provider returned an invalid response")
    allowed_domains = {
        domain.strip().lower().rstrip(".")
        for domain in payload.get("include_domains", [])
        if domain.strip()
    }
    results = []
    for item in body["results"]:
        if not isinstance(item, dict) or not item.get("url"):
            continue
        hostname = (urlparse(str(item["url"])).hostname or "").lower().rstrip(".")
        if allowed_domains and not any(
            hostname == domain or hostname.endswith(f".{domain}")
            for domain in allowed_domains
        ):
            continue
        result = {
            "title": _safe_text(item.get("title") or item["url"], 500),
            "url": _safe_text(item["url"], 2000),
            "content": _safe_text(item.get("content") or "", 2000),
        }
        if item.get("published_date"):
            result["published_date"] = _safe_text(item["published_date"], 100)
        results.append(result)
        if len(results) == payload["max_results"]:
            break
    return ToolResult(
        tool_name="web.search",
        output={
            "query": query,
            "topic": payload["topic"],
            "time_range": payload.get("time_range"),
            "include_domains": payload.get("include_domains", []),
            "searched_at": utc_now().isoformat(),
            "results": results,
        },
    )


def _fetch(
    api_key: str,
    timeout_seconds: int,
    requester: Callable[..., Any],
    request: ToolGatewayRequest,
) -> ToolResult:
    url = str(request.tool_input["url"]).strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ToolExecutionError("web fetch URL must use HTTP or HTTPS")
    payload: dict[str, Any] = {
        "urls": [url],
        "extract_depth": "basic",
        "include_images": False,
        "format": "markdown",
    }
    http_request = Request(
        TAVILY_EXTRACT_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with requester(http_request, timeout=timeout_seconds) as response:
            body = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        raise ToolExecutionError(
            f"web fetch request failed with HTTP {error.code}"
        ) from error
    except (URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ToolExecutionError("web fetch provider is unavailable") from error
    results = body.get("results") if isinstance(body, dict) else None
    if not isinstance(results, list) or not results or not isinstance(results[0], dict):
        raise ToolExecutionError("web page could not be extracted")
    result = results[0]
    return ToolResult(
        tool_name="web.fetch",
        output={
            "url": _safe_text(result.get("url") or url, 2000),
            "content": _safe_text(result.get("raw_content") or "", 12_000),
            "fetched_at": utc_now().isoformat(),
        },
    )
