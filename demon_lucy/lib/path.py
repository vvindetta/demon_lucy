from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


def abs_expand_path(path_value: str) -> str:
    return os.path.abspath(os.path.expanduser(path_value))


def canonical_path(path_value: str) -> str:
    return os.path.realpath(os.path.normpath(abs_expand_path(path_value)))


def path_has_component(path_value: str, component: str) -> bool:
    path_components = abs_expand_path(path_value).split(os.sep)
    return component in path_components


def path_is_inside(path_value: str, root_value: str) -> bool:
    path = canonical_path(path_value)
    root = canonical_path(root_value)
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:
        return False


def find_parent_with(path_value: str, marker_name: str) -> Optional[str]:
    """
    Walk up from a file or directory path and return the first parent directory
    that contains `marker_name` as a directory.

    Example:
        find_parent_with("/notes/repo/docs/todo.md", ".git") -> "/notes/repo"

    Returns None when no such parent exists.
    """
    current_path = abs_expand_path(path_value)
    if not os.path.isdir(current_path):
        current_path = os.path.dirname(current_path)

    while True:
        if os.path.isdir(os.path.join(current_path, marker_name)):
            return current_path
        parent_path = os.path.dirname(current_path)
        if parent_path == current_path:
            return None
        current_path = parent_path


def git_dir_for_repo_root(repo_root: str) -> Optional[str]:
    repo_path = abs_expand_path(repo_root)
    git_marker_path = os.path.join(repo_path, ".git")

    if os.path.isdir(git_marker_path):
        head_path = os.path.join(git_marker_path, "HEAD")
        if os.path.isfile(head_path):
            return git_marker_path
        return None

    if not os.path.isfile(git_marker_path):
        return None

    try:
        with open(git_marker_path, "r", encoding="utf-8") as handle:
            first_line = handle.readline().strip()
    except OSError:
        return None

    prefix = "gitdir:"
    if not first_line.startswith(prefix):
        return None

    git_dir_value = first_line[len(prefix) :].strip()
    if not git_dir_value:
        return None

    git_dir_path = git_dir_value
    if not os.path.isabs(git_dir_path):
        git_dir_path = os.path.join(repo_path, git_dir_path)
    git_dir_path = abs_expand_path(git_dir_path)

    if not os.path.isdir(git_dir_path):
        return None

    head_path = os.path.join(git_dir_path, "HEAD")
    if not os.path.isfile(head_path):
        return None
    return git_dir_path


def find_parent_git_repo(path_value: str) -> Optional[str]:
    current_path = abs_expand_path(path_value)
    if not os.path.isdir(current_path):
        current_path = os.path.dirname(current_path)

    while True:
        if git_dir_for_repo_root(current_path) is not None:
            return current_path
        parent_path = os.path.dirname(current_path)
        if parent_path == current_path:
            return None
        current_path = parent_path


def resolve_file_source_path(*, source: str, target_path: str) -> str:
    if source.startswith("~"):
        raise ValueError("source path must not use '~'")

    target = canonical_path(target_path)
    target_dir = os.path.dirname(target)
    allowed_root = find_parent_git_repo(target) or target_dir
    source_path = Path(source)
    if not source_path.is_absolute():
        source_path = Path(target_dir) / source_path
    resolved = canonical_path(str(source_path))
    if not path_is_inside(resolved, allowed_root):
        raise ValueError("source path is outside the allowed root")
    return resolved
