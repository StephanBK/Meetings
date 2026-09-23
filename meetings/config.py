"""Configuration from environment variables."""

import os
from pathlib import Path

# Load .env file if present
_env_path = Path(__file__).parent.parent / ".env"
if _env_path.exists():
    with open(_env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())

DATABASE_URL = os.environ.get("DATABASE_URL", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_WORKSPACE_ID = os.environ.get("ANTHROPIC_WORKSPACE_ID", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "claude-haiku-4-5-20251001")

# LLM pricing per million tokens
LLM_PRICING = {
    "claude-haiku-4-5-20251001": {"input": 1.00, "output": 5.00},
    "claude-sonnet-4-20250514": {"input": 3.00, "output": 15.00},
    "claude-opus-4-5-20251101": {"input": 15.00, "output": 75.00},
}

# Paths
PROJECT_ROOT = Path(__file__).parent.parent
SCHEMA_PATH = Path(__file__).parent / "schema.sql"
DATA_DIR = PROJECT_ROOT / "data"
CONFIG_DIR = PROJECT_ROOT / "config"
DOWNLOADS_DIR = PROJECT_ROOT / "downloads"
REPORTS_DIR = PROJECT_ROOT / "reports"

# Crawler settings
USER_AGENT = "MeetingsBot/0.1 (+https://github.com/StephanBK/Meetings)"
REQUEST_DELAY_SECONDS = 1.0

# Cost control
LLM_DAILY_CAP = float(os.environ.get("LLM_DAILY_CAP", "5.0"))  # Default $5/day
