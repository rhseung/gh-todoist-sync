"""The whole point of keeping reconcile pure: these run without a network."""

from __future__ import annotations

from datetime import date, timedelta

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
    GRACE_DAYS,
    SyncError,
    reconcile,
)

ROOT = ProjectInfo("R", "GitHub")
TODAY = date(2026, 9, 8)


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
        sections={
            ("R", one.repo_id): SectionInfo("S", one.repo_name, "R", one.section_description)
        },
        tasks={one.gh_id: TaskInfo("T", one.content, "R", "S", one.priority, one.deadline)},
        occupied=frozenset({"R", "S"}),
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


def test_repo_rename_touches_only_the_section():
    one = item()
    # Task content carries no repo name, so no task op follows a repo rename --
    # only the section's own name and the repo link in its description.
    ops = reconcile([item(repo_name="rds2")], settled(one), today=TODAY)
    assert kinds(ops) == ["RenameSection", "SetDescription"]
    assert ops[1].description == "https://github.com/rhseung/rds2\n\ngh-repo-id: 1"


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


def empty_section(empty_since: date | None) -> Snapshot:
    """A section Todoist still has but GitHub has nothing for."""
    description = "https://github.com/rhseung/rds\n\ngh-repo-id: 1"
    if empty_since:
        description += f"\ngh-empty-since: {empty_since}"
    return Snapshot(
        root=ROOT,
        orgs={},
        sections={("R", "1"): SectionInfo("S", "rds", "R", description)},
        tasks={},
        occupied=frozenset(),
        empty_since={"S": empty_since} if empty_since else {},
    )


def test_newly_empty_section_is_stamped_not_deleted():
    ops = reconcile([], empty_section(None), today=TODAY)
    assert kinds(ops) == ["SetDescription"]
    assert ops[0].description.splitlines()[-1] == "gh-empty-since: 2026-09-08"


def test_section_inside_the_grace_period_is_left_alone():
    snap = empty_section(TODAY - timedelta(days=GRACE_DAYS - 1))
    assert reconcile([], snap, today=TODAY) == []


def test_section_empty_past_the_grace_period_is_deleted():
    snap = empty_section(TODAY - timedelta(days=GRACE_DAYS))
    ops = reconcile([], snap, today=TODAY)
    assert kinds(ops) == ["Delete"]
    assert (ops[0].id, ops[0].kind) == ("S", "sections")


def test_refilled_section_loses_its_stamp():
    snap = empty_section(TODAY - timedelta(days=99))
    ops = reconcile([item()], snap, today=TODAY)
    assert kinds(ops) == ["CreateTask", "SetDescription"]
    assert ops[1].description == "https://github.com/rhseung/rds\n\ngh-repo-id: 1"


def test_an_unmarked_task_keeps_the_section_alive():
    # occupied counts every task, so a hand written note is never deleted with
    # the section around it.
    stale = empty_section(TODAY - timedelta(days=99))
    snap = Snapshot(stale.root, {}, stale.sections, {}, frozenset({"S"}), stale.empty_since)
    assert kinds(reconcile([], snap, today=TODAY)) == ["SetDescription"]


def test_deleting_an_org_project_takes_its_section():
    since = TODAY - timedelta(days=GRACE_DAYS)
    stamp = f"\ngh-empty-since: {since}"
    snap = Snapshot(
        root=ROOT,
        orgs={"8": ProjectInfo("P", "gsainfoteam", f"gh-org-id: 8{stamp}")},
        sections={("P", "2"): SectionInfo("S2", "ziggle", "P", f"gh-repo-id: 2{stamp}")},
        tasks={},
        empty_since={"P": since, "S2": since},
    )
    ops = reconcile([], snap, today=TODAY)
    assert kinds(ops) == ["Delete"]
    assert ops[0].id == "P"


def test_descriptions_lead_with_a_link_to_github():
    org = item(owner_id="8", owner_login="gsainfoteam", owner_is_org=True)
    ops = reconcile([org], Snapshot(ROOT, {}, {}, {}), today=TODAY)
    assert kinds(ops) == ["CreateOrgProject", "CreateSection", "CreateTask"]
    # Blank line between link and marker, or Todoist's markdown joins the lines.
    assert ops[0].description == "https://github.com/gsainfoteam\n\ngh-org-id: 8"
    assert ops[1].description == "https://github.com/gsainfoteam/rds\n\ngh-repo-id: 1"


def test_org_rename_rewrites_the_project_link():
    org = item(owner_id="8", owner_login="gsa-new", owner_is_org=True)
    snap = Snapshot(
        ROOT,
        {"8": ProjectInfo("P", "gsainfoteam", "https://github.com/gsainfoteam\n\ngh-org-id: 8")},
        {},
        {},
        occupied=frozenset({"P"}),
    )
    ops = reconcile([org], snap, today=TODAY)
    assert kinds(ops) == ["RenameProject", "CreateSection", "CreateTask", "SetDescription"]
    assert ops[3].description == "https://github.com/gsa-new\n\ngh-org-id: 8"
