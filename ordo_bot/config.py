"""
Configuration handling for ordo-bot.
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

    ordo_ws_url: str = Field(
        default="wss://ordoscheduler.com/websocket",
        description="WebSocket URL of the Ordo instance",
    )
    ordo_token: str = Field(
        default="",
        description="API token from Ordo Settings (used for login_user)",
    )

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

    # LLM hang safeguards
    llm_timeout_sec: float = Field(
        default=90.0,
        description="Timeout seconds for a single LLM HTTP call",
    )
    llm_max_retries: int = Field(
        default=0,
        description=(
            "Extra LLM attempts after the first failure (0 = fail immediately; "
            "user/agent can retry). Only used for transient errors if > 0."
        ),
    )

    max_history_messages: int = Field(
        default=24,
        description="Max non-system messages in agent history (0 = unlimited)",
    )
    tool_result_max_chars: int = Field(
        default=2500,
        description="Max chars per tool result in LLM context",
    )
    chat_timeout_sec: float = Field(
        default=120.0,
        description="Max seconds for one user chat turn (0 = unlimited)",
    )

    bootstrap_mode: str = Field(
        default="standard",
        description="Startup guidance: minimal | standard | rich",
    )
    bootstrap_docs: bool = Field(
        default=True,
        description="Load a short live Ordo docs summary after login",
    )
    bootstrap_playbook_path: str = Field(
        default="",
        description="Optional path to playbook markdown (empty = prompts/bootstrap.md)",
    )
    bootstrap_extra_md: str = Field(
        default="",
        description="Optional extra markdown for rich mode (project guidance)",
    )

    frontend_host: str = Field(
        default="127.0.0.1",
        description="Host to bind the frontend WebSocket server",
    )
    frontend_port: int = Field(
        default=8765,
        description="Port for the frontend WebSocket server",
    )

    log_level: str = Field(
        default="INFO",
        description="Logging level (DEBUG, INFO, WARNING, ERROR)",
    )


def load_settings(config_path: Optional[Path] = None) -> Settings:
    if config_path and config_path.exists():
        import tomllib

        with open(config_path, "rb") as f:
            data = tomllib.load(f)
        return Settings(**data)

    return Settings()
