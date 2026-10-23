import os
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent


def _sanitize_env_value(value: object) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\r", "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1].strip()
    return text.replace("\r", "").strip()


def load_project_env(env_path: str | Path | None = None, *, override: bool = True) -> Path:
    project_env = Path(env_path).resolve() if env_path else PROJECT_ROOT / ".env"
    if not project_env.is_absolute():
        project_env = (PROJECT_ROOT / project_env).resolve()
    load_dotenv(dotenv_path=str(project_env), override=override, encoding="utf-8-sig")

    for key in [
        "GROQ_API_KEY",
        "GEMINI_API_KEY",
        "TAVILY_API_KEY",
        "GROQ_MODEL",
        "GROQ_MODEL_FALLBACKS",
        "GEMINI_MODEL",
        "LLM_PROVIDERS",
        "GEMINI_MODEL_FALLBACKS",
        "GITHUB_TOKEN",
    ]:
        value = _sanitize_env_value(os.getenv(key, ""))
        os.environ[key] = value

    providers = _sanitize_env_value(os.getenv("LLM_PROVIDERS", "groq,gemini"))
    providers = ",".join(part.strip() for part in providers.split(",") if part.strip())
    os.environ["LLM_PROVIDERS"] = providers
    return project_env


load_project_env(PROJECT_ROOT / ".env", override=True)


class Settings:
    LLM_PROVIDERS: str = _sanitize_env_value(
        os.getenv("LLM_PROVIDERS", "groq,gemini")
    ) or "groq,gemini"
    GROQ_API_KEY: str = _sanitize_env_value(os.getenv("GROQ_API_KEY", ""))
    GROQ_MODEL: str = _sanitize_env_value(
        os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
    ) or "openai/gpt-oss-120b"
    GROQ_MODEL_FALLBACKS: str = _sanitize_env_value(
        os.getenv("GROQ_MODEL_FALLBACKS", "openai/gpt-oss-20b")
    ) or "openai/gpt-oss-20b"
    GEMINI_API_KEY: str = _sanitize_env_value(os.getenv("GEMINI_API_KEY", ""))
    GEMINI_MODEL: str = _sanitize_env_value(
        os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    ) or "gemini-2.5-flash"
    GEMINI_MODEL_FALLBACKS: str = _sanitize_env_value(
        os.getenv("GEMINI_MODEL_FALLBACKS", "gemini-2.5-flash,gemini-2.0-flash")
    ) or "gemini-2.5-flash,gemini-2.0-flash"
    TAVILY_API_KEY: str = _sanitize_env_value(os.getenv("TAVILY_API_KEY", ""))
    GITHUB_TOKEN: str = _sanitize_env_value(os.getenv("GITHUB_TOKEN", ""))

    LLM_CONCURRENCY: int = int(os.getenv("LLM_CONCURRENCY", "2"))
    VERIFIER_CONCURRENCY: int = int(os.getenv("VERIFIER_CONCURRENCY", "2"))
    MAX_LLM_RETRIES: int = int(os.getenv("MAX_LLM_RETRIES", "3"))
    MAX_RESEARCH_ROUNDS: int = int(os.getenv("MAX_RESEARCH_ROUNDS", "2"))
    REQUEST_TIMEOUT: float = float(os.getenv("REQUEST_TIMEOUT", "8.0"))
    OUTPUT_DIR: Path = PROJECT_ROOT / "output"
    CACHE_DIR: Path = PROJECT_ROOT / "cache"
    LLM_CACHE_DIR: Path = PROJECT_ROOT / "cache" / "llm"


settings = Settings()
settings.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
settings.CACHE_DIR.mkdir(parents=True, exist_ok=True)
settings.LLM_CACHE_DIR.mkdir(parents=True, exist_ok=True)
