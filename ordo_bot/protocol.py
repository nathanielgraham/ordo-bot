"""
Bot ↔ Client WebSocket protocol (v0.1)

This file defines the structured messages that travel between
ordo-bot and any front-end (CLI, web UI, etc.).

We use Pydantic models so every message is validated and
easy to serialize/deserialize to JSON.

Transport: Newline-Delimited JSON (NDJSON)
  - One JSON object per line
  - Same style that Ordo itself uses
"""

from typing import Any, Dict, Literal, Optional, Union
from pydantic import BaseModel, Field


# ----------------------------------------------------------------------
# Messages that the CLIENT sends TO the bot
# ----------------------------------------------------------------------

class ChatMessage(BaseModel):
    """User wants to say something to the agent."""
    type: Literal["chat"] = "chat"
    content: str = Field(..., description="The text the user typed")


class PingMessage(BaseModel):
    """Simple keep-alive / connectivity check."""
    type: Literal["ping"] = "ping"


# Union of everything a client is allowed to send
ClientMessage = Union[ChatMessage, PingMessage]


# ----------------------------------------------------------------------
# Messages that the BOT sends TO the client
# ----------------------------------------------------------------------

class AssistantMessage(BaseModel):
    """A complete reply from the agent."""
    type: Literal["message"] = "message"
    role: Literal["assistant"] = "assistant"
    content: str


class MessageDelta(BaseModel):
    """
    A streaming chunk of an assistant reply.
    Front-ends can append these as they arrive for a live typing effect.
    """
    type: Literal["message_delta"] = "message_delta"
    content: str


class OrdoEventMessage(BaseModel):
    """
    An event that originated from Ordo (jobs_changed, clusters_changed, etc.).
    The bot forwards these so the UI can stay up to date.
    """
    type: Literal["ordo_event"] = "ordo_event"
    event: str
    data: Dict[str, Any] = Field(default_factory=dict)


class StatusMessage(BaseModel):
    """Current status of the bot (connection state, which model is in use, etc.)."""
    type: Literal["status"] = "status"
    ordo_connected: bool
    model: str
    extra: Dict[str, Any] = Field(default_factory=dict)


class ErrorMessage(BaseModel):
    """Something went wrong."""
    type: Literal["error"] = "error"
    message: str


class PongMessage(BaseModel):
    """Reply to a ping."""
    type: Literal["pong"] = "pong"


# Union of everything the bot can send
BotMessage = Union[
    AssistantMessage,
    MessageDelta,
    OrdoEventMessage,
    StatusMessage,
    ErrorMessage,
    PongMessage,
]


def parse_client_message(raw: dict) -> ClientMessage:
    """
    Turn a raw dict (from JSON) into a typed ClientMessage.
    Raises ValidationError if the message is malformed.
    """
    msg_type = raw.get("type")
    if msg_type == "chat":
        return ChatMessage.model_validate(raw)
    if msg_type == "ping":
        return PingMessage.model_validate(raw)
    raise ValueError(f"Unknown client message type: {msg_type}")
