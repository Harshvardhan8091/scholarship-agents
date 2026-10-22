from __future__ import annotations

import os
from pathlib import Path

from config import PROJECT_ROOT, load_project_env

VARIABLES = [
    "GROQ_API_KEY",
    "GEMINI_API_KEY",
    "TAVILY_API_KEY",
    "GROQ_MODEL",
    "GEMINI_MODEL",
    "LLM_PROVIDERS",
]


def _warn_flag(raw_text: str, name: str) -> bool:
    if not raw_text:
        return False
    candidate = raw_text.strip()
    if candidate.startswith(("'", '"')) or candidate.endswith(("'", '"')):
        return True
    return candidate != raw_text


def _check_raw_var_format(env_path: Path, name: str) -> str | None:
    if not env_path.exists():
        return None
    try:
        text = env_path.read_text(encoding="utf-8-sig")
    except Exception:
        return None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith(f"{name}="):
            raw_value = stripped.split("=", 1)[1]
            if _warn_flag(raw_value, name):
                return "warning: leading/trailing spaces or quotes"
    return None


def _provider_warnings() -> list[str]:
    warnings: list[str] = []
    providers = os.getenv("LLM_PROVIDERS", "")
    if not providers:
        return warnings
    for provider in [part.strip().lower() for part in providers.split(",") if part.strip()]:
        key_name = {
            "groq": "GROQ_API_KEY",
            "gemini": "GEMINI_API_KEY",
            "tavily": "TAVILY_API_KEY",
        }.get(provider)
        if key_name and not os.getenv(key_name, ""):
            warnings.append(f"warning: LLM_PROVIDERS includes '{provider}' but {key_name} is missing")
    return warnings


def main() -> None:
    env_path = PROJECT_ROOT / ".env"
    load_project_env(env_path, override=True)

    print(f"ENV_PATH={env_path.resolve()}")
    print(f"ENV_EXISTS={env_path.exists()}")
    env_txt_exists = (PROJECT_ROOT / ".env.txt").exists()
    env_example_exists = (PROJECT_ROOT / ".env.example").exists()
    print(f"ENV_ALT_ONLY={env_txt_exists or (not env_path.exists() and env_example_exists)}")

    warnings: list[str] = []
    for name in VARIABLES:
        value = os.getenv(name, "")
        set_state = bool(value)
        print(f"{name} set={str(set_state).title()} length={len(value)}")

        raw_issue = _check_raw_var_format(env_path, name)
        if raw_issue:
            warnings.append(f"warning: {name} {raw_issue}")

        if name == "GROQ_API_KEY" and value and not value.startswith("gsk_"):
            warnings.append("warning: GROQ_API_KEY does not start with 'gsk_'")

    warnings.extend(_provider_warnings())
    for warning in warnings:
        print(warning)


if __name__ == "__main__":
    main()
