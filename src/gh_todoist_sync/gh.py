"""What GitHub currently hands me."""

from __future__ import annotations

from datetime import date
from typing import Any

from .models import Item
from .rest import Client, cli_token

BASE_URL = "https://api.github.com"
PER_PAGE = 100
HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}


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
    """Assigned issues and PRs, plus PRs awaiting my review or opened by me.

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

    for qualifier in ("review-requested:@me", "author:@me"):
        found = api.get("/search/issues", q=f"is:pr is:open {qualifier}", per_page=PER_PAGE)
        for pr in found["items"]:
            full_name = pr["repository_url"].removeprefix(f"{BASE_URL}/repos/")
            if full_name not in repos:
                repos[full_name] = api.get(f"/repos/{full_name}")
            if repos[full_name]["archived"]:
                continue
            # Assigned to me and also mine: PR wins, it carries the higher priority.
            items[pr["node_id"]] = _item(pr, repos[full_name], is_pr=True)

    return list(items.values())
