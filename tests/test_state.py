"""The state file is now the only record of what maps to what, so it gets one."""

from __future__ import annotations

from contextlib import suppress
from datetime import date

from gh_todoist_sync.state import State, load, save


def test_a_missing_file_reads_as_an_empty_state(tmp_path):
    # First ever run: nothing to recognise, so the tree gets built from scratch.
    assert load(tmp_path / "absent.json") == State()


def test_a_saved_state_reads_back_the_same(tmp_path):
    path = tmp_path / "state.json"
    before = State(
        root="R",
        orgs={"8": "P"},
        sections={"1": "S"},
        tasks={"I_a": "T"},
        empty_since={"S": date(2026, 9, 8)},
    )
    save(before, path)
    assert load(path) == before


def test_forget_drops_an_object_whatever_kind_it_was():
    state = State(root="R", orgs={"8": "P"}, sections={"1": "S"}, tasks={"I_a": "T"})
    state.empty_since["S"] = date(2026, 9, 8)

    state.forget("S")
    assert state.sections == {} and state.empty_since == {}

    state.forget("T")
    assert state.tasks == {}

    # Losing the root is what makes the next run rebuild the whole tree.
    state.forget("R")
    assert state.root is None


def test_saving_leaves_the_old_file_intact_if_it_cannot_finish(tmp_path):
    path = tmp_path / "state.json"
    save(State(root="R"), path)
    with suppress(AttributeError):
        save(State(root="R", empty_since={"S": "not a date"}), path)  # type: ignore[dict-item]
    assert load(path).root == "R"
