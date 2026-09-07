"""The whole point of keeping reconcile pure: these run without a network."""

from __future__ import annotations

from datetime import date

import pytest

from gh_todoist_sync.models import (
    MARKER_TASK,
    PRIORITY_ISSUE,
    PRIORITY_PR,
    Item,
    ProjectInfo,
    SectionInfo,
    Snapshot,
    TaskInfo,
    marker_value,
)
from gh_todoist_sync.reconcile import (
    SyncError,
    reconcile,
)

ROOT = ProjectInfo("R", "GitHub")


def item(**overrides) -> Item:
    base = {
        "gh_id": "I_a",
        "is_pr": False,
        "repo_id": "1",
        "repo_name": "rds",
        "owner_id": "9",
        "owner_login": "rhseung",
        "owner_is_org": False,
        "number": 42,
        "title": "fix thing",
        "url": "https://gh/1",
        "deadline": None,
    }
    return Item(**{**base, **overrides})


def settled(one: Item) -> Snapshot:
    """A snapshot that already matches `one` exactly."""
    return Snapshot(
        root=ROOT,
        orgs={},
        sections={("R", one.repo_id): SectionInfo("S", one.repo_name, "R")},
        tasks={one.gh_id: TaskInfo("T", one.content, "R", "S", one.priority, one.deadline)},
    )


def kinds(ops) -> list[str]:
    return [type(op).__name__ for op in ops]


def test_steady_state_is_a_no_op():
    one = item()
    assert reconcile([one], settled(one)) == []


def test_empty_todoist_builds_the_tree():
    ops = reconcile([item()], Snapshot(None, {}, {}, {}))
    assert kinds(ops) == ["CreateRoot", "CreateSection", "CreateTask"]


def test_new_issue_in_a_known_repo():
    one = item()
    ops = reconcile([one, item(gh_id="I_b", number=43)], settled(one))
    assert kinds(ops) == ["CreateTask"]


def test_repo_rename_moves_only_the_section():
    one = item()
    # Task content carries no repo name, so nothing else has to change.
    assert kinds(reconcile([item(repo_name="rds2")], settled(one))) == ["RenameSection"]


def test_issue_retitle_rewrites_the_task():
    one = item()
    ops = reconcile([item(title="fix other thing")], settled(one))
    assert kinds(ops) == ["UpdateTask"]
    assert ops[0].content == "[#42](https://gh/1) fix other thing"


def test_org_repo_gets_a_sub_project():
    org = item(
        gh_id="I_b",
        repo_id="2",
        repo_name="ziggle",
        owner_id="8",
        owner_login="gsainfoteam",
        owner_is_org=True,
        is_pr=True,
        deadline=date(2026, 10, 1),
    )
    ops = reconcile([org], Snapshot(ROOT, {}, {}, {}))
    assert kinds(ops) == ["CreateOrgProject", "CreateSection", "CreateTask"]
    assert ops[2].deadline == date(2026, 10, 1)
    assert ops[2].priority == PRIORITY_PR


def test_org_rename_follows_github():
    org = item(owner_id="8", owner_login="gsa-new", owner_is_org=True)
    snap = Snapshot(ROOT, {"8": ProjectInfo("P", "gsainfoteam")}, {}, {})
    assert kinds(reconcile([org], snap))[0] == "RenameProject"


def test_vanished_issue_is_completed():
    one = item()
    ops = reconcile([], settled(one))
    assert kinds(ops) == ["CompleteTask"]
    assert ops[0].id == "T"


def test_bulk_completion_is_refused():
    tasks = {f"I_{n}": TaskInfo(f"T{n}", "c", "R", "S", PRIORITY_ISSUE, None) for n in range(21)}
    snap = Snapshot(ROOT, {}, {}, tasks)
    with pytest.raises(SyncError, match="21 tasks"):
        reconcile([], snap)
    assert len(reconcile([], snap, cap=99)) == 21


def test_unmarked_tasks_are_invisible():
    # Anything without a gh-id marker never reaches the snapshot, so a hand
    # written note living in the GitHub project is never touched.
    assert marker_value("just a note", MARKER_TASK) is None
    assert marker_value(f"{MARKER_TASK}I_a\nhttps://gh/1", MARKER_TASK) == "I_a"


def test_task_in_the_wrong_section_is_moved():
    one = item()
    snap = settled(one)
    snap.tasks[one.gh_id] = TaskInfo("T", one.content, "R", "ELSEWHERE", one.priority, one.deadline)
    assert kinds(reconcile([one], snap)) == ["MoveTask"]
