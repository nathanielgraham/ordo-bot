"""
Main entry point for ordo-bot.

This is what runs when the user types:

    ordo-bot --config config.toml

Current behaviour:
  1. Load configuration
  2. Connect to Ordo with the API token
  3. Create the LLM client and a simple Agent
  4. Optionally run smoke tests or an interactive chat
  5. Stay running until Ctrl-C

Later:
  - Frontend WebSocket server (CLI / web UI connect here)
  - Agent tools that call Ordo commands
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path

from ordo_bot import __version__
from ordo_bot.agent import Agent
from ordo_bot.config import load_settings
from ordo_bot.llm import LLM
from ordo_bot.ordo_client import OrdoClient


def setup_logging(level: str) -> None:
    """Configure basic logging so we can see what the bot is doing."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )


async def interactive_chat(agent: Agent) -> None:
    """
    Simple terminal chat loop for testing the agent before the
    frontend WebSocket exists.

    Type a message and press Enter. Type 'quit' or 'exit' to leave.
    """
    log = logging.getLogger("ordo_bot")
    log.info("Interactive chat ready. Type a message (or 'quit' to exit).")
    print()  # blank line so the prompt is clear

    loop = asyncio.get_running_loop()

    while True:
        # input() blocks, so run it in a thread so the event loop stays alive
        try:
            user_text = await loop.run_in_executor(None, lambda: input("you> "))
        except (EOFError, KeyboardInterrupt):
            print()
            break

        user_text = user_text.strip()
        if not user_text:
            continue
        if user_text.lower() in {"quit", "exit"}:
            break

        reply = await agent.handle_chat(user_text)
        print(f"bot> {reply}")
        print()


async def run_bot(
    settings,
    *,
    smoke: bool = False,
    chat: bool = False,
) -> None:
    """
    Core async loop.

    - Connects to Ordo
    - Creates LLM + Agent
    - Optionally runs smoke tests / interactive chat
    - Then waits until Ctrl-C (unless chat mode already exited)
    """
    log = logging.getLogger("ordo_bot")

    if not settings.ordo_token:
        log.error(
            "No Ordo token configured. "
            "Set ordo_token in config.toml or the ORDO_BOT_ORDO_TOKEN environment variable."
        )
        raise RuntimeError("Missing Ordo token")

    # ------------------------------------------------------------------
    # Ordo client
    # ------------------------------------------------------------------
    ordo = OrdoClient(
        url=settings.ordo_ws_url,
        token=settings.ordo_token,
    )

    async def on_ordo_message(data: dict) -> None:
        log.debug("Ordo message: %s", data)

    ordo.on_message = on_ordo_message

    # ------------------------------------------------------------------
    # LLM + Agent
    # ------------------------------------------------------------------
    llm = LLM(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
    )
    agent = Agent(llm)
    log.info("LLM ready: %s @ %s", settings.llm_model, settings.llm_base_url)

    try:
        log.info("Connecting to Ordo at %s ...", settings.ordo_ws_url)
        await ordo.connect()
        log.info("Connected and logged in to Ordo")

        # ------------------------------------------------------------------
        # Optional Ordo smoke test
        # ------------------------------------------------------------------
        if smoke:
            log.info("Running Ordo smoke test: get_documentation(section='overview')")
            try:
                reply = await ordo.get_documentation(
                    section="overview", format="markdown"
                )
                success = reply.get("success")
                log.info("Ordo smoke test reply success=%s", success)
                doc = reply.get("documentation") or reply.get("content") or ""
                if isinstance(doc, str) and doc:
                    snippet = doc[:300].replace("\n", " ")
                    log.info(
                        "Documentation snippet: %s%s",
                        snippet,
                        "..." if len(doc) > 300 else "",
                    )
            except Exception as e:
                log.error("Ordo smoke test failed: %s", e)

            # Also poke the LLM once
            log.info("Running LLM smoke test ...")
            try:
                llm_reply = await agent.handle_chat(
                    "Reply with exactly the word: pong"
                )
                log.info("LLM smoke reply: %s", llm_reply)
            except Exception as e:
                log.error("LLM smoke test failed: %s", e)

        # ------------------------------------------------------------------
        # Optional interactive chat (terminal)
        # ------------------------------------------------------------------
        if chat:
            await interactive_chat(agent)
            # After chat ends we shut down
            return

        # ------------------------------------------------------------------
        # Idle until Ctrl-C
        # Later: start frontend WebSocket server here
        # ------------------------------------------------------------------
        log.info("Bot is running. Press Ctrl-C to stop.")
        await asyncio.Future()

    except asyncio.CancelledError:
        log.info("Shutdown requested")
    except Exception as e:
        log.error("Fatal error: %s", e)
        raise
    finally:
        log.info("Closing Ordo connection ...")
        await ordo.close()
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
        help="After login, run quick Ordo + LLM test commands",
    )
    parser.add_argument(
        "--chat",
        action="store_true",
        help="Interactive terminal chat with the agent (for testing)",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"ordo-bot {__version__}",
    )
    args = parser.parse_args()

    settings = load_settings(args.config)

    setup_logging(settings.log_level)
    log = logging.getLogger("ordo_bot")

    log.info("ordo-bot %s starting", __version__)
    log.info(
        "LLM endpoint: %s  (model: %s)", settings.llm_base_url, settings.llm_model
    )
    log.info("Ordo URL: %s", settings.ordo_ws_url)

    if not settings.ordo_token:
        log.error(
            "No Ordo token configured. "
            "Set ordo_token in config.toml or the ORDO_BOT_ORDO_TOKEN environment variable."
        )
        sys.exit(1)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    main_task = loop.create_task(
        run_bot(settings, smoke=args.smoke, chat=args.chat)
    )

    def _handle_signal() -> None:
        log.info("Received stop signal")
        main_task.cancel()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_signal)
        except NotImplementedError:
            pass

    try:
        loop.run_until_complete(main_task)
    except asyncio.CancelledError:
        pass
    except RuntimeError as e:
        log.error("%s", e)
        sys.exit(1)
    finally:
        loop.close()


if __name__ == "__main__":
    main()
