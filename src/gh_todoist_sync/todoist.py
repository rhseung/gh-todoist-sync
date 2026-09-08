"""Todoist's current state, and how operations are carried out against it."""

from __future__ import annotations

from datetime import date
from typing import Any

from .models import (
    MARKER_EMPTY,
    MARKER_ORG,
    MARKER_REPO,
    MARKER_ROOT,
    MARKER_TASK,
    ROOT_NAME,
    ProjectInfo,
    SectionInfo,
    Snapshot,
    TaskInfo,
    marker_value,
)
from .reconcile import (
    CompleteTask,
    CreateOrgProject,
    CreateRoot,
    CreateSection,
    CreateTask,
    Delete,
    Existing,
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
    UpdateTask,
)
from .rest import Client, cli_token

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


def snapshot(api: Client) -> Snapshot:
    projects = _all(api, "/projects")
    root = next(
        (p for p in projects if marker_value(p.get("description"), MARKER_ROOT) is not None), None
    )
    if root is None:
        return Snapshot(root=None, orgs={}, sections={}, tasks={})

    tree = [root] + [p for p in projects if p.get("parent_id") == root["id"]]
    sections: dict[tuple[str, str], SectionInfo] = {}
    tasks: dict[str, TaskInfo] = {}
    occupied: set[str] = set()
    empty_since: dict[str, date] = {}
    for project in tree:
        if when := marker_value(project.get("description"), MARKER_EMPTY):
            empty_since[project["id"]] = date.fromisoformat(when)
        for raw in _all(api, "/sections", project_id=project["id"]):
            if repo_id := marker_value(raw.get("description"), MARKER_REPO):
                sections[(raw["project_id"], repo_id)] = SectionInfo(
                    raw["id"], raw["name"], raw["project_id"], raw.get("description") or ""
                )
                if when := marker_value(raw.get("description"), MARKER_EMPTY):
                    empty_since[raw["id"]] = date.fromisoformat(when)
        for raw in _all(api, "/tasks", project_id=project["id"]):
            occupied.add(raw["project_id"])
            if raw.get("section_id"):
                occupied.add(raw["section_id"])
            if gh_id := marker_value(raw.get("description"), MARKER_TASK):
                tasks[gh_id] = TaskInfo(
                    id=raw["id"],
                    content=raw["content"],
                    project_id=raw["project_id"],
                    section_id=raw.get("section_id"),
                    priority=raw["priority"],
                    deadline=_deadline(raw.get("deadline")),
                )
    return Snapshot(
        root=ProjectInfo(root["id"], root["name"]),
        orgs={
            owner_id: ProjectInfo(p["id"], p["name"], p.get("description") or "")
            for p in tree[1:]
            if (owner_id := marker_value(p.get("description"), MARKER_ORG))
        },
        sections=sections,
        tasks=tasks,
        occupied=frozenset(occupied),
        empty_since=empty_since,
    )


class Applier:
    """Runs ops in order, resolving symbolic refs as the objects come into being."""

    def __init__(self, api: Client) -> None:
        self._api = api
        self._root: str | None = None
        self._orgs: dict[str, str] = {}
        self._sections: dict[str, str] = {}

    def project(self, ref: ProjectRef) -> str:
        match ref:
            case Existing(id=value):
                return value
            case NewRoot():
                assert self._root, "root project referenced before it was created"
                return self._root
            case NewOrg(owner_id=owner_id):
                return self._orgs[owner_id]

    def section(self, ref: SectionRef) -> str:
        match ref:
            case Existing(id=value):
                return value
            case NewSection(repo_id=repo_id):
                return self._sections[repo_id]

    def run(self, op: Op) -> None:
        api = self._api
        match op:
            case CreateRoot():
                self._root = api.post("/projects", name=ROOT_NAME, description=MARKER_ROOT)["id"]
            case CreateOrgProject(owner_id=owner_id, name=name, description=description):
                self._orgs[owner_id] = api.post(
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
                self._sections[repo_id] = api.post(
                    "/sections",
                    name=name,
                    project_id=self.project(project),
                    description=description,
                )["id"]
            case RenameSection(id=section_id, name=name):
                api.post(f"/sections/{section_id}", name=name)
            case CreateTask(
                content=content,
                description=description,
                priority=priority,
                deadline=deadline,
                project=project,
                section=section,
            ):
                api.post(
                    "/tasks",
                    content=content,
                    description=description,
                    priority=priority,
                    project_id=self.project(project),
                    section_id=self.section(section),
                    deadline_date=deadline.isoformat() if deadline else None,
                )
            case UpdateTask(id=task_id, content=content, priority=priority, deadline=deadline):
                api.post(
                    f"/tasks/{task_id}",
                    content=content,
                    priority=priority,
                    deadline_date=deadline.isoformat() if deadline else None,
                )
            case MoveTask(id=task_id, project=project, section=section):
                api.post(
                    f"/tasks/{task_id}/move",
                    project_id=self.project(project),
                    section_id=self.section(section),
                )
            case CompleteTask(id=task_id):
                api.post(f"/tasks/{task_id}/close")
            case SetDescription(id=object_id, kind=kind, description=description):
                api.post(f"/{kind}/{object_id}", description=description)
            case Delete(id=object_id, kind=kind):
                api.delete(f"/{kind}/{object_id}")


def apply(api: Client, ops: list[Op]) -> None:
    applier = Applier(api)
    for op in ops:
        applier.run(op)
