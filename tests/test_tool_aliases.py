from ordo_bot.llm import recover_tool_calls_from_exception, recover_tool_calls_from_text
from ordo_bot.tools import canonical_tool_name, _normalize_args, tool_inventory


def test_canonical_strips_mcp_prefix_and_aliases():
    assert canonical_tool_name("list_jobs") == "find_cluster"
    assert canonical_tool_name("ordo.list_jobs") == "find_cluster"
    assert canonical_tool_name("list_clusters") == "find_cluster"
    assert canonical_tool_name("find_cluster") == "find_cluster"
    assert canonical_tool_name("read_job") == "read_job"


def test_find_cluster_default_root_drops_limit():
    args = _normalize_args("find_cluster", {"limit": 1000})
    assert args["name"] == "/root"
    assert "limit" not in args


class _FakeStatus:
    def __init__(self, body):
        self.body = body

    def __str__(self):
        return "Error code: 400 - Tool choice is none, but model called a tool"


def test_recover_groq_failed_generation_list_jobs():
    exc = _FakeStatus(
        {
            "error": {
                "message": "Tool choice is none, but model called a tool",
                "code": "tool_use_failed",
                "failed_generation": '{"name": "list_jobs", "arguments": {"limit": 1000}}',
            }
        }
    )
    calls = recover_tool_calls_from_exception(exc)
    assert len(calls) == 1
    assert calls[0].name == "list_jobs"
    assert "1000" in calls[0].arguments


def test_recover_from_plain_text_blob():
    calls = recover_tool_calls_from_text(
        '{"name": "ordo.list_jobs", "arguments": {}}'
    )
    assert calls[0].name == "ordo.list_jobs"
    assert canonical_tool_name(calls[0].name) == "find_cluster"


def test_tool_inventory_has_reads_not_command_reply():
    inv = tool_inventory(allow_write=False)
    assert "find_cluster" in inv["read"]
    assert "list_tools" in inv["read"]
    assert "list_jobs" in inv["read"]
    assert inv["write"] == []
    assert "command_reply" in inv["not_tools"]
    inv_w = tool_inventory(allow_write=True)
    assert "start_cluster" in inv_w["write"]
