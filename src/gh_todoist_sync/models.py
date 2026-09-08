"""Shared vocabulary between the GitHub side, the Todoist side, and reconcile.

Nothing here imports an SDK, so the pure logic and its tests stay cheap.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

ROOT_NAME = "GitHub"

# The GitHub -> Todoist mapping lives in Todoist's own description fields rather
# than a local state file, so it survives a new machine and manual UI edits.
MARKER_ROOT = "gh-root: 1"
MARKER_ORG = "gh-org-id: "
MARKER_REPO = "gh-repo-id: "
MARKER_TASK = "gh-id: "
# Stamped on a section or sub-project the moment it is first seen empty, so the
# grace period survives between runs without a local state file.
MARKER_EMPTY = "gh-empty-since: "

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

    @property
    def description(self) -> str:
        return f"{MARKER_TASK}{self.gh_id}\n{self.url}"

    # Written as an explicit markdown link, not a bare URL: Todoist rewrites a
    # bare URL into a titled link of its own, which would read as a change on
    # every single poll and rewrite the description forever.
    @property
    def owner_url(self) -> str:
        return f"https://github.com/{self.owner_login}"

    @property
    def repo_url(self) -> str:
        return f"{self.owner_url}/{self.repo_name}"

    # A blank line has to follow the link, or markdown pulls the marker up onto
    # the link's own line.

    @property
    def project_description(self) -> str:
        return f"[{self.owner_login}]({self.owner_url})\n\n{MARKER_ORG}{self.owner_id}"

    @property
    def section_description(self) -> str:
        return f"[{self.repo_name}]({self.repo_url})\n\n{MARKER_REPO}{self.repo_id}"

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
    """Todoist's current state, pre-indexed by the GitHub ids embedded in it."""

    root: ProjectInfo | None
    orgs: dict[str, ProjectInfo]  # owner_id -> sub-project
    sections: dict[tuple[str, str], SectionInfo]  # (project_id, repo_id) -> section
    tasks: dict[str, TaskInfo]  # gh_id -> task
    # Every task counts here, marked or not: deleting a container takes whatever
    # is inside it, so a hand written note is enough to keep one alive.
    occupied: frozenset[str] = frozenset()  # project and section ids holding a task
    empty_since: dict[str, date] = field(default_factory=dict)  # id -> first seen empty
    labels: dict[str, LabelInfo] = field(default_factory=dict)  # label name -> label


def without_marker(description: str, prefix: str) -> str:
    """The description as it reads with one marker line taken back out."""
    kept = [line for line in description.splitlines() if not line.strip().startswith(prefix)]
    return "\n".join(kept)


def marker_value(description: str | None, prefix: str) -> str | None:
    for line in (description or "").splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix):
            return stripped[len(prefix) :].strip()
    return None
