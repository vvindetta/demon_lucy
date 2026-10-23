from pathlib import Path

import pytest
from watchdog.events import FileCreatedEvent, FileModifiedEvent, FileMovedEvent

from demon_lucy.file_handler import FileHandler
from demon_lucy.lib.args.parser import parse_args
from demon_lucy.lib.dynamic_blocks.parser import format_dynamic_block
from demon_lucy.module_manager import ModuleManager
from demon_lucy.modules.alias import Alias
from demon_lucy.modules.formatter import Formatter
from demon_lucy.modules.include import Include
from demon_lucy.modules.new import New
from demon_lucy.modules.renamer import Renamer
from demon_lucy.runtime import DEMON_LUCY_STARTUP_TEMPLATE, select_demon_lucy_modules


def manager(*extra_modules, flags=()):
    modules = [New(), *extra_modules]
    template = DEMON_LUCY_STARTUP_TEMPLATE + [
        argument for module in modules for argument in module.template
    ]
    return ModuleManager(
        modules=modules,
        startup_args=parse_args(
            template=template,
            args=["--sys-notification-provider", "disable", *flags],
        ),
    )


def run(note, instance=None, event=FileModifiedEvent):
    return (instance or manager()).run(str(note), event(str(note)), event_id="evt-new")


@pytest.mark.parametrize("event", [FileCreatedEvent, FileModifiedEvent, FileMovedEvent])
def test_new_moves_body_and_leaves_link(tmp_path, event):
    note = tmp_path / "note.md"
    note.write_text('before\n--new "Идея"\nbody\n\nsecond\n--end\nafter\n')
    trigger = (
        event(str(note))
        if event != FileMovedEvent
        else event(str(tmp_path / ".save"), str(note))
    )
    changed = manager().run(str(note), trigger)
    target = tmp_path / "Идея.md"
    assert target.read_bytes() == b"body\n\nsecond\n"
    assert note.read_text() == "before\n[Идея](Идея.md)\nafter\n"
    assert changed == {str(note): 1, str(target): 1}
    assert run(note) is None


@pytest.mark.parametrize(
    ("source", "body", "replacement"),
    [
        ("--new result\nbody", b"body", "[result](result.md)"),
        (
            "--new result\r\n\tfirst\r\n\r\nlast\r\n",
            b"\tfirst\r\n\r\nlast\r\n",
            "[result](result.md)\r\n",
        ),
        ("--new=result", b"", "[result](result.md)"),
        ("--new result\n--end\n", b"", "[result](result.md)\n"),
        (
            "--new result\n--new nested.md\nliteral\n",
            b"--new nested.md\nliteral\n",
            "[result](result.md)\n",
        ),
    ],
)
def test_new_preserves_body_bytes_and_optional_end(tmp_path, source, body, replacement):
    note = tmp_path / "note.md"
    note.write_bytes(source.encode())
    assert run(note)
    assert (tmp_path / "result.md").read_bytes() == body
    assert note.read_bytes() == replacement.encode()
    assert not (tmp_path / "nested.md").exists()


def test_new_multiple_blocks_paths_and_extensions(tmp_path):
    note = tmp_path / "note.md"
    absolute = tmp_path / "absolute.txt"
    note.write_text(
        f'--new "folder/Идея [1] (#%)?.txt"\none\n--end\nkeep\n--new "{absolute}"\ntwo'
    )
    assert run(note)
    assert (tmp_path / "folder/Идея [1] (#%)?.txt").read_text() == "one\n"
    assert absolute.read_text() == "two"
    assert (
        note.read_text()
        == "[Идея \\[1\\] (#%)?](folder/Идея%20[1]%20%28%23%25%29%3F.txt)\nkeep\n[absolute](absolute.txt)"
    )


def test_new_ignores_examples_and_keeps_literal_end_markers(tmp_path):
    generated = format_dynamic_block(
        arg="example", params={}, body="--new forbidden\n--end"
    )
    examples = "```text\n--new example\n--end\n```\n    --new indented\n" + generated
    body = "```text\n--- example begin ---\n--end\n```\n" + generated
    note = tmp_path / "note.md"
    note.write_text(examples + "--new result\n" + body + "--end\nafter\n")
    assert run(note)
    assert (tmp_path / "result.md").read_text() == body
    assert note.read_text() == examples + "[result](result.md)\nafter\n"
    assert not (tmp_path / "forbidden.md").exists()


