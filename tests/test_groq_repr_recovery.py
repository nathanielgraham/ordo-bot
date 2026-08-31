from ordo_bot.llm import recover_tool_calls_from_exception, recover_tool_calls_from_text
from ordo_bot.tools import canonical_tool_name


class _StrOnly:
    def __init__(self, text):
        self._text = text

    def __str__(self):
        return self._text


REPR_FIND = (
    "Error code: 400 - {'error': {'message': 'Tool choice is none, but model called a tool',"
    " 'type': 'invalid_request_error', 'code': 'tool_use_failed',"
    " 'failed_generation': '{\"name\": \"find_cluster\", \"arguments\": {\"name\":\"/root\"}}'}}"
)

REPR_ESCAPED = (
    "Error code: 400 - {'error': {'message': 'Tool choice is none, but model called a tool',"
    " 'type': 'invalid_request_error', 'code': 'tool_use_failed',"
    " 'failed_generation': '{\"name\": \"find_cluster\", \"arguments\": {\"name\":\"\\/root\"}}'}}"
)

REPR_SLASH = (
    "Error code: 400 - {'error': {'message': 'Tool choice is none, but model called a tool',"
    " 'type': 'invalid_request_error', 'code': 'tool_use_failed',"
    " 'failed_generation': '{\"name\": \"oriole/find_cluster\", \"arguments\": {\"name\":\"/root\"}}'}}"
)


def test_recover_python_repr_find_cluster():
    calls = recover_tool_calls_from_exception(_StrOnly(REPR_FIND))
    assert len(calls) == 1
    assert canonical_tool_name(calls[0].name) == "find_cluster"
    assert "/root" in calls[0].arguments.replace("\\/", "/")


def test_recover_python_repr_escaped_slash_in_path():
    calls = recover_tool_calls_from_exception(_StrOnly(REPR_ESCAPED))
    assert len(calls) == 1
    assert canonical_tool_name(calls[0].name) == "find_cluster"


def test_recover_slash_prefixed_tool_name():
    calls = recover_tool_calls_from_text(REPR_SLASH)
    assert len(calls) == 1
    assert canonical_tool_name(calls[0].name) == "find_cluster"
