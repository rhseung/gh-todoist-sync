"""Pure diff between GitHub's state and Todoist's state.

No network, no SDK: reconcile() takes two plain values and returns an ordered
list of operations. That is what makes the interesting logic testable.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date

from .models import LABEL_COLORS, Item, ProjectInfo, SectionInfo, Snapshot

# A run that wants to complete more than this is almost certainly reacting to a
# degraded GitHub response (revoked org access, a truncated page) rather than to
# me actually having finished that much work.
COMPLETE_CAP = 20

# A section or sub-project that has gone empty is usually about to be refilled:
# the last issue in a repo closes today, the next one opens tomorrow. So an empty
# container is stamped with the date rather than deleted, and only goes once it
# has stayed empty this many days.
GRACE_DAYS = 7


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
    description: str


@dataclass(frozen=True, slots=True)
class RenameProject:
    id: str
    name: str


@dataclass(frozen=True, slots=True)
class CreateSection:
    repo_id: str
    name: str
    description: str
    project: ProjectRef


@dataclass(frozen=True, slots=True)
class RenameSection:
    id: str
    name: str


@dataclass(frozen=True, slots=True)
class CreateTask:
    gh_id: str
    content: str
    description: str
    priority: int
    deadline: date | None
    labels: tuple[str, ...]
    project: ProjectRef
    section: SectionRef


@dataclass(frozen=True, slots=True)
class UpdateTask:
    id: str
    content: str
    description: str
    priority: int
    deadline: date | None
    labels: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MoveTask:
    id: str
    project: ProjectRef
    section: SectionRef


@dataclass(frozen=True, slots=True)
class CompleteTask:
    id: str
    gh_id: str
    content: str


@dataclass(frozen=True, slots=True)
class DeleteTask:
    """For work that never happened: a completion would claim it did."""

    id: str
    gh_id: str
    content: str


@dataclass(frozen=True, slots=True)
class SetDescription:
    """Stamps or clears the empty marker. `kind` is the REST collection."""

    id: str
    kind: str
    description: str


@dataclass(frozen=True, slots=True)
class Delete:
    id: str
    kind: str
    name: str


@dataclass(frozen=True, slots=True)
class MarkEmpty:
    """Bookkeeping only -- the grace clock lives in the state file, not Todoist."""

    id: str
    since: date | None


@dataclass(frozen=True, slots=True)
class SetLabel:
    """Creates the label, or repaints one Todoist made by hand when first used."""

    id: str | None
    name: str
    color: str


@dataclass(frozen=True, slots=True)
class ReorderTasks:
    """The order a section's tasks should sit in, as GitHub ids."""

    gh_ids: tuple[str, ...]


