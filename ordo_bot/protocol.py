"""
Bot ↔ Client WebSocket protocol (v0.2)

Transport: Newline-Delimited JSON (NDJSON).

Client → bot:  chat | ping | reset
Bot → client:  ack | progress | message | error | ordo_event | status | pong
"""

from typing import Any, Dict, Literal, Optional, Union
from pydantic import BaseModel, Field


# ----------------------------------------------------------------------
# Client → bot
# ----------------------------------------------------------------------

class ChatMessage(BaseModel):
    """User wants to say something to the agent (queued server-side)."""
    type: Literal["chat"] = "chat"
    content: str = Field(..., description="The text the user typed")


class PingMessage(BaseModel):
    type: Literal["ping"] = "ping"


class ResetMessage(BaseModel):
    """Clear agent history and drop pending queued chats (immediate)."""
    type: Literal["reset"] = "reset"


ClientMessage = Union[ChatMessage, PingMessage, ResetMessage]


# ----------------------------------------------------------------------
# Bot → client
# ----------------------------------------------------------------------

class AckMessage(BaseModel):
    """Chat was accepted (queued or starting)."""
    type: Literal["ack"] = "ack"
    content: str = "received"
    queue_depth: int = Field(
        default=0,
        description="Pending chats ahead of this one (0 = next / in progress)",
    )


class ProgressMessage(BaseModel):
    """Lightweight status so the UI does not look hung."""
    type: Literal["progress"] = "progress"
    content: str = "processing"


class AssistantMessage(BaseModel):
    type: Literal["message"] = "message"
    role: Literal["assistant"] = "assistant"
    content: str


class MessageDelta(BaseModel):
    type: Literal["message_delta"] = "message_delta"
    content: str


class OrdoEventMessage(BaseModel):
    type: Literal["ordo_event"] = "ordo_event"
    event: str
    data: Dict[str, Any] = Field(default_factory=dict)


class StatusMessage(BaseModel):
    type: Literal["status"] = "status"
    ordo_connected: bool
    model: str
    extra: Dict[str, Any] = Field(default_factory=dict)


class ErrorMessage(BaseModel):
    type: Literal["error"] = "error"
    message: str


class PongMessage(BaseModel):
    type: Literal["pong"] = "pong"


BotMessage = Union[
    AckMessage,
    ProgressMessage,
    AssistantMessage,
    MessageDelta,
    OrdoEventMessage,
    StatusMessage,
    ErrorMessage,
    PongMessage,
]


def parse_client_message(raw: dict) -> ClientMessage:
    msg_type = raw.get("type")
    if msg_type == "chat":
        return ChatMessage.model_validate(raw)
    if msg_type == "ping":
        return PingMessage.model_validate(raw)
    if msg_type == "reset":
        return ResetMessage.model_validate(raw)
    raise ValueError(f"Unknown client message type: {msg_type}")
