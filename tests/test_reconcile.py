"""The whole point of keeping reconcile pure: these run without a network."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from gh_todoist_sync.models import (
    LABEL_BLOCKED,
    LABEL_ISSUE,
    LABEL_PR,
    PRIORITY_ISSUE,
    PRIORITY_PR,
    Item,
    LabelInfo,
    ProjectInfo,
    Ref,
    SectionInfo,
    Snapshot,
    TaskInfo,
)
from gh_todoist_sync.reconcile import (
    GRACE_DAYS,
    CreateTask,
    MarkEmpty,
    ReorderTasks,
    SetLabel,
    SyncError,
    reconcile,
)

ROOT = ProjectInfo("R", "GitHub")
TODAY = date(2026, 9, 8)
# Painted already, so _label_ops stays quiet and the other tests read clean.
PAINTED = {
    LABEL_PR: LabelInfo("LP", "grape"),
    LABEL_ISSUE: LabelInfo("LI", "green"),
    LABEL_BLOCKED: LabelInfo("LB", "red"),
}


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
        tasks={
            one.gh_id: TaskInfo(
                "T",
                one.content,
                "R",
                "S",
                one.priority,
                one.deadline,
                one.labels(),
                one.description,
            )
        },
        occupied=frozenset({"R", "S"}),
        labels=PAINTED,
    )


def kinds(ops) -> list[str]:
    return [type(op).__name__ for op in ops]


def test_steady_state_is_a_no_op():
    one = item()
    assert reconcile([one], settled(one)) == []


def test_empty_todoist_builds_the_tree():
    ops = reconcile([item()], Snapshot(None, {}, {}, {}, labels=PAINTED))
    assert kinds(ops) == ["CreateRoot", "CreateSection", "CreateTask", "ReorderTasks"]


def test_new_issue_in_a_known_repo():
    one = item()
    ops = reconcile([one, item(gh_id="I_b", number=43)], settled(one))
    assert kinds(ops) == ["CreateTask", "ReorderTasks"]


def test_repo_rename_touches_only_the_section():
    one = item()
    # Task content carries no repo name, so no task op follows a repo rename --
    # only the section's own name and the repo link in its description.
    ops = reconcile([item(repo_name="rds2")], settled(one), today=TODAY)
    assert kinds(ops) == ["RenameSection", "SetDescription"]
    assert ops[1].description == "[rds2](https://github.com/rhseung/rds2)"


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
    ops = reconcile([org], Snapshot(ROOT, {}, {}, {}, labels=PAINTED))
    assert kinds(ops) == ["CreateOrgProject", "CreateSection", "CreateTask", "ReorderTasks"]
    assert ops[2].deadline == date(2026, 10, 1)
    assert ops[2].priority == PRIORITY_PR


def test_org_rename_follows_github():
    org = item(owner_id="8", owner_login="gsa-new", owner_is_org=True)
    snap = Snapshot(ROOT, {"8": ProjectInfo("P", "gsainfoteam")}, {}, {}, labels=PAINTED)
    assert kinds(reconcile([org], snap))[0] == "RenameProject"


def test_vanished_issue_is_completed():
    one = item()
    ops = reconcile([], settled(one))
    assert kinds(ops) == ["CompleteTask"]
    assert ops[0].id == "T"


def test_issue_closed_as_not_planned_is_deleted():
    one = item()
    ops = reconcile([], settled(one), discarded=frozenset({one.gh_id}))
    assert kinds(ops) == ["DeleteTask"]
    assert ops[0].id == "T"


def test_bulk_completion_is_refused():
    tasks = {f"I_{n}": TaskInfo(f"T{n}", "c", "R", "S", PRIORITY_ISSUE, None) for n in range(21)}
    snap = Snapshot(ROOT, {}, {}, tasks, labels=PAINTED)
    with pytest.raises(SyncError, match="21 tasks"):
        reconcile([], snap)
    assert len(reconcile([], snap, cap=99)) == 21


def test_task_in_the_wrong_section_is_moved():
    one = item()
    snap = settled(one)
    snap.tasks[one.gh_id] = TaskInfo(
        "T", one.content, "R", "ELSEWHERE", one.priority, one.deadline, one.labels()
    )
    assert kinds(reconcile([one], snap)) == ["MoveTask"]


def empty_section(empty_since: date | None) -> Snapshot:
    """A section Todoist still has but GitHub has nothing for."""
    return Snapshot(
        root=ROOT,
        orgs={},
        sections={
            ("R", "1"): SectionInfo("S", "rds", "R", "[rds](https://github.com/rhseung/rds)")
        },
        tasks={},
        occupied=frozenset(),
        empty_since={"S": empty_since} if empty_since else {},
        labels=PAINTED,
    )


def test_newly_empty_section_is_stamped_not_deleted():
    ops = reconcile([], empty_section(None), today=TODAY)
    assert ops == [MarkEmpty("S", TODAY)]


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
    assert kinds(ops) == ["CreateTask", "ReorderTasks", "MarkEmpty"]
    assert ops[2] == MarkEmpty("S", None)


def test_an_unmarked_task_keeps_the_section_alive():
    # occupied counts every task, so a hand written note is never deleted with
    # the section around it.
    stale = empty_section(TODAY - timedelta(days=99))
    snap = Snapshot(
        stale.root, {}, stale.sections, {}, frozenset({"S"}), stale.empty_since, PAINTED
    )
    assert reconcile([], snap, today=TODAY) == [MarkEmpty("S", None)]


def test_deleting_an_org_project_takes_its_section():
    since = TODAY - timedelta(days=GRACE_DAYS)
    snap = Snapshot(
        root=ROOT,
        orgs={"8": ProjectInfo("P", "gsainfoteam")},
        sections={("P", "2"): SectionInfo("S2", "ziggle", "P")},
        tasks={},
        empty_since={"P": since, "S2": since},
        labels=PAINTED,
    )
    ops = reconcile([], snap, today=TODAY)
    assert kinds(ops) == ["Delete"]
    assert ops[0].id == "P"


def test_descriptions_lead_with_a_link_to_github():
    org = item(owner_id="8", owner_login="gsainfoteam", owner_is_org=True)
    ops = reconcile([org], Snapshot(ROOT, {}, {}, {}, labels=PAINTED), today=TODAY)
    assert kinds(ops) == ["CreateOrgProject", "CreateSection", "CreateTask", "ReorderTasks"]
    # Explicit markdown link, not a bare URL -- Todoist retitles a bare URL and
    # the description would then differ from what reconcile wants on every poll.
    assert ops[0].description == "[gsainfoteam](https://github.com/gsainfoteam)"
    assert ops[1].description == "[rds](https://github.com/gsainfoteam/rds)"


def test_org_rename_rewrites_the_project_link():
    org = item(owner_id="8", owner_login="gsa-new", owner_is_org=True)
    snap = Snapshot(
        ROOT,
        {
            "8": ProjectInfo(
                "P", "gsainfoteam", "[gsainfoteam](https://github.com/gsainfoteam)\n\ngh-org-id: 8"
            )
        },
        {},
        {},
        occupied=frozenset({"P"}),
        labels=PAINTED,
    )
    ops = reconcile([org], snap, today=TODAY)
    assert kinds(ops) == [
        "RenameProject",
        "CreateSection",
        "CreateTask",
        "ReorderTasks",
        "SetDescription",
    ]
    assert ops[4].description == "[gsa-new](https://github.com/gsa-new)"


def test_labels_are_painted_so_the_two_kinds_read_apart():
    # Todoist invents the label in grey the first time a task names it.
    ops = reconcile([item()], Snapshot(ROOT, {}, {}, {}), today=TODAY)
    assert [(op.name, op.color, op.id) for op in ops if isinstance(op, SetLabel)] == [
        (LABEL_BLOCKED, "red", None),
        (LABEL_ISSUE, "green", None),
        (LABEL_PR, "grape", None),
    ]
    faded = dict(PAINTED, **{LABEL_PR: LabelInfo("LP", "grey")})
    ops = reconcile([item()], Snapshot(ROOT, {}, {}, {}, labels=faded), today=TODAY)
    assert [(op.name, op.id) for op in ops if isinstance(op, SetLabel)] == [(LABEL_PR, "LP")]


def test_kind_is_a_label_so_a_filter_can_see_it():
    pair = [item(), item(gh_id="I_b", number=43, is_pr=True)]
    ops = reconcile(pair, Snapshot(ROOT, {}, {}, {}, labels=PAINTED), today=TODAY)
    assert [op.labels for op in ops if isinstance(op, CreateTask)] == [(LABEL_ISSUE,), (LABEL_PR,)]


def test_an_issue_turned_pr_swaps_its_label_and_keeps_manual_ones():
    one = item()
    snap = settled(one)
    snap.tasks[one.gh_id] = TaskInfo(
        "T", one.content, "R", "S", one.priority, one.deadline, ("waiting", LABEL_ISSUE)
    )
    ops = reconcile([item(is_pr=True)], snap, today=TODAY)
    assert kinds(ops) == ["UpdateTask"]
    assert ops[0].labels == ("waiting", LABEL_PR)


# --- dependencies -----------------------------------------------------------


def test_a_blocker_sorts_ahead_of_what_it_blocks():
    # Numbers run the other way on purpose: depth has to beat the tie-break.
    blocker = item(gh_id="I_a", number=90)
    blocked = item(gh_id="I_b", number=10, blocked_by=(Ref("I_a", 90, "rhseung/rds"),))
    ops = reconcile([blocked, blocker], Snapshot(ROOT, {}, {}, {}, labels=PAINTED))
    assert [op.gh_id for op in ops if isinstance(op, CreateTask)] == ["I_a", "I_b"]
    assert [op.gh_ids for op in ops if isinstance(op, ReorderTasks)] == [("I_a", "I_b")]


def test_a_chain_orders_end_to_end():
    a = item(gh_id="I_a", number=3)
    b = item(gh_id="I_b", number=2, blocked_by=(Ref("I_a", 3, "rhseung/rds"),))
    c = item(gh_id="I_c", number=1, blocked_by=(Ref("I_b", 2, "rhseung/rds"),))
    ops = reconcile([c, b, a], Snapshot(ROOT, {}, {}, {}, labels=PAINTED))
    assert [op.gh_ids for op in ops if isinstance(op, ReorderTasks)] == [("I_a", "I_b", "I_c")]


def test_a_blocker_outside_the_list_still_pushes_the_item_down():
    # Someone else's issue never shows up as a task, but it still has to close.
    free = item(gh_id="I_a", number=90)
    waiting = item(gh_id="I_b", number=10, blocked_by=(Ref("I_x", 5, "other/repo"),))
    ops = reconcile([waiting, free], Snapshot(ROOT, {}, {}, {}, labels=PAINTED))
    assert [op.gh_ids for op in ops if isinstance(op, ReorderTasks)] == [("I_a", "I_b")]


def test_a_dependency_cycle_terminates():
    a = item(gh_id="I_a", number=1, blocked_by=(Ref("I_b", 2, "rhseung/rds"),))
    b = item(gh_id="I_b", number=2, blocked_by=(Ref("I_a", 1, "rhseung/rds"),))
    ops = reconcile([a, b], Snapshot(ROOT, {}, {}, {}, labels=PAINTED))
    assert len([op for op in ops if isinstance(op, CreateTask)]) == 2


def test_a_blocked_item_says_so_in_its_labels_and_description():
    one = item(
        blocked_by=(Ref("I_x", 5, "rhseung/rds"), Ref("I_y", 7, "other/repo")),
        blocking=(Ref("I_z", 9, "rhseung/rds"),),
    )
    ops = reconcile([one], Snapshot(ROOT, {}, {}, {}, labels=PAINTED))
    created = next(op for op in ops if isinstance(op, CreateTask))
    assert created.description == "blocked by #5, other/repo#7\nblocks #9"
    assert LABEL_BLOCKED in created.labels


def test_an_unblocked_item_carries_no_blocked_label_and_no_description():
    ops = reconcile([item()], Snapshot(ROOT, {}, {}, {}, labels=PAINTED))
    created = next(op for op in ops if isinstance(op, CreateTask))
    assert created.description == ""
    assert LABEL_BLOCKED not in created.labels


def test_an_order_that_already_holds_is_left_alone():
    one = item()
    assert [op for op in reconcile([one], settled(one)) if isinstance(op, ReorderTasks)] == []


def test_what_frees_the_most_goes_first_among_equals():
    # Both can be started today; one clears the way for another, one for nobody.
    lone = item(gh_id="I_a", number=1)
    opener = item(gh_id="I_b", number=9, blocking=(Ref("I_c", 3, "rhseung/rds"),))
    waiting = item(gh_id="I_c", number=3, blocked_by=(Ref("I_b", 9, "rhseung/rds"),))
    ops = reconcile([lone, opener, waiting], Snapshot(ROOT, {}, {}, {}, labels=PAINTED))
    assert [op.gh_ids for op in ops if isinstance(op, ReorderTasks)] == [("I_b", "I_a", "I_c")]


def test_freeing_two_beats_freeing_one():
    # Same depth, same chain length: the count of what waits is what separates them.
    wide = item(
        gh_id="I_a",
        number=9,
        blocking=(Ref("I_c", 1, "rhseung/rds"), Ref("I_d", 2, "rhseung/rds")),
    )
    narrow = item(gh_id="I_b", number=3, blocking=(Ref("I_e", 4, "rhseung/rds"),))
    behind = [
        item(gh_id="I_c", number=1, blocked_by=(Ref("I_a", 9, "rhseung/rds"),)),
        item(gh_id="I_d", number=2, blocked_by=(Ref("I_a", 9, "rhseung/rds"),)),
        item(gh_id="I_e", number=4, blocked_by=(Ref("I_b", 3, "rhseung/rds"),)),
    ]
    ops = reconcile([narrow, wide, *behind], Snapshot(ROOT, {}, {}, {}, labels=PAINTED))
    order = next(op.gh_ids for op in ops if isinstance(op, ReorderTasks))
    assert order[:2] == ("I_a", "I_b")


def test_a_chain_outweighs_a_single_dependent():
    # A -> B -> C frees two in the end, so it beats D, which frees only one.
    a = item(gh_id="I_a", number=9, blocking=(Ref("I_b", 1, "rhseung/rds"),))
    b = item(
        gh_id="I_b",
        number=1,
        blocked_by=(Ref("I_a", 9, "rhseung/rds"),),
        blocking=(Ref("I_c", 2, "rhseung/rds"),),
    )
    c = item(gh_id="I_c", number=2, blocked_by=(Ref("I_b", 1, "rhseung/rds"),))
    d = item(gh_id="I_d", number=3, blocking=(Ref("I_e", 4, "rhseung/rds"),))
    e = item(gh_id="I_e", number=4, blocked_by=(Ref("I_d", 3, "rhseung/rds"),))
    ops = reconcile([d, a, b, c, e], Snapshot(ROOT, {}, {}, {}, labels=PAINTED))
    order = next(op.gh_ids for op in ops if isinstance(op, ReorderTasks))
    assert order[:2] == ("I_a", "I_d")


def test_the_most_blocked_sinks_to_the_bottom():
    # Both wait, but one waits on two things, so it is the furthest from ready.
    a = item(gh_id="I_a", number=1, blocking=(Ref("I_c", 3, "rhseung/rds"),))
    b = item(gh_id="I_b", number=2, blocking=(Ref("I_d", 4, "rhseung/rds"),))
    one = item(gh_id="I_c", number=3, blocked_by=(Ref("I_a", 1, "rhseung/rds"),))
    two = item(
        gh_id="I_d",
        number=4,
        blocked_by=(Ref("I_a", 1, "rhseung/rds"), Ref("I_b", 2, "rhseung/rds")),
    )
    ops = reconcile([two, one, b, a], Snapshot(ROOT, {}, {}, {}, labels=PAINTED))
    order = next(op.gh_ids for op in ops if isinstance(op, ReorderTasks))
    assert order[-1] == "I_d"