type Op = (
    CreateRoot
    | CreateOrgProject
    | RenameProject
    | CreateSection
    | RenameSection
    | CreateTask
    | UpdateTask
    | MoveTask
    | ReorderTasks
    | CompleteTask
    | DeleteTask
    | SetDescription
    | Delete
    | MarkEmpty
    | SetLabel
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
    orgs = {i.owner_id: i for i in items if i.owner_is_org}
    for owner_id, one in sorted(orgs.items()):
        have = snap.orgs.get(owner_id)
        if have is None:
            yield CreateOrgProject(owner_id, one.owner_login, one.project_description)
        elif have.name != one.owner_login:
            yield RenameProject(have.id, one.owner_login)


def _section_ops(items: list[Item], refs: _Refs) -> Iterator[Op]:
    """One section per repo, inside that repo's project."""
    seen: set[str] = set()
    for item in sorted(items, key=lambda i: (i.owner_login, i.repo_name)):
        if item.repo_id in seen:
            continue
        seen.add(item.repo_id)
        have = refs.existing_section(item)
        if have is None:
            yield CreateSection(
                item.repo_id, item.repo_name, item.section_description, refs.project(item)
            )
        elif have.name != item.repo_name:
            yield RenameSection(have.id, item.repo_name)


def _depths(items: list[Item]) -> dict[str, int]:
    """How many dependency hops sit in front of each item.

    Depth 0 is work with nothing open ahead of it. A blocker outside this list --
    someone else's issue, a repo I hold no assignment in -- still counts as one
    hop, because it still has to close first. A dependency cycle would never
    settle, so a revisited id contributes nothing and the walk terminates.
    """
    by_id = {item.gh_id: item for item in items}
    depth: dict[str, int] = {}

    def walk(gh_id: str, seen: frozenset[str]) -> int:
        if gh_id in depth:
            return depth[gh_id]
        item = by_id.get(gh_id)
        if item is None or gh_id in seen:
            return 0
        found = max((1 + walk(r.gh_id, seen | {gh_id}) for r in item.blocked_by), default=0)
        depth[gh_id] = found
        return found

    for item in items:
        walk(item.gh_id, frozenset())
    return depth


def _heights(items: list[Item]) -> dict[str, int]:
    """How far the chain waiting on each item reaches.

    Depth alone drops everything startable into one bucket ordered by number, so
    work that unblocks three other issues sits below work that unblocks none.
    Height is the mirror measure: the longer the queue behind an item, the more
    finishing it is worth, so it goes first among equals.
    """
    by_id = {item.gh_id: item for item in items}
    height: dict[str, int] = {}

    def walk(gh_id: str, seen: frozenset[str]) -> int:
        if gh_id in height:
            return height[gh_id]
        item = by_id.get(gh_id)
        if item is None or gh_id in seen:
            return 0
        found = max((1 + walk(r.gh_id, seen | {gh_id}) for r in item.blocking), default=0)
        height[gh_id] = found
        return found

    for item in items:
        walk(item.gh_id, frozenset())
    return height


def _order_key(depth: dict[str, int], height: dict[str, int]):
    """What is startable first, then what unblocks the most, then issue number."""
    return lambda i: (
        i.owner_login,
        i.repo_name,
        depth.get(i.gh_id, 0),
        -height.get(i.gh_id, 0),
        i.number,
    )


def _order_ops(
    items: list[Item], snap: Snapshot, depth: dict[str, int], height: dict[str, int]
) -> Iterator[Op]:
    """One reorder per section whose sequence no longer matches the plan.

    Sorting by depth puts what can be started now at the top, which is the point:
    the section reads as a queue instead of as issue numbers. Creating tasks in
    order would only ever fix the run that created them, so the order is stated
    outright each time it drifts.
    """
    groups: dict[tuple[str, str], list[Item]] = {}
    for item in items:
        groups.setdefault((item.owner_login, item.repo_name), []).append(item)
    for _, group in sorted(groups.items()):
        want = [i.gh_id for i in sorted(group, key=_order_key(depth, height))]
        present = [gh_id for gh_id in want if gh_id in snap.tasks]
        current = sorted(present, key=lambda g: snap.tasks[g].child_order)
        if current != present or len(present) != len(want):
            yield ReorderTasks(tuple(want))


def _task_ops(
    items: list[Item],
    snap: Snapshot,
    refs: _Refs,
    depth: dict[str, int],
    height: dict[str, int],
) -> Iterator[Op]:
    for item in sorted(items, key=_order_key(depth, height)):
        task = snap.tasks.get(item.gh_id)
        if task is None:
            yield CreateTask(
                item.gh_id,
                item.content,
                item.description,
                item.priority,
                item.deadline,
                item.labels(),
                refs.project(item),
                refs.section(item),
            )
            continue
        labels = item.labels(task.labels)
        if (
            task.content != item.content
            or task.description != item.description
            or task.priority != item.priority
            or task.deadline != item.deadline
            or task.labels != labels
        ):
            yield UpdateTask(
                task.id, item.content, item.description, item.priority, item.deadline, labels
            )
        section = refs.section(item)
        if not isinstance(section, Existing) or task.section_id != section.id:
            yield MoveTask(task.id, refs.project(item), section)


def _completion_ops(
    items: list[Item], snap: Snapshot, cap: int, discarded: frozenset[str]
) -> list[Op]:
    """Anything GitHub no longer hands me."""
    goal = {i.gh_id for i in items}
    stale = [(gh_id, t) for gh_id, t in sorted(snap.tasks.items()) if gh_id not in goal]
    if len(stale) > cap:
        raise SyncError(
            f"{len(stale)} tasks would be completed (cap {cap}). That usually means a "
            f"degraded GitHub response, not finished work. Re-run with --force if intended."
        )
    return [
        DeleteTask(t.id, gh_id, t.content)
        if gh_id in discarded
        else CompleteTask(t.id, gh_id, t.content)
        for gh_id, t in stale
    ]


def _label_ops(snap: Snapshot) -> Iterator[Op]:
    """Todoist invents a label the first time a task names one, in plain grey."""
    for name, color in sorted(LABEL_COLORS.items()):
        have = snap.labels.get(name)
        if have is None or have.color != color:
            yield SetLabel(have.id if have else None, name, color)


def _cleanup_ops(
    items: list[Item], snap: Snapshot, refs: _Refs, today: date, grace: int
) -> list[Op]:
    """Keep every description current, and delete what stays empty past the grace.

    A task completed by this same run still counts as occupying its section --
    the snapshot was taken before it closed -- so the clock starts one poll late.
    """
    live = set(snap.occupied)
    wanted: dict[str, str] = {}
    for item in items:
        project = refs.project(item)
        if isinstance(project, Existing):
            live.add(project.id)
            if item.owner_is_org:
                wanted[project.id] = item.project_description
        if section := refs.existing_section(item):
            live.add(section.id)
            wanted[section.id] = item.section_description

    ops: list[Op] = []
    gone: set[str] = set()

    def decide(kind: str, info: ProjectInfo | SectionInfo) -> None:
        since = snap.empty_since.get(info.id)
        if info.id in live:
            if since is not None:
                ops.append(MarkEmpty(info.id, None))
        elif since is None:
            ops.append(MarkEmpty(info.id, today))
        elif (today - since).days >= grace:
            ops.append(Delete(info.id, kind, info.name))
            gone.add(info.id)
            return
        # A stale container has no item to rebuild from, so its own text stands.
        if (want := wanted.get(info.id)) and want != info.description:
            ops.append(SetDescription(info.id, kind, want))

    for project in sorted(snap.orgs.values(), key=lambda p: p.id):
        decide("projects", project)
    for section in sorted(snap.sections.values(), key=lambda s: s.id):
        if section.project_id not in gone:  # deleting the project takes it anyway
            decide("sections", section)
    return ops


def reconcile(  # noqa: PLR0913
    items: list[Item],
    snap: Snapshot,
    *,
    cap: int = COMPLETE_CAP,
    grace: int = GRACE_DAYS,
    today: date | None = None,
    discarded: frozenset[str] = frozenset(),
) -> list[Op]:
    refs = _Refs(snap)
    ops: list[Op] = [] if snap.root else [CreateRoot()]
    ops += _label_ops(snap)
    ops += _org_ops(items, snap)
    ops += _section_ops(items, refs)
    depth, height = _depths(items), _heights(items)
    ops += _task_ops(items, snap, refs, depth, height)
    ops += _order_ops(items, snap, depth, height)
    ops += _completion_ops(items, snap, cap, discarded)
    ops += _cleanup_ops(items, snap, refs, today or date.today(), grace)
    return ops
