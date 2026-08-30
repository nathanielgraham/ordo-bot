from ordo_bot.tools import _iso, _nest_clusters, _prepare_result, _slim_cluster


def test_iso_epoch_is_2026_not_2023():
    assert _iso(1788121668) == "2026-08-30T20:27:48Z"
    assert _iso(None) is None


def test_nest_ops_under_root_and_deploy_under_ops():
    flat = [
        {"id": 1, "name": "root", "parent_id": None, "jobstate": "immutable", "jobs": []},
        {"id": 16, "name": "ops", "parent_id": 1, "jobstate": "complete", "jobs": []},
        {
            "id": 17,
            "name": "deploy-saas",
            "parent_id": 16,
            "jobstate": "complete",
            "jobs": [{"id": 7, "name": "pull", "jobstate": "complete"}],
        },
        {
            "id": 18,
            "name": "Bork da Cake",
            "parent_id": 1,
            "jobstate": "complete",
            "jobs": [{"id": 11, "name": "prep", "jobstate": "complete"}],
        },
    ]
    tree = _nest_clusters(flat)
    names = {c["name"] for c in tree["clusters"]}
    assert names == {"ops", "Bork da Cake"}
    ops = next(c for c in tree["clusters"] if c["name"] == "ops")
    assert ops["clusters"][0]["name"] == "deploy-saas"
    assert ops["clusters"][0]["jobs"][0]["name"] == "pull"


def test_prepare_find_cluster_includes_index_and_tree():
    data = {
        "success": 1,
        "clusters": [
            {"id": 1, "name": "root", "parent_id": None, "jobstate": "immutable"},
            {"id": 18, "name": "Bork da Cake", "parent_id": 1, "jobstate": "waiting"},
        ],
    }
    out = _prepare_result("find_cluster", data, 8000)
    parsed = __import__("json").loads(out)
    assert parsed["count"] == 2
    ids = {row["id"] for row in parsed["index"]}
    assert ids == {1, 18}
    assert parsed["tree"]["name"] == "root"
    assert parsed["tree"]["clusters"][0]["name"] == "Bork da Cake"


def test_read_cluster_warns_no_children_and_isos_times():
    data = {
        "id": 16,
        "name": "ops",
        "jobstate": "complete",
        "parent_id": 1,
        "started": 1788121668,
        "ended": 1788121674,
        "jobs": [],
    }
    out = _prepare_result("read_cluster", data, 8000)
    parsed = __import__("json").loads(out)
    assert "find_cluster" in parsed["child_clusters"]
    assert parsed["started"].startswith("2026-")
    assert "script" not in parsed
