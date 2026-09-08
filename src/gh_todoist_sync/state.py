"""Which Todoist object stands for which piece of GitHub.

This mapping used to live in Todoist's own description fields, which meant every
task carried a line of `gh-id: ...` that the user had to look at. It lives in one
local file instead, so the descriptions hold only what a person would want to
read.

The trade is that this file is now load bearing: lose it and the next run does
not recognise the tree it built, so it builds a second one alongside. It is
small and rewritten on every run, so back it up with the rest of the home
directory and that stays theoretical.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

PATH = Path.home() / ".local/state/gh-todoist-sync/state.json"


@dataclass(slots=True)
class State:
    root: str | None = None
    orgs: dict[str, str] = field(default_factory=dict)  # owner_id -> project id
    sections: dict[str, str] = field(default_factory=dict)  # repo_id -> section id
    tasks: dict[str, str] = field(default_factory=dict)  # gh_id -> task id
    empty_since: dict[str, date] = field(default_factory=dict)  # todoist id -> first seen empty

    def forget(self, todoist_id: str) -> None:
        """Drop every trace of one Todoist object, whatever kind it was."""
        for mapping in (self.orgs, self.sections, self.tasks):
            for key, value in list(mapping.items()):
                if value == todoist_id:
                    del mapping[key]
        self.empty_since.pop(todoist_id, None)
        if self.root == todoist_id:
            self.root = None


def load(path: Path = PATH) -> State:
    if not path.exists():
        return State()
    raw = json.loads(path.read_text())
    return State(
        root=raw.get("root"),
        orgs=raw.get("orgs", {}),
        sections=raw.get("sections", {}),
        tasks=raw.get("tasks", {}),
        empty_since={k: date.fromisoformat(v) for k, v in raw.get("empty_since", {}).items()},
    )


def save(state: State, path: Path = PATH) -> None:
    # Written beside the target and renamed over it: a crash partway through
    # leaves the previous file intact rather than a truncated one.
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "root": state.root,
        "orgs": state.orgs,
        "sections": state.sections,
        "tasks": state.tasks,
        "empty_since": {k: v.isoformat() for k, v in state.empty_since.items()},
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(body, indent=2, ensure_ascii=False) + "\n")
    tmp.replace(path)
