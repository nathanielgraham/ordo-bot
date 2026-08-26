"""
Main entry point for ordo-bot.
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
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )


async def interactive_chat(agent: Agent) -> None:
    log = logging.getLogger("ordo_bot")
    log.info("Interactive chat ready. Type a message (or 'quit' / 'reset').")
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
        if user_text.lower() in {"reset", "/reset"}:
            agent.reset()
            print("bot> Conversation history cleared.\n")
            continue

        reply = await agent.handle_chat(user_text)
        print(f"bot> {reply}")
        print()


async def run_bot(
    settings,
    *,
    smoke: bool = False,
    chat: bool = False,
) -> None:
    log = logging.getLogger("ordo_bot")

    if not settings.ordo_token:
        log.error(
            "No Ordo token configured. "
            "Set ordo_token in config.toml or ORDO_BOT_ORDO_TOKEN."
        )
        raise RuntimeError("Missing Ordo token")

    llm = LLM(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
    )
    agent = Agent(
        llm,
        ordo=None,
        max_history_messages=settings.max_history_messages,
        tool_result_max_chars=settings.tool_result_max_chars,
        bootstrap_docs=settings.bootstrap_docs,
    )
    log.info("LLM ready: %s @ %s", settings.llm_model, settings.llm_base_url)
    log.info(
        "Agent history cap=%s tool_result_max_chars=%s bootstrap_docs=%s",
        settings.max_history_messages,
        settings.tool_result_max_chars,
        settings.bootstrap_docs,
    )

    frontend = FrontendServer(
        host=settings.frontend_host,
        port=settings.frontend_port,
        agent=agent,
        ordo_connected=False,
        model=settings.llm_model,
    )

    ordo = OrdoClient(
        url=settings.ordo_ws_url,
        token=settings.ordo_token,
    )

    async def on_ordo_message(data: dict) -> None:
        # Forward to UI only — never into agent.messages
        log.debug("Ordo message: %s", data)
        broadcast = data.get("broadcast")
        if broadcast:
            await frontend.broadcast_ordo_event(broadcast, data)

    ordo.on_message = on_ordo_message

    try:
        await frontend.start()

        log.info("Connecting to Ordo at %s ...", settings.ordo_ws_url)
        await ordo.connect()
        log.info("Connected and logged in to Ordo")

        agent.ordo = ordo
        await agent.bootstrap()

        frontend.ordo_connected = True
        await frontend.broadcast_status()

        if smoke:
            log.info("Running Ordo smoke test: get_documentation")
            try:
                reply = await ordo.get_documentation(
                    section="overview", format="summary"
                )
                log.info("Ordo smoke test success=%s", reply.get("success"))
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

        if chat:
            await interactive_chat(agent)
            return

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
        help="Interactive terminal chat with the agent",
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
            "Set ordo_token in config.toml or ORDO_BOT_ORDO_TOKEN."
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
