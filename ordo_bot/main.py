"""
Main entry point for ordo-bot.

For now this is only a skeleton. In the next steps we will fill in:
  - connecting to Ordo
  - starting the frontend WebSocket server
  - the agent loop

The goal of this file is just to load configuration and show
that the package can be started.
"""

import argparse
import logging
import sys
from pathlib import Path

from ordo_bot import __version__
from ordo_bot.config import load_settings


def setup_logging(level: str) -> None:
    """Configure basic logging so we can see what the bot is doing."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )


def main() -> None:
    """
    Entry point when the user runs `ordo-bot` or `python -m ordo_bot`.
    """
    parser = argparse.ArgumentParser(
        description="Ordo Bot – connect your own LLM to an Ordo instance"
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config.toml"),
        help="Path to the TOML config file (default: config.toml)",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"ordo-bot {__version__}",
    )
    args = parser.parse_args()

    # Load settings (from file + environment + defaults)
    settings = load_settings(args.config)

    setup_logging(settings.log_level)
    log = logging.getLogger("ordo_bot")

    log.info("ordo-bot %s starting", __version__)
    log.info("Frontend will listen on %s:%s", settings.frontend_host, settings.frontend_port)
    log.info("LLM endpoint: %s  (model: %s)", settings.llm_base_url, settings.llm_model)
    log.info("Ordo URL: %s", settings.ordo_ws_url)

    # ------------------------------------------------------------------
    # TODO (next steps):
    #   1. Create OrdoClient and connect
    #   2. Start the frontend WebSocket server
    #   3. Run the agent loop
    # ------------------------------------------------------------------

    log.info("Skeleton started successfully. Real logic comes next.")
    # For now we just exit so the user can verify the package works.
    # Later this will become an asyncio.run(...) that keeps running.


if __name__ == "__main__":
    main()
