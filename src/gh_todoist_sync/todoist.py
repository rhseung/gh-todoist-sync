"""Todoist's current state, and how operations are carried out against it."""

from __future__ import annotations

from datetime import date
from typing import Any

from .models import (
    LABEL_COLORS,
    ROOT_NAME,
    LabelInfo,
    ProjectInfo,
    SectionInfo,
    Snapshot,
    TaskInfo,
)
from .reconcile import (
    CompleteTask,
    CreateOrgProject,
    CreateRoot,
    CreateSection,
    CreateTask,
    Delete,
    Existing,
    MarkEmpty,
    MoveTask,
    NewOrg,
    NewRoot,
    NewSection,
    Op,
    ProjectRef,
    RenameProject,
    RenameSection,
    SectionRef,
    SetDescription,
    SetLabel,
    UpdateTask,
)
from .rest import Client, cli_token
from .state import State, save

BASE_URL = "https://api.todoist.com/api/v1"
PAGE = 200


def client() -> Client:
    return Client(BASE_URL, cli_token("TODOIST_API_TOKEN", ["td", "auth", "token", "view"]))


def _all(api: Client, path: str, **params: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    cursor = None
    while True:
        page = api.get(path, limit=PAGE, **({"cursor": cursor} if cursor else {}), **params)
        out += page["results"]
        cursor = page.get("next_cursor")
        if not cursor:
            return out


def _deadline(raw: Any) -> date | None:
    value = raw.get("date") if isinstance(raw, dict) else raw
    return date.fromisoformat(value) if value else None


def _labels(api: Client) -> dict[str, LabelInfo]:
    raw = _all(api, "/labels")
    return {r["name"]: LabelInfo(r["id"], r["color"]) for r in raw if r["name"] in LABEL_COLORS}


def snapshot(api: Client, state: State) -> Snapshot:
    """Read Todoist back through the ids the state file remembers.

    Anything the state file points at that Todoist no longer has is dropped from
    both, so a project deleted by hand is simply rebuilt on the next run.
    """
    labels = _labels(api)
    projects = {p["id"]: p for p in _all(api, "/projects")}
    for project_id in list(state.orgs.values()):
        if project_id not in projects:
            state.forget(project_id)
    if not state.root or state.root not in projects:
        state.root = None
        return Snapshot(root=None, orgs={}, sections={}, tasks={}, labels=labels)

    orgs = {
        owner_id: ProjectInfo(pid, projects[pid]["name"], projects[pid].get("description") or "")
        for owner_id, pid in state.orgs.items()
        if pid in projects
    }
    repo_of = {section_id: repo_id for repo_id, section_id in state.sections.items()}
    gh_of = {task_id: gh_id for gh_id, task_id in state.tasks.items()}

    sections: dict[tuple[str, str], SectionInfo] = {}
    tasks: dict[str, TaskInfo] = {}
    occupied: set[str] = set()
    seen: set[str] = set()
    for project_id in [state.root, *(p.id for p in orgs.values())]:
        for raw in _all(api, "/sections", project_id=project_id):
            seen.add(raw["id"])
            if repo_id := repo_of.get(raw["id"]):
                sections[(project_id, repo_id)] = SectionInfo(
                    raw["id"], raw["name"], project_id, raw.get("description") or ""
                )
        for raw in _all(api, "/tasks", project_id=project_id):
            seen.add(raw["id"])
            occupied.add(raw["project_id"])
            if raw.get("section_id"):
                occupied.add(raw["section_id"])
            if gh_id := gh_of.get(raw["id"]):
                tasks[gh_id] = TaskInfo(
                    id=raw["id"],
                    content=raw["content"],
                    project_id=raw["project_id"],
                    section_id=raw.get("section_id"),
                    priority=raw["priority"],
                    deadline=_deadline(raw.get("deadline")),
                    labels=tuple(raw.get("labels") or ()),
                )
    for todoist_id in [*state.sections.values(), *state.tasks.values()]:
        if todoist_id not in seen:
            state.forget(todoist_id)

    root = projects[state.root]
    return Snapshot(
        root=ProjectInfo(state.root, root["name"], root.get("description") or ""),
        orgs=orgs,
        sections=sections,
        tasks=tasks,
        occupied=frozenset(occupied),
        empty_since=dict(state.empty_since),
        labels=labels,
    )


class Applier:
    """Runs ops in order, recording into the state file what each one created.

    The state file doubles as the symbolic-reference table: a project created
    earlier in this same run is already in it by the time an op refers to it.
    """

    def __init__(self, api: Client, state: State) -> None:
        self._api = api
        self._state = state

    def project(self, ref: ProjectRef) -> str:
        match ref:
            case Existing(id=value):
                return value
            case NewRoot():
                assert self._state.root, "root project referenced before it was created"
                return self._state.root
            case NewOrg(owner_id=owner_id):
                return self._state.orgs[owner_id]

    def section(self, ref: SectionRef) -> str:
        match ref:
            case Existing(id=value):
                return value
            case NewSection(repo_id=repo_id):
                return self._state.sections[repo_id]

    # One flat arm per op: the branch count is the size of the op vocabulary,
    # and splitting it would only scatter the dispatch across two places.
    def run(self, op: Op) -> None:  # noqa: PLR0912
        api = self._api
        state = self._state
        match op:
            case CreateRoot():
                state.root = api.post("/projects", name=ROOT_NAME)["id"]
            case CreateOrgProject(owner_id=owner_id, name=name, description=description):
                state.orgs[owner_id] = api.post(
                    "/projects",
                    name=name,
                    parent_id=self.project(NewRoot()),
                    description=description,
                )["id"]
            case RenameProject(id=project_id, name=name):
                api.post(f"/projects/{project_id}", name=name)
            case CreateSection(
                repo_id=repo_id, name=name, description=description, project=project
            ):
                state.sections[repo_id] = api.post(
                    "/sections",
                    name=name,
                    project_id=self.project(project),
                    description=description,
                )["id"]
            case RenameSection(id=section_id, name=name):
                api.post(f"/sections/{section_id}", name=name)
            case CreateTask(
                gh_id=gh_id,
                content=content,
                priority=priority,
                deadline=deadline,
                labels=labels,
                project=project,
                section=section,
            ):
                state.tasks[gh_id] = api.post(
                    "/tasks",
                    content=content,
                    priority=priority,
                    labels=list(labels),
                    project_id=self.project(project),
                    section_id=self.section(section),
                    deadline_date=deadline.isoformat() if deadline else None,
                )["id"]
            case UpdateTask(
                id=task_id, content=content, priority=priority, deadline=deadline, labels=labels
            ):
                api.post(
                    f"/tasks/{task_id}",
                    content=content,
                    priority=priority,
                    labels=list(labels),
                    deadline_date=deadline.isoformat() if deadline else None,
                )
            case MoveTask(id=task_id, project=project, section=section):
                api.post(
                    f"/tasks/{task_id}/move",
                    project_id=self.project(project),
                    section_id=self.section(section),
                )
            case CompleteTask(id=task_id, gh_id=gh_id):
                api.post(f"/tasks/{task_id}/close")
                state.tasks.pop(gh_id, None)
            case SetDescription(id=object_id, kind=kind, description=description):
                api.post(f"/{kind}/{object_id}", description=description)
            case Delete(id=object_id, kind=kind):
                api.delete(f"/{kind}/{object_id}")
                state.forget(object_id)
            case MarkEmpty(id=object_id, since=since):
                if since is None:
                    state.empty_since.pop(object_id, None)
                else:
                    state.empty_since[object_id] = since
            case SetLabel(id=label_id, name=name, color=color):
                path = f"/labels/{label_id}" if label_id else "/labels"
                api.post(path, name=name, color=color)


def apply(api: Client, state: State, ops: list[Op]) -> None:
    """Saves whatever got applied, even if an op raises partway through.

    Without that, a run that dies after creating tasks would forget their ids
    and build a second copy of every one of them on the next run.
    """
    applier = Applier(api, state)
    try:
        for op in ops:
            applier.run(op)
    finally:
        save(state)
