from __future__ import annotations

import os
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from demon_lucy.modules.git.helpers import format_path_for_commit_message
from demon_lucy.modules.git.types import _RepoBatch

_ACTION_ORDER = ("modified", "added", "deleted", "renamed", "copied", "type_changed")
_ACTION_VERBS = {
    "added": "add",
    "modified": "modify",
    "deleted": "delete",
    "renamed": "rename",
    "copied": "copy",
    "type_changed": "change type",
}
_ACTION_HEADINGS = {
    "added": "Added",
    "modified": "Modified",
    "deleted": "Deleted",
    "renamed": "Renamed",
    "copied": "Copied",
    "type_changed": "Type changed",
}


@dataclass(frozen=True)
class GitChange:
    action: str
    path: str
    old_path: str = ""
    additions: Optional[int] = None
    deletions: Optional[int] = None
    binary: bool = False


@dataclass(frozen=True)
class CommitMessage:
    subject: str
    body: str = ""

    def to_git_args(self) -> list[str]:
        args = ["commit", "-m", self.subject]
        if self.body.strip():
            args.extend(["-m", self.body])
        return args

    def as_text(self) -> str:
        if not self.body.strip():
            return self.subject
        return f"{self.subject}\n\n{self.body}"


def _split_z(text: str) -> list[str]:
    if not text:
        return []
    items = text.split("\0")
    if items and items[-1] == "":
        items.pop()
    return items


def _normalize_path(path_text: str, repo_root: str) -> str:
    path_text = format_path_for_commit_message(path_text or "").strip()
    if not path_text:
        return ""

    if repo_root and os.path.isabs(path_text):
        try:
            rel_path = os.path.relpath(path_text, repo_root)
        except ValueError:
            rel_path = path_text
        if not rel_path.startswith(".."):
            path_text = rel_path

    return path_text.replace(os.sep, "/")


def _action_from_status(status_text: str) -> str:
    status = (status_text or "").strip().upper()
    if status.startswith("A"):
        return "added"
    if status.startswith("D"):
        return "deleted"
    if status.startswith("R"):
        return "renamed"
    if status.startswith("C"):
        return "copied"
    if status.startswith("T"):
        return "type_changed"
    return "modified"


def parse_name_status_z(text: str, repo_root: str = "") -> list[GitChange]:
    tokens = _split_z(text)
    changes: list[GitChange] = []
    index = 0
    while index < len(tokens):
        status_token = tokens[index]
        index += 1
        if not status_token:
            continue

        action = _action_from_status(status_token)
        if action in {"renamed", "copied"}:
            if index + 1 >= len(tokens):
                break
            old_path = _normalize_path(tokens[index], repo_root)
            path = _normalize_path(tokens[index + 1], repo_root)
            index += 2
            changes.append(GitChange(action=action, path=path, old_path=old_path))
            continue

        if index >= len(tokens):
            break
        path = _normalize_path(tokens[index], repo_root)
        index += 1
        changes.append(GitChange(action=action, path=path))
    return changes


def _parse_numstat_value(value: str) -> tuple[Optional[int], bool]:
    if value == "-":
        return None, True
    try:
        return int(value), False
    except ValueError:
        return None, False


def _numstat_key(path: str, old_path: str = "") -> tuple[str, str]:
    return path, old_path


def parse_numstat_z(
    text: str, repo_root: str = ""
) -> dict[tuple[str, str], tuple[Optional[int], Optional[int], bool]]:
    tokens = _split_z(text)
    stats: dict[tuple[str, str], tuple[Optional[int], Optional[int], bool]] = {}
    index = 0
    while index < len(tokens):
        record = tokens[index]
        index += 1
        if not record:
            continue

        parts = record.split("\t")
        if len(parts) < 2:
            continue

        additions, additions_binary = _parse_numstat_value(parts[0])
        deletions, deletions_binary = _parse_numstat_value(parts[1])
        binary = additions_binary or deletions_binary

        if len(parts) >= 3 and parts[2]:
            path = _normalize_path("\t".join(parts[2:]), repo_root)
            stats[_numstat_key(path)] = (additions, deletions, binary)
            continue

        if index + 1 >= len(tokens):
            continue
        old_path = _normalize_path(tokens[index], repo_root)
        path = _normalize_path(tokens[index + 1], repo_root)
        index += 2
        stats[_numstat_key(path, old_path)] = (additions, deletions, binary)
        stats[_numstat_key(path)] = (additions, deletions, binary)

    return stats


def changes_from_staged_diff(
    *,
    name_status_z: str,
    numstat_z: str,
    repo_root: str,
) -> list[GitChange]:
    changes = parse_name_status_z(name_status_z, repo_root=repo_root)
    stats = parse_numstat_z(numstat_z, repo_root=repo_root)
    out: list[GitChange] = []

    for change in changes:
        stat = stats.get(_numstat_key(change.path, change.old_path))
        if stat is None:
            stat = stats.get(_numstat_key(change.path))
        if stat is None:
            out.append(change)
            continue
        additions, deletions, binary = stat
        out.append(
            GitChange(
                action=change.action,
                path=change.path,
                old_path=change.old_path,
                additions=additions,
                deletions=deletions,
                binary=binary,
            )
        )
    return out


