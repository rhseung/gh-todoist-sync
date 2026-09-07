"""Shared vocabulary between the GitHub side, the Todoist side, and reconcile.

Nothing here imports an SDK, so the pure logic and its tests stay cheap.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

ROOT_NAME = "GitHub"

# The GitHub -> Todoist mapping lives in Todoist's own description fields rather
# than a local state file, so it survives a new machine and manual UI edits.
MARKER_ROOT = "gh-root: 1"
MARKER_ORG = "gh-org-id: "
MARKER_REPO = "gh-repo-id: "
MARKER_TASK = "gh-id: "

# Todoist's API priority runs backwards from the p1..p4 labels in the UI.
PRIORITY_ISSUE = 1  # p4
PRIORITY_PR = 3  # p2


@dataclass(frozen=True, slots=True)
class Item:
    """One piece of GitHub work assigned to me."""

    gh_id: str  # GraphQL node id, stable across renames and transfers
    is_pr: bool
    repo_id: str
    repo_name: str
    owner_id: str
    owner_login: str
    owner_is_org: bool
    number: int
    title: str
    url: str
    deadline: date | None

    @property
    def content(self) -> str:
        # Todoist renders markdown, so the number doubles as a link. The repo is
        # left out because the section already names it.
        return f"[#{self.number}]({self.url}) {self.title}"

    @property
    def description(self) -> str:
        return f"{MARKER_TASK}{self.gh_id}\n{self.url}"

    @property
    def priority(self) -> int:
        return PRIORITY_PR if self.is_pr else PRIORITY_ISSUE


@dataclass(frozen=True, slots=True)
class ProjectInfo:
    id: str
    name: str


@dataclass(frozen=True, slots=True)
class SectionInfo:
    id: str
    name: str
    project_id: str


@dataclass(frozen=True, slots=True)
class TaskInfo:
    id: str
    content: str
    project_id: str
    section_id: str | None
    priority: int
    deadline: date | None


@dataclass(frozen=True, slots=True)
class Snapshot:
    """Todoist's current state, pre-indexed by the GitHub ids embedded in it."""

    root: ProjectInfo | None
    orgs: dict[str, ProjectInfo]  # owner_id -> sub-project
    sections: dict[tuple[str, str], SectionInfo]  # (project_id, repo_id) -> section
    tasks: dict[str, TaskInfo]  # gh_id -> task


def marker_value(description: str | None, prefix: str) -> str | None:
    for line in (description or "").splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix):
            return stripped[len(prefix) :].strip()
    return None
