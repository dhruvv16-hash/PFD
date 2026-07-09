import os
from pathlib import Path
from pydantic import BaseModel, Field

# Base Directory
BASE_DIR = Path(__file__).resolve().parent.parent

# Native .env loader to avoid external dependency issues
env_file = BASE_DIR / ".env"
if env_file.is_file():
    with open(env_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                os.environ[k.strip()] = v.strip().strip("'\"")

class Settings(BaseModel):
    db_path: str = Field(
        default=os.getenv("DB_PATH", str(BASE_DIR / "db" / "platform.db")),
        description="Path to SQLite database"
    )
    log_dir: str = Field(
        default=os.getenv("LOG_DIR", str(BASE_DIR / "logs")),
        description="Directory for logs"
    )
    log_level: str = Field(
        default=os.getenv("LOG_LEVEL", "INFO"),
        description="Log level for console and file logging"
    )
    # Orchestrator retry configurations
    max_retries: int = Field(default=3, description="Maximum number of retries per agent")
    retry_base_delay: float = Field(default=1.0, description="Base delay in seconds for exponential backoff")
    retry_multiplier: float = Field(default=2.0, description="Multiplier for exponential backoff")

settings = Settings()

# Ensure directories exist
os.makedirs(os.path.dirname(settings.db_path), exist_ok=True)
os.makedirs(settings.log_dir, exist_ok=True)

