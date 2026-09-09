"""What GitHub currently hands me."""

from __future__ import annotations

from datetime import date
from typing import Any

from .models import Item
from .rest import Client, cli_token

BASE_URL = "https://api.github.com"
PER_PAGE = 100
HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}

# An issue I opened and nobody took is mine to do, so it belongs here; one taken
# by someone else does not. `no:assignee` draws that line in the query -- the
# assignee:@me half of "mine" already arrives through the REST assigned list.
SEARCHES = (
    "is:pr is:open review-requested:@me",
    "is:pr is:open author:@me",
    "is:issue is:open author:@me no:assignee",
)

# GraphQL takes node ids straight, which is all the state file keeps, so nothing
# has to be parsed back out of a task's text to ask about it. 100 is the cap the
# nodes() field enforces.
NODES_PER_CALL = 100
# How the two kinds say the work never happened. An issue spells out its reason;
# a PR only closes, and MERGED is the separate state that means it landed, so a
# plain CLOSED is a PR that was given up on.
DISCARDED_REASONS = {"NOT_PLANNED", "DUPLICATE"}
DISCARDED_STATE = "CLOSED"


def client() -> Client:
    return Client(BASE_URL, cli_token("GITHUB_TOKEN", ["gh", "auth", "token"]), **HEADERS)


def _item(payload: dict[str, Any], repo: dict[str, Any], *, is_pr: bool) -> Item:
    owner = repo["owner"]
    milestone = payload.get("milestone") or {}
    due = milestone.get("due_on")
    return Item(
        gh_id=payload["node_id"],
        is_pr=is_pr,
        repo_id=str(repo["id"]),
        repo_name=repo["name"],
        owner_id=str(owner["id"]),
        owner_login=owner["login"],
        owner_is_org=owner["type"] == "Organization",
        number=payload["number"],
        title=" ".join(payload["title"].split()),
        url=payload["html_url"],
        deadline=date.fromisoformat(due[:10]) if due else None,
    )


def _pages(api: Client, path: str, **params: Any):
    page = 1
    while True:
        batch = api.get(path, per_page=PER_PAGE, page=page, **params)
        if not batch:
            return
        yield from batch
        if len(batch) < PER_PAGE:
            return
        page += 1


def desired(api: Client) -> list[Item]:
    """Assigned issues and PRs, plus what I opened or was asked to review.

    Archived repos are dropped: their work cannot be acted on, so a task for it
    is noise. The repo stays cached either way, so the skip costs no extra call.

    The assigned list comes from the REST issues endpoint rather than search: it
    has no search-index lag, and it embeds the repository and owner ids that the
    whole id-based mapping depends on. Search returns neither, so the few PRs it
    finds get one cached repository lookup each.
    """
    items: dict[str, Item] = {}
    repos: dict[str, dict[str, Any]] = {}

    for issue in _pages(api, "/issues", filter="assigned", state="open"):
        repo = issue["repository"]
        repos[repo["full_name"]] = repo
        if repo["archived"]:
            continue
        items[issue["node_id"]] = _item(issue, repo, is_pr="pull_request" in issue)

    for query in SEARCHES:
        found = api.get("/search/issues", q=query, per_page=PER_PAGE)
        for payload in found["items"]:
            full_name = payload["repository_url"].removeprefix(f"{BASE_URL}/repos/")
            if full_name not in repos:
                repos[full_name] = api.get(f"/repos/{full_name}")
            if repos[full_name]["archived"]:
                continue
            is_pr = "pull_request" in payload
            items[payload["node_id"]] = _item(payload, repos[full_name], is_pr=is_pr)

    return list(items.values())


def _never_happened(node: dict[str, Any]) -> bool:
    return node.get("stateReason") in DISCARDED_REASONS or node.get("state") == DISCARDED_STATE


def discarded(api: Client, gh_ids: set[str]) -> frozenset[str]:
    """Of the ids that fell out of the goal, the ones closed as not-work.

    An issue closed as not planned or duplicate, and a PR closed without being
    merged, are work that never happened; completing the Todoist task would file
    a false record of having done it. Every other way an id can vanish -- closed
    as completed, merged, unassigned, repo archived, access lost -- still reads
    as a completion, and an id GitHub no longer serves comes back null and falls
    through to one.
    """
    query = (
        "query($ids: [ID!]!) { nodes(ids: $ids) {"
        " ... on Issue { id stateReason }"
        " ... on PullRequest { id state } } }"
    )
    ids = sorted(gh_ids)
    out: set[str] = set()
    for start in range(0, len(ids), NODES_PER_CALL):
        body = api.post(
            "/graphql", query=query, variables={"ids": ids[start : start + NODES_PER_CALL]}
        )
        # An id that no longer resolves -- issue deleted, access lost -- comes
        # back as a null node next to an error, with the rest of the batch
        # intact. That is the ordinary case here, so only a reply carrying no
        # data at all counts as a failure worth stopping the run for.
        if (data := body.get("data")) is None:
            raise RuntimeError(f"GitHub GraphQL refused the node lookup: {body.get('errors')}")
        out |= {n["id"] for n in data["nodes"] if n and _never_happened(n)}
    return frozenset(out)