def _fallback_action(event_type: str) -> str:
    normalized = (event_type or "").strip().lower()
    if normalized == "created":
        return "added"
    if normalized == "deleted":
        return "deleted"
    return "modified"


def fallback_changes_from_paths(
    *,
    paths: list[str],
    repo_root: str,
    event_type: str,
) -> list[GitChange]:
    action = _fallback_action(event_type)
    return [
        GitChange(action=action, path=path)
        for path in [_normalize_path(path_item, repo_root) for path_item in paths]
        if path
    ]


def _ordered_actions(changes: list[GitChange]) -> list[str]:
    seen = {change.action for change in changes}
    ordered = [action for action in _ACTION_ORDER if action in seen]
    ordered.extend(sorted(seen - set(ordered)))
    return ordered


def _path_label(change: GitChange) -> str:
    if change.action in {"renamed", "copied"} and change.old_path:
        return f"{change.old_path} -> {change.path}"
    return change.path


def _stats_label(change: GitChange) -> str:
    if change.binary:
        return " (binary)"
    if change.additions is None and change.deletions is None:
        return ""
    additions = 0 if change.additions is None else change.additions
    deletions = 0 if change.deletions is None else change.deletions
    return f" (+{additions}/-{deletions})"


def _plural_files(count: int) -> str:
    return "file" if count == 1 else "files"


def _subject_for_changes(batch: _RepoBatch, changes: list[GitChange]) -> str:
    max_files = max(1, int(batch.commit_message_max_subject_files))
    if len(changes) == 1:
        change = changes[0]
        verb = _ACTION_VERBS.get(change.action, "update")
        return f"{batch.base_message}: {verb} {_path_label(change)}"

    if len(changes) <= max_files:
        grouped: dict[str, list[str]] = defaultdict(list)
        for change in changes:
            grouped[change.action].append(_path_label(change))

        parts = []
        for action in _ordered_actions(changes):
            verb = _ACTION_VERBS.get(action, "update")
            parts.append(f"{verb} {', '.join(grouped[action])}")
        return f"{batch.base_message}: {'; '.join(parts)}"

    counts = Counter(change.action for change in changes)
    summary_parts = []
    for action in _ordered_actions(changes):
        verb = _ACTION_VERBS.get(action, "update")
        summary_parts.append(f"{verb} {counts[action]}")
    return (
        f"{batch.base_message}: sync {len(changes)} {_plural_files(len(changes))}: "
        + ", ".join(summary_parts)
    )


def _triggered_paths(batch: _RepoBatch) -> list[str]:
    return [
        path
        for path in [
            _normalize_path(path_item, batch.repo_root) for path_item in batch.hinted_paths
        ]
        if path
    ]


def _body_for_changes(batch: _RepoBatch, changes: list[GitChange]) -> str:
    style = str(batch.commit_message_style).strip().lower()
    if style == "compact":
        return ""

    max_body_files = max(1, int(batch.commit_message_max_body_files))
    lines = [
        f"Event: {batch.event_type or 'change'}",
        f"Repository: {batch.repo_root}",
        f"Changed: {len(changes)} {_plural_files(len(changes))}",
        "",
    ]

    emitted = 0
    for action in _ordered_actions(changes):
        group = [change for change in changes if change.action == action]
        if not group:
            continue
        heading = _ACTION_HEADINGS.get(action, "Updated")
        lines.append(f"{heading}:")
        for change in group:
            if emitted >= max_body_files:
                break
            lines.append(f"- {_path_label(change)}{_stats_label(change)}")
            emitted += 1
        lines.append("")
        if emitted >= max_body_files:
            break

    remaining = len(changes) - emitted
    if remaining > 0:
        lines.append(f"... +{remaining} more")
        lines.append("")

    triggered = _triggered_paths(batch)
    if triggered:
        lines.append("Triggered by:")
        for path in triggered[:max_body_files]:
            lines.append(f"- {path}")
        if len(triggered) > max_body_files:
            lines.append(f"... +{len(triggered) - max_body_files} more")

    return "\n".join(lines).strip()


def _append_timestamp(batch: _RepoBatch, subject: str) -> str:
    if not batch.add_timestamp_to_message:
        return subject
    return f"{subject} [{datetime.now().strftime(batch.timestamp_format)}]"


def build_commit_message(
    batch: _RepoBatch,
    changed_paths: list[str],
    changes: Optional[list[GitChange]] = None,
) -> CommitMessage:
    usable_changes = list(changes or [])
    if not usable_changes:
        paths = changed_paths or batch.hinted_paths
        usable_changes = fallback_changes_from_paths(
            paths=paths,
            repo_root=batch.repo_root,
            event_type=batch.event_type,
        )

    if not usable_changes:
        subject = f"{batch.base_message}: {batch.event_type or 'change'}"
        return CommitMessage(subject=_append_timestamp(batch, subject))

    subject = _append_timestamp(batch, _subject_for_changes(batch, usable_changes))
    return CommitMessage(subject=subject, body=_body_for_changes(batch, usable_changes))
