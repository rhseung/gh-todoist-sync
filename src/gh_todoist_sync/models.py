"""Shared vocabulary between the GitHub side, the Todoist side, and reconcile.

Nothing here imports an SDK, so the pure logic and its tests stay cheap.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

ROOT_NAME = "GitHub"

# Todoist's API priority runs backwards from the p1..p4 labels in the UI.
PRIORITY_ISSUE = 1  # p4
PRIORITY_PR = 3  # p2

# Priority says how urgent, not what kind, so the kind is a label -- which is
# also what `@gh-pr` in a Todoist filter can select on. The colours are the ones
# GitHub itself uses for the two icons, so the sidebar reads at a glance.
LABEL_PR = "gh-pr"
LABEL_ISSUE = "gh-issue"
LABEL_COLORS = {LABEL_PR: "grape", LABEL_ISSUE: "green"}


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

    # Written as an explicit markdown link, not a bare URL: Todoist rewrites a
    # bare URL into a titled link of its own, which would read as a change on
    # every single poll and rewrite the description forever.
    @property
    def owner_url(self) -> str:
        return f"https://github.com/{self.owner_login}"

    @property
    def repo_url(self) -> str:
        return f"{self.owner_url}/{self.repo_name}"

    @property
    def project_description(self) -> str:
        return f"[{self.owner_login}]({self.owner_url})"

    @property
    def section_description(self) -> str:
        return f"[{self.repo_name}]({self.repo_url})"

    @property
    def priority(self) -> int:
        return PRIORITY_PR if self.is_pr else PRIORITY_ISSUE

    @property
    def label(self) -> str:
        return LABEL_PR if self.is_pr else LABEL_ISSUE

    def labels(self, current: tuple[str, ...] = ()) -> tuple[str, ...]:
        """This item's kind, on top of whatever labels were added by hand."""
        return (*(x for x in current if x not in LABEL_COLORS), self.label)


@dataclass(frozen=True, slots=True)
class ProjectInfo:
    id: str
    name: str
    description: str = ""


@dataclass(frozen=True, slots=True)
class SectionInfo:
    id: str
    name: str
    project_id: str
    description: str = ""


@dataclass(frozen=True, slots=True)
class TaskInfo:
    id: str
    content: str
    project_id: str
    section_id: str | None
    priority: int
    deadline: date | None
    labels: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LabelInfo:
    id: str
    color: str


@dataclass(frozen=True, slots=True)
class Snapshot:
    """Todoist's current state, indexed by GitHub id through the state file."""

    root: ProjectInfo | None
    orgs: dict[str, ProjectInfo]  # owner_id -> sub-project
    sections: dict[tuple[str, str], SectionInfo]  # (project_id, repo_id) -> section
    tasks: dict[str, TaskInfo]  # gh_id -> task
    # Every task counts here, ours or not: deleting a container takes whatever
    # is inside it, so a hand written note is enough to keep one alive.
    occupied: frozenset[str] = frozenset()  # project and section ids holding a task
    empty_since: dict[str, date] = field(default_factory=dict)  # id -> first seen empty
    labels: dict[str, LabelInfo] = field(default_factory=dict)  # label name -> label
