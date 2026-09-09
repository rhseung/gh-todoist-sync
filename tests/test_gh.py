"""desired() against a fake client: which queries run, and what they map to."""

from __future__ import annotations

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


if __name__ == "__main__":
    test_unassigned_issues_i_opened_are_collected()
    print("ok")
