"""Pure diff between GitHub's state and Todoist's state.

No network, no SDK: reconcile() takes two plain values and returns an ordered
list of operations. That is what makes the interesting logic testable.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date

from .models import Item, SectionInfo, Snapshot

# A run that wants to complete more than this is almost certainly reacting to a
# degraded GitHub response (revoked org access, a truncated page) rather than to
# me actually having finished that much work.
COMPLETE_CAP = 20


class SyncError(Exception):
    pass


# --- references -------------------------------------------------------------
# A project or section created during this run has no Todoist id yet, so ops
# refer to it symbolically and apply() resolves the reference as it goes.


@dataclass(frozen=True, slots=True)
class Existing:
    id: str


@dataclass(frozen=True, slots=True)
class NewRoot:
    pass


@dataclass(frozen=True, slots=True)
class NewOrg:
    owner_id: str


@dataclass(frozen=True, slots=True)
class NewSection:
    repo_id: str


type ProjectRef = Existing | NewRoot | NewOrg
type SectionRef = Existing | NewSection


# --- operations -------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CreateRoot:
    pass


@dataclass(frozen=True, slots=True)
class CreateOrgProject:
    owner_id: str
    name: str


@dataclass(frozen=True, slots=True)
class RenameProject:
    id: str
    name: str


@dataclass(frozen=True, slots=True)
class CreateSection:
    repo_id: str
    name: str
    project: ProjectRef


@dataclass(frozen=True, slots=True)
class RenameSection:
    id: str
    name: str


@dataclass(frozen=True, slots=True)
class CreateTask:
    content: str
    description: str
    priority: int
    deadline: date | None
    project: ProjectRef
    section: SectionRef


@dataclass(frozen=True, slots=True)
class UpdateTask:
    id: str
    content: str
    priority: int
    deadline: date | None


@dataclass(frozen=True, slots=True)
class MoveTask:
    id: str
    project: ProjectRef
    section: SectionRef


@dataclass(frozen=True, slots=True)
class CompleteTask:
    id: str
    content: str


type Op = (
    CreateRoot
    | CreateOrgProject
    | RenameProject
    | CreateSection
    | RenameSection
    | CreateTask
    | UpdateTask
    | MoveTask
    | CompleteTask
)


class _Refs:
    """Resolves an item to the project and section it belongs in.

    Sections are keyed by (project, repo) rather than repo alone: a repo moved
    between owners simply gets a fresh section in its new home. The stale one is
    left alone -- deleting a section takes its tasks with it.
    """

    def __init__(self, snap: Snapshot) -> None:
        self._snap = snap
        self._root: ProjectRef = Existing(snap.root.id) if snap.root else NewRoot()

    def project(self, item: Item) -> ProjectRef:
        if not item.owner_is_org:
            return self._root
        have = self._snap.orgs.get(item.owner_id)
        return Existing(have.id) if have else NewOrg(item.owner_id)

    def existing_section(self, item: Item) -> SectionInfo | None:
        project = self.project(item)
        if not isinstance(project, Existing):
            return None  # the project itself is being created this run
        return self._snap.sections.get((project.id, item.repo_id))

    def section(self, item: Item) -> SectionRef:
        have = self.existing_section(item)
        return Existing(have.id) if have else NewSection(item.repo_id)


def _org_ops(items: list[Item], snap: Snapshot) -> Iterator[Op]:
    """One sub-project per organization, named after the org as GitHub has it."""
    orgs = {i.owner_id: i.owner_login for i in items if i.owner_is_org}
    for owner_id, login in sorted(orgs.items()):
        have = snap.orgs.get(owner_id)
        if have is None:
            yield CreateOrgProject(owner_id, login)
        elif have.name != login:
            yield RenameProject(have.id, login)


def _section_ops(items: list[Item], refs: _Refs) -> Iterator[Op]:
    """One section per repo, inside that repo's project."""
    seen: set[str] = set()
    for item in sorted(items, key=lambda i: (i.owner_login, i.repo_name)):
        if item.repo_id in seen:
            continue
        seen.add(item.repo_id)
        have = refs.existing_section(item)
        if have is None:
            yield CreateSection(item.repo_id, item.repo_name, refs.project(item))
        elif have.name != item.repo_name:
            yield RenameSection(have.id, item.repo_name)


def _task_ops(items: list[Item], snap: Snapshot, refs: _Refs) -> Iterator[Op]:
    for item in sorted(items, key=lambda i: (i.owner_login, i.repo_name, i.number)):
        task = snap.tasks.get(item.gh_id)
        if task is None:
            yield CreateTask(
                item.content,
                item.description,
                item.priority,
                item.deadline,
                refs.project(item),
                refs.section(item),
            )
            continue
        if (
            task.content != item.content
            or task.priority != item.priority
            or task.deadline != item.deadline
        ):
            yield UpdateTask(task.id, item.content, item.priority, item.deadline)
        section = refs.section(item)
        if not isinstance(section, Existing) or task.section_id != section.id:
            yield MoveTask(task.id, refs.project(item), section)


def _completion_ops(items: list[Item], snap: Snapshot, cap: int) -> list[Op]:
    """Anything GitHub no longer hands me."""
    goal = {i.gh_id for i in items}
    stale = [t for gh_id, t in sorted(snap.tasks.items()) if gh_id not in goal]
    if len(stale) > cap:
        raise SyncError(
            f"{len(stale)} tasks would be completed (cap {cap}). That usually means a "
            f"degraded GitHub response, not finished work. Re-run with --force if intended."
        )
    return [CompleteTask(t.id, t.content) for t in stale]


def reconcile(items: list[Item], snap: Snapshot, cap: int = COMPLETE_CAP) -> list[Op]:
    refs = _Refs(snap)
    ops: list[Op] = [] if snap.root else [CreateRoot()]
    ops += _org_ops(items, snap)
    ops += _section_ops(items, refs)
    ops += _task_ops(items, snap, refs)
    ops += _completion_ops(items, snap, cap)
    return ops
