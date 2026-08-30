from ordo_bot.watches import TERMINAL_JOBSTATES, is_terminal, jobstate_of


def test_terminals_come_from_ordo_wsagent():
    assert "killed" in TERMINAL_JOBSTATES
    assert is_terminal({"jobstate": "killed"})
    assert is_terminal({"jobstate": "complete"})
    assert not is_terminal({"jobstate": "running", "state_id": 5})
    assert not is_terminal({"state_id": 5})
    assert jobstate_of({"jobstate": "FAILED"}) == "failed"
