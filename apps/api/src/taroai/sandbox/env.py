import re


SANDBOX_ENV_NAME_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def invalid_sandbox_env_names(env: dict[str, str]) -> list[str]:
    return sorted(
        name
        for name in env
        if SANDBOX_ENV_NAME_PATTERN.fullmatch(name) is None
    )
