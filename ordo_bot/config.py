"""
Configuration handling for ordo-bot.

We use pydantic-settings so configuration can come from:
  - a TOML file (config.toml)
  - environment variables
  - defaults defined in this file

This keeps secrets (API keys) out of the code and makes it easy
for users to change settings without editing Python.
"""

from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Main settings class.

    All fields have sensible defaults so the bot can start
    even with a minimal config file.
    """

    model_config = SettingsConfigDict(
        env_prefix="ORDO_BOT_",          # environment variables start with ORDO_BOT_
        env_file=".env",                 # also load from a .env file if present
        env_file_encoding="utf-8",
        extra="ignore",                  # ignore unknown fields instead of crashing
    )

    # ------------------------------------------------------------------
    # Ordo connection
    # ------------------------------------------------------------------
    ordo_ws_url: str = Field(
        default="ws://localhost:8080/ws",
        description="WebSocket URL of the Ordo instance",
    )
    ordo_username: str = Field(
        default="",
        description="Username for Ordo login",
    )
    ordo_password: str = Field(
        default="",
        description="Password for Ordo login",
    )

    # ------------------------------------------------------------------
    # LLM (OpenAI-compatible)
    # ------------------------------------------------------------------
    llm_base_url: str = Field(
        default="http://localhost:11434/v1",  # default = local Ollama
        description="Base URL of the OpenAI-compatible API",
    )
    llm_api_key: str = Field(
        default="ollama",                     # Ollama ignores the key, but some clients require one
        description="API key for the LLM provider",
    )
    llm_model: str = Field(
        default="llama3.2",
        description="Model name to use",
    )

    # ------------------------------------------------------------------
    # Frontend WebSocket server (the API that CLI / web UI connect to)
    # ------------------------------------------------------------------
    frontend_host: str = Field(
        default="127.0.0.1",
        description="Host to bind the frontend WebSocket server",
    )
    frontend_port: int = Field(
        default=8765,
        description="Port for the frontend WebSocket server",
    )

    # ------------------------------------------------------------------
    # Misc
    # ------------------------------------------------------------------
    log_level: str = Field(
        default="INFO",
        description="Logging level (DEBUG, INFO, WARNING, ERROR)",
    )


def load_settings(config_path: Optional[Path] = None) -> Settings:
    """
    Load settings, optionally from a TOML file.

    If a config_path is given and the file exists, we read it.
    Environment variables and defaults still apply on top.
    """
    if config_path and config_path.exists():
        # pydantic-settings can load TOML via model_validate
        import tomllib  # Python 3.11+ standard library

        with open(config_path, "rb") as f:
            data = tomllib.load(f)
        return Settings(**data)

    return Settings()
