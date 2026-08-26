"""
Bot ↔ Client WebSocket protocol (v0.1+)

Transport: Newline-Delimited JSON (NDJSON).
"""

from typing import Any, Dict, Literal, Union
from pydantic import BaseModel, Field


# ----------------------------------------------------------------------
# Client → bot
# ----------------------------------------------------------------------

class ChatMessage(BaseModel):
    """User wants to say something to the agent."""
    type: Literal["chat"] = "chat"
    content: str = Field(..., description="The text the user typed")


class PingMessage(BaseModel):
    """Simple keep-alive / connectivity check."""
    type: Literal["ping"] = "ping"


class ResetMessage(BaseModel):
    """Clear the agent's conversation history (keep system prompt)."""
    type: Literal["reset"] = "reset"


ClientMessage = Union[ChatMessage, PingMessage, ResetMessage]


# ----------------------------------------------------------------------
# Bot → client
# ----------------------------------------------------------------------

class AssistantMessage(BaseModel):
    """A complete reply from the agent."""
    type: Literal["message"] = "message"
    role: Literal["assistant"] = "assistant"
    content: str


class MessageDelta(BaseModel):
    """Streaming chunk of an assistant reply (future)."""
    type: Literal["message_delta"] = "message_delta"
    content: str


class OrdoEventMessage(BaseModel):
    """
    Event from Ordo (jobs_changed, etc.).

    Forwarded to UI only — never stored in agent chat history.
    """
    type: Literal["ordo_event"] = "ordo_event"
    event: str
    data: Dict[str, Any] = Field(default_factory=dict)


class StatusMessage(BaseModel):
    """Current status of the bot."""
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


BotMessage = Union[
    AssistantMessage,
    MessageDelta,
    OrdoEventMessage,
    StatusMessage,
    ErrorMessage,
    PongMessage,
]


def parse_client_message(raw: dict) -> ClientMessage:
    """Turn a raw dict into a typed ClientMessage."""
    msg_type = raw.get("type")
    if msg_type == "chat":
        return ChatMessage.model_validate(raw)
    if msg_type == "ping":
        return PingMessage.model_validate(raw)
    if msg_type == "reset":
        return ResetMessage.model_validate(raw)
    raise ValueError(f"Unknown client message type: {msg_type}")
