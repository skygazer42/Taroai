import re


REDACTED_PROVIDER_ERROR_VALUE = "[REDACTED]"
MAX_PROVIDER_ERROR_DETAIL_LENGTH = 2000
SENSITIVE_PROVIDER_ERROR_FIELD_PATTERN = re.compile(
    r'("?(?:access[_-]?key|access[_-]?token|api[_-]?key|authorization|'
    r'bearer[_-]?token|credential|password|secret|token)"?\s*:\s*)("[^"]*"|[^,}\s]+)',
    re.IGNORECASE,
)
SECRET_LIKE_PROVIDER_ERROR_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9][A-Za-z0-9_-]{7,}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}\b"),
)


def redact_provider_error_detail(detail: str, api_key: str = "") -> str:
    redacted = detail
    if api_key:
        redacted = redacted.replace(api_key, REDACTED_PROVIDER_ERROR_VALUE)
    redacted = SENSITIVE_PROVIDER_ERROR_FIELD_PATTERN.sub(
        _redact_sensitive_provider_error_field,
        redacted,
    )
    for pattern in SECRET_LIKE_PROVIDER_ERROR_PATTERNS:
        redacted = pattern.sub(REDACTED_PROVIDER_ERROR_VALUE, redacted)
    if len(redacted) > MAX_PROVIDER_ERROR_DETAIL_LENGTH:
        redacted = f"{redacted[:MAX_PROVIDER_ERROR_DETAIL_LENGTH]}...[truncated]"
    return redacted


def _redact_sensitive_provider_error_field(match: re.Match[str]) -> str:
    value = match.group(2)
    if value.startswith('"'):
        replacement = f'"{REDACTED_PROVIDER_ERROR_VALUE}"'
    else:
        replacement = REDACTED_PROVIDER_ERROR_VALUE
    return f"{match.group(1)}{replacement}"