def test_new_filename_with_colon_is_a_local_link(tmp_path):
    note = tmp_path / "note.md"
    note.write_text('--new "12:00"\nbody\n')
    assert run(note)
    assert (tmp_path / "12:00.md").read_text() == "body\n"
    assert note.read_text() == "[12:00](./12:00.md)\n"


@pytest.mark.parametrize(
    "header",
    [
        "--new",
        '--new ""',
        '--new "unfinished',
        "--new one two",
        "--new folder/",
        "--new .",
        "--new note.md",
        "--new one --rename moved.md",
    ],
)
def test_new_invalid_command_keeps_source(tmp_path, header):
    note = tmp_path / "note.md"
    source = header + "\nbody\n--end\n"
    note.write_text(source)
    assert run(note) is None
    assert note.read_text() == source
    assert list(tmp_path.iterdir()) == [note]


@pytest.mark.parametrize("existing", ["file", "directory", "symlink", "dangling"])
def test_new_conflicts_do_not_overwrite_or_partially_extract(tmp_path, existing):
    note = tmp_path / "note.md"
    target = tmp_path / "result.md"
    if existing == "file":
        target.write_text("existing")
    elif existing == "directory":
        target.mkdir()
    else:
        other = tmp_path / "other.md"
        if existing == "symlink":
            other.write_text("existing")
        target.symlink_to(other)
    source = "--new first\none\n--end\n--new result\ntwo\n"
    note.write_text(source)
    assert run(note) is None
    assert note.read_text() == source
    assert not (tmp_path / "first.md").exists()
    if existing in {"file", "symlink"}:
        assert target.read_text() == "existing"


def test_new_rejects_duplicate_destinations(tmp_path):
    note = tmp_path / "note.md"
    source = "--new result\none\n--end\n--new ./result.md\ntwo\n"
    note.write_text(source)
    assert run(note) is None
    assert note.read_text() == source
    assert not (tmp_path / "result.md").exists()


def test_invalid_new_header_does_not_run_trailing_commands(tmp_path):
    note = tmp_path / "note.md"
    source = "--new result --rename wrong.md\nbody\n"
    note.write_text(source)
    assert run(note, manager(Renamer())) is None
    assert note.read_text() == source
    assert list(tmp_path.iterdir()) == [note]


def test_new_body_is_not_source_arguments_or_alias_input(tmp_path):
    note = tmp_path / "note.md"
    body = "--rename wrong.md\n--alias-text word=wrong\n--b hello\nword\n- task\n"
    source = "--new result\n" + body
    note.write_text(source)
    instance = manager(
        Alias(),
        Renamer(),
        Formatter(),
        flags=(
            "--alias-command",
            "b=--banner",
            "--alias-text",
            "word=changed",
            "--formatter-todo",
        ),
    )
    assert run(note, instance)
    assert note.read_text() == "[result](result.md)\n"
    assert (tmp_path / "result.md").read_text() == body
    assert not (tmp_path / "wrong.md").exists()


def test_new_command_alias_protects_following_body(tmp_path):
    note = tmp_path / "note.md"
    body = "--rename wrong.md\n--n nested\nword\n"
    note.write_text("--n result\n" + body)
    instance = manager(
        Alias(),
        Renamer(),
        flags=("--alias-command", "n=--new {args}", "--alias-text", "word=changed"),
    )
    assert run(note, instance)
    assert (tmp_path / "result.md").read_text() == body
    assert note.read_text() == "[result](result.md)\n"


