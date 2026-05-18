from __future__ import annotations

from lucy_notes_manager.modules.git.types import _RepoBatch

_PULL_ONLY_EVENT_TYPES = {"opened", "scheduled_pull"}


def should_force_flush_batch(batch: _RepoBatch, now_timestamp: float) -> bool:
    if batch.max_batch_seconds <= 0.0:
        return False
    if not batch.event_types:
        return False
    if batch.event_types.issubset(_PULL_ONLY_EVENT_TYPES):
        return False
    return (now_timestamp - batch.first_event_at) >= batch.max_batch_seconds


def update_periodic_pull_state(
    self, repo_root: str, config_snapshot: dict, now_timestamp: float
) -> None:
    interval_seconds = config_snapshot["git_pull_interval_hours"] * 3600.0

    if interval_seconds <= 0.0:
        self._periodic_pull_next_at.pop(repo_root, None)
        self._periodic_pull_intervals_seconds.pop(repo_root, None)
        self._periodic_pull_configs.pop(repo_root, None)
        return

    self._periodic_pull_intervals_seconds[repo_root] = interval_seconds
    self._periodic_pull_configs[repo_root] = dict(config_snapshot)

    if repo_root not in self._periodic_pull_next_at:
        self._periodic_pull_next_at[repo_root] = now_timestamp + interval_seconds


def collect_due_periodic_pull_events(
    self, now_timestamp: float
) -> list[tuple[str, str, list[str], dict, bool]]:
    events: list[tuple[str, str, list[str], dict, bool]] = []

    for repo_root, next_allowed in list(self._periodic_pull_next_at.items()):
        if now_timestamp < next_allowed:
            continue

        interval_seconds = self._periodic_pull_intervals_seconds.get(repo_root, 0.0)
        config_snapshot = self._periodic_pull_configs.get(repo_root)

        if interval_seconds <= 0.0 or not isinstance(config_snapshot, dict):
            self._periodic_pull_next_at.pop(repo_root, None)
            self._periodic_pull_intervals_seconds.pop(repo_root, None)
            self._periodic_pull_configs.pop(repo_root, None)
            continue

        self._periodic_pull_next_at[repo_root] = now_timestamp + interval_seconds
        events.append((repo_root, "scheduled_pull", [], dict(config_snapshot), True))

    return events
