"""
Configuration handling for ordo-bot.

We use pydantic-settings so configuration can come from:
  - a TOML file (config.toml)
  - environment variables
  - defaults defined in this file
"""

from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Main settings class."""

    model_config = SettingsConfigDict(
        env_prefix="ORDO_BOT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # Ordo connection
    # ------------------------------------------------------------------
    ordo_ws_url: str = Field(
        default="wss://ordoscheduler.com/websocket",
        description="WebSocket URL of the Ordo instance",
    )
    ordo_token: str = Field(
        default="",
        description="API token from Ordo Settings (used for login_user)",
    )

    # ------------------------------------------------------------------
    # LLM (OpenAI-compatible)
    # ------------------------------------------------------------------
    llm_base_url: str = Field(
        default="http://localhost:11434/v1",
        description="Base URL of the OpenAI-compatible API",
    )
    llm_api_key: str = Field(
        default="ollama",
        description="API key for the LLM provider",
    )
    llm_model: str = Field(
        default="llama3.2",
        description="Model name to use",
    )

    # ------------------------------------------------------------------
    # Agent context hygiene
    # ------------------------------------------------------------------
    # Max non-system messages kept in history. 0 = unlimited (large-context models).
    max_history_messages: int = Field(
        default=24,
        description="Max non-system messages in agent history (0 = unlimited)",
    )
    # Max characters of a single tool result stored for the LLM.
    tool_result_max_chars: int = Field(
        default=2500,
        description="Max chars per tool result in LLM context",
    )
    # Fetch get_documentation summary once after Ordo login.
    bootstrap_docs: bool = Field(
        default=True,
        description="Load a short Ordo docs summary into context after login",
    )

    # ------------------------------------------------------------------
    # Frontend WebSocket server
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
    """Load settings, optionally from a TOML file."""
    if config_path and config_path.exists():
        import tomllib

        with open(config_path, "rb") as f:
            data = tomllib.load(f)
        return Settings(**data)

    return Settings()
