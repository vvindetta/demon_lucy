import shlex
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

from demon_lucy.lib.args.models import Template
from demon_lucy.lib.args.parser import split_arg_line


@dataclass(frozen=True)
class ArgSegment:
    flag: str
    values: Tuple[str, ...] = ()

    @property
    def tokens(self) -> List[str]:
        return [self.flag, *self.values]

    def with_flag(self, flag: str) -> "ArgSegment":
        return ArgSegment(flag=flag, values=self.values)


ArgSegmentMigrator = Callable[[ArgSegment], Tuple[Optional[ArgSegment], bool]]


def looks_like_arg_flag(token: str) -> bool:
    return token.startswith("--") and len(token) > 2


def split_arg_segments(tokens: List[str]) -> List[ArgSegment]:
    segments: List[ArgSegment] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if not looks_like_arg_flag(token):
            index += 1
            continue

        values: List[str] = []
        index += 1
        while index < len(tokens) and not looks_like_arg_flag(tokens[index]):
            values.append(tokens[index])
            index += 1
        segments.append(ArgSegment(flag=token, values=tuple(values)))
    return segments


def flatten_arg_segments(segments: Iterable[ArgSegment]) -> List[str]:
    return [token for segment in segments for token in segment.tokens]


def render_arg_segments(segments: Iterable[ArgSegment]) -> str:
    return shlex.join(flatten_arg_segments(segments))


def migrate_arg_line_segments(
    line: str,
    *,
    migrate_segment: ArgSegmentMigrator,
    candidate_flags: Iterable[str] = (),
) -> Tuple[str, bool]:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return line, False

    candidate_flag_set = set(candidate_flags)
    if candidate_flag_set and not any(flag in line for flag in candidate_flag_set):
        return line, False

    newline = "\n" if line.endswith("\n") else ""
    try:
        tokens = split_arg_line(stripped)
    except ValueError:
        return line, False

    changed = False
    migrated_segments: List[ArgSegment] = []
    for segment in split_arg_segments(tokens):
        migrated_segment, segment_changed = migrate_segment(segment)
        changed = changed or segment_changed
        if migrated_segment is not None:
            migrated_segments.append(migrated_segment)

    if not changed:
        return line, False
    return render_arg_segments(migrated_segments) + newline, True


def delete_args_from_string(
    line: str, flags: Iterable[str], *, template: Template | None = None
) -> str:
    """
    Remove flags and their values from a single line.

    - flags: flags to remove (e.g. ["--banner"])
    - If removed flag is in form "--flag=value" -> removed fully.
    - If removed flag is in form "--flag" -> removes ALL following value tokens
      until the next flag-like token (greedy).
    - Preserves trailing newline automatically.
    - With a template, respects literal values of removed and retained flags.

    Heuristic for "flag-like token":
      --something  -> flag
      -s           -> flag
      but NOT negative numbers like -1, -2.5
    """

    def looks_like_flag(token: str) -> bool:
        if token.startswith("--") and len(token) > 2:
            return True
        if token.startswith("-") and len(token) > 1:
            return not (token[1].isdigit() or token[1] == ".")
        return False

    newline = "\n" if line.endswith("\n") else ""
    raw = line[:-1] if newline else line
    if not raw:
        return line

    remove = set(flags)
    if not remove:
        return line
    if not any(flag in raw for flag in remove):
        return line

    tokens = split_arg_line(raw)
    schemas = {f"--{item.name}": item for item in template or ()}

    out: List[str] = []
    removed_any = False
    i = 0
    while i < len(tokens):
        tok = tokens[i]

        # handle --flag=value by checking only the head part
        head = tok.split("=", 1)[0] if tok.startswith("-") else tok
        schema = schemas.get(head)
        literal_count = 0
        if schema is not None:
            literal_count = schema.literal_value_count or int(
                schema.literal_first_value
            )
        if "=" in tok:
            literal_count = max(0, literal_count - 1)

        if head in remove:
            removed_any = True
            i = min(len(tokens), i + 1 + literal_count)

            if schema is not None and schema.literal_value_count:
                continue
            if "=" in tok and not (schema and schema.literal_first_value):
                continue

            # consume value tokens until next flag-like token
            while i < len(tokens) and not looks_like_flag(tokens[i]):
                i += 1
            continue

        out.append(tok)
        i += 1
        out.extend(tokens[i : i + literal_count])
        i += literal_count

    if not removed_any:
        return line

    return (shlex.join(out) if out else "") + newline
