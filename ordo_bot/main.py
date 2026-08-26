"""
Main entry point for ordo-bot.

This is what runs when the user types:

    ordo-bot --config config.toml

Current behaviour:
  1. Load configuration
  2. Connect to Ordo with the API token
  3. Create the LLM client and Agent
  4. Start the frontend WebSocket server (for CLI / web UI)
  5. Optionally run smoke tests or an interactive terminal chat
  6. Stay running until Ctrl-C
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
from ordo_bot.frontend_server import FrontendServer
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
    Simple terminal chat loop (alternative to the WebSocket CLI).

    Type a message and press Enter. Type 'quit' or 'exit' to leave.
    """
    log = logging.getLogger("ordo_bot")
    log.info("Interactive chat ready. Type a message (or 'quit' to exit).")
    print()

    loop = asyncio.get_running_loop()

    while True:
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
    """
    log = logging.getLogger("ordo_bot")

    if not settings.ordo_token:
        log.error(
            "No Ordo token configured. "
            "Set ordo_token in config.toml or the ORDO_BOT_ORDO_TOKEN environment variable."
        )
        raise RuntimeError("Missing Ordo token")

    # ------------------------------------------------------------------
    # LLM + Agent
    # ------------------------------------------------------------------
    llm = LLM(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
    )
    # ordo is attached after login so tools become available
    agent = Agent(llm, ordo=None)
    log.info("LLM ready: %s @ %s", settings.llm_model, settings.llm_base_url)

    # ------------------------------------------------------------------
    # Frontend WebSocket server (clients connect here)
    # ------------------------------------------------------------------
    frontend = FrontendServer(
        host=settings.frontend_host,
        port=settings.frontend_port,
        agent=agent,
        ordo_connected=False,  # updated after Ordo login
        model=settings.llm_model,
    )

    # ------------------------------------------------------------------
    # Ordo client
    # ------------------------------------------------------------------
    ordo = OrdoClient(
        url=settings.ordo_ws_url,
        token=settings.ordo_token,
    )

    async def on_ordo_message(data: dict) -> None:
        """Forward interesting Ordo events to all frontend clients."""
        log.debug("Ordo message: %s", data)
        # Broadcasts look like: {"broadcast": "jobs_changed", ...}
        broadcast = data.get("broadcast")
        if broadcast:
            await frontend.broadcast_ordo_event(broadcast, data)

    ordo.on_message = on_ordo_message

    try:
        # Start frontend first so clients can connect while we log in
        await frontend.start()

        log.info("Connecting to Ordo at %s ...", settings.ordo_ws_url)
        await ordo.connect()
        log.info("Connected and logged in to Ordo")

        # Enable Ordo tools now that we are logged in
        agent.ordo = ordo
        frontend.ordo_connected = True
        await frontend.broadcast_status()

        # ------------------------------------------------------------------
        # Optional smoke tests
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

            log.info("Running LLM smoke test ...")
            try:
                llm_reply = await agent.handle_chat(
                    "Reply with exactly the word: pong"
                )
                log.info("LLM smoke reply: %s", llm_reply)
            except Exception as e:
                log.error("LLM smoke test failed: %s", e)

        # ------------------------------------------------------------------
        # Optional interactive terminal chat
        # ------------------------------------------------------------------
        if chat:
            await interactive_chat(agent)
            return

        # ------------------------------------------------------------------
        # Idle until Ctrl-C
        # ------------------------------------------------------------------
        log.info(
            "Bot is running. Frontend at ws://%s:%s  (Ctrl-C to stop)",
            settings.frontend_host,
            settings.frontend_port,
        )
        await asyncio.Future()

    except asyncio.CancelledError:
        log.info("Shutdown requested")
    except Exception as e:
        log.error("Fatal error: %s", e)
        raise
    finally:
        log.info("Shutting down ...")
        await frontend.stop()
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
    log.info(
        "Frontend will listen on %s:%s",
        settings.frontend_host,
        settings.frontend_port,
    )

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
