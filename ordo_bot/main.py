"""
Main entry point for ordo-bot.

This is what runs when the user types:

    ordo-bot --config config.toml

Current behaviour (Phase 1):
  1. Load configuration
  2. Connect to Ordo with the API token
  3. Stay running so we can later add the agent + frontend WebSocket
  4. Shut down cleanly on Ctrl-C

Later phases will add:
  - LLM calls
  - The agent brain
  - The frontend WebSocket server that CLI / web UI connect to
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path

from ordo_bot import __version__
from ordo_bot.config import load_settings
from ordo_bot.ordo_client import OrdoClient


def setup_logging(level: str) -> None:
    """Configure basic logging so we can see what the bot is doing."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )


async def run_bot(settings, smoke: bool = False) -> None:
    """
    Core async loop.

    - Creates the Ordo client
    - Connects and logs in
    - Optionally runs a tiny smoke command
    - Then waits until the user presses Ctrl-C (or the process is killed)
    """
    log = logging.getLogger("ordo_bot")

    if not settings.ordo_token:
        log.error(
            "No Ordo token configured. "
            "Set ordo_token in config.toml or the ORDO_BOT_ORDO_TOKEN environment variable."
        )
        sys.exit(1)

    client = OrdoClient(
        url=settings.ordo_ws_url,
        token=settings.ordo_token,
    )

    # Optional: log every message that arrives from Ordo (useful while developing)
    async def on_ordo_message(data: dict) -> None:
        # Keep this quiet for normal use; switch to DEBUG to see everything
        log.debug("Ordo message: %s", data)

    client.on_message = on_ordo_message

    try:
        log.info("Connecting to Ordo at %s ...", settings.ordo_ws_url)
        await client.connect()
        log.info("Connected and logged in to Ordo")

        # ------------------------------------------------------------------
        # Optional smoke test – one real command so we know the client works
        # ------------------------------------------------------------------
        if smoke:
            log.info("Running smoke test: get_documentation(section='overview')")
            try:
                reply = await client.get_documentation(
                    section="overview", format="markdown"
                )
                # Print a short summary so the user can see it succeeded
                success = reply.get("success")
                log.info("Smoke test reply success=%s", success)
                # Show a small snippet of the documentation if present
                doc = reply.get("documentation") or reply.get("content") or ""
                if isinstance(doc, str) and doc:
                    snippet = doc[:300].replace("\n", " ")
                    log.info(
                        "Documentation snippet: %s%s",
                        snippet,
                        "..." if len(doc) > 300 else "",
                    )
            except Exception as e:
                log.error("Smoke test failed: %s", e)

        # ------------------------------------------------------------------
        # Keep the process alive.
        # Later this is where we will start:
        #   - the frontend WebSocket server
        #   - the agent loop
        # ------------------------------------------------------------------
        log.info("Bot is running. Press Ctrl-C to stop.")
        # Wait forever (or until cancelled by the signal handler)
        await asyncio.Future()

    except asyncio.CancelledError:
        log.info("Shutdown requested")
    except Exception as e:
        log.error("Fatal error: %s", e)
        raise
    finally:
        log.info("Closing Ordo connection ...")
        await client.close()
        log.info("Bye")


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
        "--smoke",
        action="store_true",
        help="After login, run a quick test command and print the result",
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
    log.info(
        "LLM endpoint: %s  (model: %s)", settings.llm_base_url, settings.llm_model
    )
    log.info("Ordo URL: %s", settings.ordo_ws_url)

    # ------------------------------------------------------------------
    # Run the async bot and handle Ctrl-C cleanly
    # ------------------------------------------------------------------
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    main_task = loop.create_task(run_bot(settings, smoke=args.smoke))

    # Make Ctrl-C cancel the main task so we shut down gracefully
    def _handle_signal() -> None:
        log.info("Received stop signal")
        main_task.cancel()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_signal)
        except NotImplementedError:
            # Windows does not support add_signal_handler the same way
            pass

    try:
        loop.run_until_complete(main_task)
    except asyncio.CancelledError:
        pass
    finally:
        loop.close()


if __name__ == "__main__":
    main()