@pytest.mark.parametrize("file_rule", [False, True])
def test_new_alias_body_cannot_override_alias_definitions(tmp_path, file_rule):
    note = tmp_path / "note.md"
    body = '--alias-command "n=--rename {args}"\n--alias-text "word=wrong"\nword\n'
    prefix = '--alias-command "n=--new {args}"\n' if file_rule else ""
    note.write_text(prefix + "word\n--n result\n" + body)
    flags = ["--alias-text", "word=changed"]
    if not file_rule:
        flags.extend(["--alias-command", "n=--new {args}"])
    assert run(note, manager(Alias(), Renamer(), flags=flags))
    assert note.exists()
    assert note.read_text() == prefix + "changed\n[result](result.md)\n"
    assert (tmp_path / "result.md").read_text() == body


def test_failed_new_keeps_body_protected_from_other_modules(tmp_path):
    note = tmp_path / "note.md"
    (tmp_path / "result.md").write_text("existing")
    source = "--new result\n--rename wrong.md\n- task\nword\n"
    note.write_text(source)
    instance = manager(
        Alias(),
        Renamer(),
        Formatter(),
        flags=("--alias-text", "word=changed", "--formatter-todo"),
    )
    assert run(note, instance) is None
    assert note.read_text() == source


def test_failed_new_does_not_refresh_generated_content_inside_body(tmp_path):
    note = tmp_path / "note.md"
    (tmp_path / "result.md").write_text("existing")
    (tmp_path / "included.md").write_text("changed")
    source = "--new result\n" + format_dynamic_block(
        arg="include", params={"source": "included.md"}, body="\told"
    )
    note.write_text(source)
    assert run(note, manager(Include())) is None
    assert note.read_text() == source


def test_new_creation_event_is_suppressed_but_next_save_runs_commands(tmp_path):
    note = tmp_path / "note.md"
    note.write_text("--new result\n--rename changed.md\n")
    handler = FileHandler(manager(Renamer()), open_cooldown_seconds=0)
    handler.on_modified(FileModifiedEvent(str(note)))
    target = tmp_path / "result.md"
    assert note.exists() and target.exists()
    handler.on_created(FileCreatedEvent(str(target)))
    handler.on_moved(FileMovedEvent(str(tmp_path / ".temporary"), str(note)))
    assert target.exists()
    assert handler._ignore_paths == {}
    handler.on_modified(FileModifiedEvent(str(target)))
    assert not target.exists()
    assert (tmp_path / "changed.md").exists()


@pytest.mark.parametrize("failure", ["create_second", "write_source", "source_changed"])
def test_new_rolls_back_created_files_on_failure(tmp_path, monkeypatch, failure):
    note = tmp_path / "note.md"
    source = "--new first\none\n--end\n--new second\ntwo\n"
    note.write_text(source)
    from demon_lucy.modules.new import module

    create = module.create_bytes_atomic
    write = module.write_text_atomic

    def create_file(path, content):
        if failure == "create_second" and path.endswith("second.md"):
            raise OSError("disk full")
        return create(path, content)

    def write_source(path, text, **kwargs):
        if failure == "write_source":
            raise OSError("write failed")
        if failure == "source_changed":
            note.write_text("outside edit")
        return write(path, text, **kwargs)

    monkeypatch.setattr(module, "create_bytes_atomic", create_file)
    monkeypatch.setattr(module, "write_text_atomic", write_source)
    assert run(note) is None
    assert note.read_text() == (
        "outside edit" if failure == "source_changed" else source
    )
    assert list(tmp_path.iterdir()) == [note]


def test_new_rollback_preserves_external_target_changes(tmp_path, monkeypatch, caplog):
    note = tmp_path / "note.md"
    source = "--new result\nbody\n"
    note.write_text(source)
    target = tmp_path / "result.md"

    def fail(*args, **kwargs):
        target.write_text("outside edit")
        raise OSError("write failed")

    monkeypatch.setattr("demon_lucy.modules.new.module.write_text_atomic", fail)
    assert run(note) == {str(target): 1}
    assert target.read_text() == "outside edit"
    assert note.read_text() == source
    assert "retained=" in caplog.text


def test_new_is_available_and_enabled_by_default():
    defaults = parse_args(template=DEMON_LUCY_STARTUP_TEMPLATE, args=[])
    assert "new" in defaults.require("sys-modules").value
    assert any(
        isinstance(module, New)
        for module in select_demon_lucy_modules(include_names=["new"])
    )
