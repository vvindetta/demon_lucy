"""Recognize the module's own email documents; this is not an engine directive."""

import os
import re

import yaml

from demon_lucy.modules.email.errors import EmailError
from demon_lucy.modules.email.files import read_bytes_no_follow

LITERAL_MARKER = "<!-- lucy-email -->"


class _HeadersLoader(yaml.SafeLoader):
    def compose_node(self, parent, index):
        if self.check_event(yaml.AliasEvent):
            raise ValueError("Aliases are not supported in email headers")
        self._depth = getattr(self, "_depth", 0) + 1
        try:
            if self._depth > 8:
                raise ValueError("Email headers are nested too deeply")
            return super().compose_node(parent, index)
        finally:
            self._depth -= 1

    def construct_mapping(self, node, deep=False):
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in result:
                raise ValueError("Unknown or duplicate email header")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def read_frontmatter(text: str) -> tuple[dict, str]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    header, separator, body = text[4:].partition("\n---\n")
    if not text.startswith("---\n") or not separator or len(header) > 65536:
        raise EmailError(
            "Use YAML headers between two --- lines.", reason="invalid_draft"
        )
    try:
        values = yaml.load(header, Loader=_HeadersLoader)
        if not isinstance(values, dict):
            raise ValueError("Email headers must be a mapping")
    except (yaml.YAMLError, ValueError, TypeError) as error:
        raise EmailError(
            "Invalid YAML email headers.", reason="invalid_draft"
        ) from error
    return values, body.removeprefix("\n")


def render_frontmatter(values: dict, body: str) -> str:
    headers = yaml.safe_dump(values, allow_unicode=True, sort_keys=False, width=1000)
    return "---\n" + headers + "---\n\n" + body


def document_identity(text: str) -> str | None:
    if text.startswith("---\n") or text.startswith("---\r\n"):
        try:
            values, _ = read_frontmatter(text)
        except EmailError:
            return None
        identity = values.get("id")
        return (
            identity
            if values.get("email") in ("draft", "message") and isinstance(identity, str)
            else None
        )
    lines = text.splitlines()[:2]
    if len(lines) == 2 and lines[0] == LITERAL_MARKER:
        match = re.fullmatch(r"<!-- lucy-email-id:([a-fA-F0-9-]+) -->", lines[1])
        return match.group(1) if match else None
    return None


def is_literal_text(text: str) -> bool:
    return (
        text.partition("\n")[0].removesuffix("\r") == LITERAL_MARKER
        or document_identity(text) is not None
    )


def is_literal_document(path: str | os.PathLike[str]) -> bool:
    try:
        if not os.path.isfile(path):
            return False
        return is_literal_text(
            read_bytes_no_follow(os.fspath(path), 128 * 1024 * 1024).decode("utf-8")
        )
    except (OSError, UnicodeDecodeError, ValueError):
        return False
