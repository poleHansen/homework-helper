import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None


ROOT = Path(__file__).resolve().parent.parent
if load_dotenv:
    load_dotenv(ROOT / ".env")
DATA_DIR = Path(os.getenv("HOMEWORK_DATA_DIR", ROOT / "data"))
UPLOAD_DIR = DATA_DIR / "uploads"
DATABASE_PATH = DATA_DIR / "homework.db"

MODEL_NAME = os.getenv("HOMEWORK_MODEL", "")
API_KEY = os.getenv("HOMEWORK_API_KEY", "")
API_BASE = os.getenv("HOMEWORK_API_BASE", "")
GRADING_CONCURRENCY = max(1, int(os.getenv("GRADING_CONCURRENCY", "3")))

SUPPORTED_SUFFIXES = {".pdf", ".docx", ".txt", ".md", ".jpg", ".jpeg", ".png"}


def env_model_settings() -> dict[str, str]:
    return {
        "model": MODEL_NAME,
        "api_key": API_KEY,
        "api_base": API_BASE,
        "concurrency": str(GRADING_CONCURRENCY),
    }
