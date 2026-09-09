"""desired() against a fake client: which queries run, and what they map to."""

from __future__ import annotations

import pytest

from gh_todoist_sync import gh

REPO = {
    "id": 1,
    "name": "rhseung",
    "full_name": "rhseung/rhseung",
    "archived": False,
    "owner": {"id": 9, "login": "rhseung", "type": "User"},
}


def found(node_id: str, number: int, *, is_pr: bool) -> dict:
    payload = {
        "node_id": node_id,
        "number": number,
        "title": "t",
        "html_url": f"https://github.com/rhseung/rhseung/issues/{number}",
        "repository_url": f"{gh.BASE_URL}/repos/rhseung/rhseung",
    }
    if is_pr:
        payload["pull_request"] = {}
    return payload


class FakeClient:
    def __init__(self, results: dict[str, list[dict]]) -> None:
        self.results = results
        self.queries: list[str] = []

    def get(self, path: str, **params):
        if path == "/issues":
            return []
        if path == "/search/issues":
            self.queries.append(params["q"])
            return {"items": self.results.get(params["q"], [])}
        return REPO


def test_unassigned_issues_i_opened_are_collected():
    api = FakeClient(
        {
            "is:pr is:open author:@me": [found("PR_a", 1, is_pr=True)],
            "is:issue is:open author:@me no:assignee": [found("I_b", 38, is_pr=False)],
        }
    )

    items = {item.gh_id: item for item in gh.desired(api)}

    # Assigned to someone else is left out by the query, not by a filter here.
    assert "is:issue is:open author:@me no:assignee" in api.queries
    assert items["PR_a"].is_pr is True
    assert items["I_b"].is_pr is False
    assert items["I_b"].number == 38


def test_discarded_picks_out_what_was_never_done():
    nodes = [
        {"id": "I_planned", "stateReason": "COMPLETED"},
        {"id": "I_dropped", "stateReason": "NOT_PLANNED"},
        {"id": "I_dupe", "stateReason": "DUPLICATE"},
        {"id": "PR_merged", "state": "MERGED"},
        {"id": "PR_given_up", "state": "CLOSED"},
        {"id": "PR_still_open", "state": "OPEN"},
        None,  # gone, or no longer visible to this token
    ]

    class GraphQL:
        def post(self, path, **body):
            assert path == "/graphql"
            return {"data": {"nodes": nodes}}

    ids = {n["id"] for n in nodes if n} | {"I_gone"}
    assert gh.discarded(GraphQL(), ids) == frozenset({"I_dropped", "I_dupe", "PR_given_up"})


def test_discarded_skips_the_call_when_nothing_vanished():
    class Explodes:
        def post(self, *a, **k):
            raise AssertionError("no ids, no call")

    assert gh.discarded(Explodes(), set()) == frozenset()


def test_discarded_survives_an_id_github_no_longer_resolves():
    """A deleted issue answers with a null node and an error, not a dead run."""

    class Partial:
        def post(self, path, **body):
            return {
                "data": {"nodes": [None, {"id": "I_dropped", "stateReason": "NOT_PLANNED"}]},
                "errors": [{"type": "NOT_FOUND", "path": ["nodes", 0]}],
            }

    assert gh.discarded(Partial(), {"I_gone", "I_dropped"}) == frozenset({"I_dropped"})


def test_discarded_stops_the_run_when_nothing_came_back():
    class Refused:
        def post(self, path, **body):
            return {"errors": [{"message": "Bad credentials"}]}

    with pytest.raises(RuntimeError, match="Bad credentials"):
        gh.discarded(Refused(), {"I_a"})
