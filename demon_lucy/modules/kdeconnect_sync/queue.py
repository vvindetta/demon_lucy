from __future__ import annotations

import os

from demon_lucy.lib.path import git_dir_for_repo_root


def queue_root_for_repo(repo_root: str, queue_dir_name: str) -> str:
    return os.path.normpath(os.path.join(repo_root, queue_dir_name))


def outgoing_pc_to_phone_dir(repo_root: str, queue_dir_name: str) -> str:
    return os.path.join(
        queue_root_for_repo(repo_root, queue_dir_name), "outgoing_pc_to_phone"
    )


def is_queue_internal_path(
    path_value: str, repo_root: str, queue_dir_name: str
) -> bool:
    queue_root = queue_root_for_repo(repo_root, queue_dir_name)
    path_norm = os.path.normpath(path_value)
    return path_norm == queue_root or path_norm.startswith(queue_root + os.sep)


def ensure_queue_excluded_in_repo(repo_root: str, queue_dir_name: str) -> None:
    git_dir = git_dir_for_repo_root(repo_root)
    if not git_dir:
        return

    git_exclude_path = os.path.join(git_dir, "info", "exclude")
    os.makedirs(os.path.dirname(git_exclude_path), exist_ok=True)

    normalized_queue_rel = queue_dir_name.strip().replace("\\", "/").strip("/")
    if not normalized_queue_rel:
        return

    required_pattern = f"{normalized_queue_rel}/"

    existing_lines: list[str] = []
    try:
        with open(git_exclude_path, "r", encoding="utf-8") as file_handle:
            existing_lines = [line.rstrip("\n") for line in file_handle.readlines()]
    except FileNotFoundError:
        existing_lines = []

    if required_pattern in existing_lines:
        return

    with open(git_exclude_path, "a", encoding="utf-8") as file_handle:
        if existing_lines and existing_lines[-1].strip():
            file_handle.write("\n")
        file_handle.write("# demon_lucy kdeconnect patch queue\n")
        file_handle.write(required_pattern + "\n")
