import os
from pathlib import Path
from dotenv import load_dotenv

# Automatically load .env from the project directory
PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env")

class Settings:
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "").strip()
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
    GEMINI_MODEL_FALLBACKS: str = os.getenv(
        "GEMINI_MODEL_FALLBACKS", "gemini-2.5-flash,gemini-2.0-flash,gemini-1.5-flash"
    ).strip()
    TAVILY_API_KEY: str = os.getenv("TAVILY_API_KEY", "").strip()
    GITHUB_TOKEN: str = os.getenv("GITHUB_TOKEN", "").strip()
    
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
