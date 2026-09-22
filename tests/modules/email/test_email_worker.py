from dataclasses import replace
from pathlib import Path
import subprocess
import sys
import time

import pytest
from watchdog.events import DirMovedEvent, FileMovedEvent

from demon_lucy.lib.args.parser import parse_args
from demon_lucy.module_manager import ModuleManager
from demon_lucy.modules.abstract_module import AbstractModule
from demon_lucy.modules.email import Email
from demon_lucy.modules.email.codec import parse_draft, render_draft
from demon_lucy.modules.email.config import TEMPLATE, ACCOUNT_FILE
from demon_lucy.modules.email.errors import EmailError
from demon_lucy.modules.email.scaffold import initialize
from demon_lucy.modules.email.worker import EmailWorker
from demon_lucy.runtime import DEMON_LUCY_STARTUP_TEMPLATE


def wait_for(predicate):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    assert predicate()


@pytest.fixture
def worker(tmp_path, monkeypatch):
    root = tmp_path / "Mail"
    args = parse_args(
        [
            "--email-root",
            str(root),
            "--sys-ignore-paths",
            str(root),
            "--sys-watch-paths",
            str(tmp_path),
            "--email-fetch-interval-seconds",
            "0",
            "--email-repair-interval-seconds",
            "1",
            "--sys-notification-provider",
            "disable",
            "--email-imap-host",
            "imap.example.test",
            "--email-imap-username",
            "lucy",
        ],
        [*DEMON_LUCY_STARTUP_TEMPLATE, *TEMPLATE],
    )
    initialize(str(root), args)
    module = Email()
    monkeypatch.setattr(
        "demon_lucy.modules.email.module.safe_notify", lambda *a, **kw: None
    )
    result = EmailWorker(module, args)
    yield result
    result.stop()


@pytest.mark.parametrize(
    "relative", ["Inbox", "Actions", "Actions/Reply", ".email", ".email/raw", ""]
)
def test_observed_structural_directory_move_returns_with_content(worker, relative):
    root = Path(worker.root)
    source = root / relative
    original = source / "keep.txt"
    original.write_text("saved content")
    destination = root.parent / "accidentally moved"
    source.rename(destination)
    worker.handle_move(DirMovedEvent(str(source), str(destination)))
    assert original.read_text() == "saved content"
    assert not destination.exists()


@pytest.mark.parametrize(
    "relative",
    ["new email.md", "welcome.md", "status.md", ACCOUNT_FILE, ".email/.state.sqlite3"],
)
def test_observed_control_move_restores_exact_bytes(worker, relative):
    root = Path(worker.root)
    source = root / relative
    before = source.read_bytes()
    destination = root / "Trash" / source.name
    source.rename(destination)
    worker.handle_move(FileMovedEvent(str(source), str(destination)))
    assert source.read_bytes() == before
    assert not destination.exists()


def test_repair_recreates_deleted_public_structure_and_keeps_composer(worker):
    root = Path(worker.root)
    starter = root / "new email.md"
    before = render_draft(
        replace(
            parse_draft(starter.read_text()),
            to="me@example.test",
            body="My unsent body",
        )
    )
    starter.write_text(before)
    for relative in ("Inbox", "Drafts", "Actions/Reply"):
        (root / relative).rmdir()
    for relative in ("welcome.md", "refresh.md", "status.md", ACCOUNT_FILE):
        (root / relative).unlink()
    changed = worker.structure.repair()
    assert len(changed) == 7
    assert starter.read_text() == before
    assert "imap.example.test" in (root / ACCOUNT_FILE).read_text()
    assert not (root / "Local-only").exists()
    assert not (root / "Actions/Send").exists()
    assert not (root / "setup-systemd").exists()
    assert worker.structure.repair() == {}


def test_repair_never_reinitializes_missing_delivery_database(worker):
    root = Path(worker.root)
    (root / ".email/.state.sqlite3").unlink()
    with pytest.raises(EmailError, match="database"):
        worker.structure.repair()
    with pytest.raises(EmailError, match="database"):
        worker.start()
    assert not (root / ".email/.state.sqlite3").exists()


def test_move_collision_and_symlink_preserve_both_paths(worker):
    root = Path(worker.root)
    original = root / "welcome.md"
    original.rename(root / "Trash/moved.md")
    original.write_text("new edit")
    worker.handle_move(FileMovedEvent(str(original), str(root / "Trash/moved.md")))
    assert original.read_text() == "new edit"
    assert (root / "Trash/moved.md").exists()
    (root / "Inbox").rmdir()
    outside = root.parent / "outside"
    outside.mkdir()
    (root / "Inbox").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        worker.structure.repair()
    assert not list(outside.iterdir())


def test_real_observer_repairs_repeated_moves_and_deletions(worker, monkeypatch):
    root = Path(worker.root)
    monkeypatch.setattr(
        worker.module,
        "_execute",
        lambda *a, **kw: pytest.fail("unexpected network action"),
    )
    worker.start()
    for attempt in range(3):
        destination = root / f"wrong-{attempt}"
        (root / "Inbox").rename(destination)
        wait_for(lambda: (root / "Inbox").is_dir() and not destination.exists())
        (root / "welcome.md").unlink()
        wait_for(lambda: (root / "welcome.md").exists())
    assert worker.thread.is_alive()
    assert not (root / "Local-only").exists()


def test_worker_returns_refresh_from_anywhere_and_fetches_once(worker, monkeypatch):
    root = Path(worker.root)
    calls = []
    monkeypatch.setattr(
        worker.module, "_execute", lambda ctx, action, **kw: calls.append(action)
    )
    worker.start()
    source = root / "refresh.md"
    destination = root.parent / "refresh moved here.md"
    source.rename(destination)
    wait_for(lambda: source.exists() and not destination.exists() and calls)
    time.sleep(0.1)
    assert calls == ["email-fetch"]


def test_worker_starts_polling_and_does_not_send_existing_sent_drafts(
    worker, monkeypatch
):
    root = Path(worker.root)
    source = root / "new email.md"
    source.rename(root / "Sent/queued-before-start.md")
    calls = []
    monkeypatch.setattr(
        worker.module, "_execute", lambda ctx, action, **kw: calls.append(action)
    )
    worker.fetch_interval = 1
    worker.start()
    wait_for(lambda: calls)
    assert calls == ["email-fetch"]
    assert (root / "Sent/queued-before-start.md").exists()


def test_worker_sends_only_an_observed_draft_move(worker, monkeypatch):
    root = Path(worker.root)
    source = root / "new email.md"
    calls = []
    monkeypatch.setattr(
        worker.module, "_execute", lambda ctx, action, **kw: calls.append(action)
    )
    worker.start()
    source.rename(root / "Sent" / source.name)
    wait_for(lambda: calls)
    assert calls == ["email-send"]
    assert source.exists()


def test_mail_body_never_reaches_general_modules(worker):
    class OrdinaryModule(AbstractModule):
        name = "ordinary"

        def modified(self, ctx, system):
            pytest.fail("email reached general note module")

    root = Path(worker.root)
    path = root / "new email.md"
    path.write_text(
        render_draft(
            replace(
                parse_draft(path.read_text()), body="--email-init unwanted\n--cmd bad\n"
            )
        )
    )
    manager = ModuleManager([OrdinaryModule()], worker.args)
    from watchdog.events import FileModifiedEvent

    assert manager.run(str(path), FileModifiedEvent(str(path))) is None
    assert not (root / "unwanted").exists()


def test_worker_requires_account_exclusion(worker):
    args = worker.args.merged_with(
        parse_args(
            ["--sys-ignore-paths", "/unrelated"],
            [*DEMON_LUCY_STARTUP_TEMPLATE, *TEMPLATE],
        )
    )
    with pytest.raises(EmailError, match="sys-ignore-paths"):
        EmailWorker(worker.module, args)


def test_existing_daemon_entry_point_bootstraps_email_without_engine_changes(
    worker, tmp_path
):
    root = Path(worker.root)
    config = tmp_path / "lucy.conf"
    config.write_text(
        f'--sys-modules email\n--sys-log-level info\n--email-root "{root}"\n--sys-watch-paths "{tmp_path}"\n--sys-ignore-paths "{root}"\n--email-fetch-interval-seconds 0\n--email-repair-interval-seconds 1\n--sys-notification-provider disable\n'
    )
    log = tmp_path / "daemon.log"
    with log.open("w") as output:
        process = subprocess.Popen(
            [sys.executable, "main_daemon.py", "--sys-config-path", str(config)],
            stdout=output,
            stderr=output,
        )
        try:
            wait_for(lambda: "email.worker_started" in log.read_text())
            (root / "Archive").rmdir()
            wait_for(lambda: (root / "Archive").is_dir())
            assert process.poll() is None
        finally:
            process.terminate()
            process.wait(timeout=5)


def test_startup_repairs_deleted_config_from_saved_account(worker):
    path = Path(worker.root) / ACCOUNT_FILE
    before = path.read_bytes()
    path.unlink()
    worker.start()
    assert path.read_bytes() == before


def test_busy_startup_retries_layout_upgrade(worker):
    from demon_lucy.modules.email.files import locked_file

    root = Path(worker.root)
    (root / "Local-only").mkdir()
    with locked_file(str(root / ".email/.lock")):
        worker.start()
        assert worker.thread.is_alive()
        assert (root / "Local-only").exists()
    wait_for(lambda: not (root / "Local-only").exists())


@pytest.mark.parametrize("destination_name", ["Inbox/renamed.md", "../misplaced.md"])
def test_registered_draft_returns_after_accidental_move(worker, destination_name):
    from demon_lucy.modules.email.codec import new_draft
    from demon_lucy.modules.email.storage import MailStore
    import uuid

    root = Path(worker.root)
    with MailStore(worker.root) as store:
        store.create_draft(
            replace(new_draft(uuid.uuid4().hex), body="My unsent text"),
            "Drafts/reply.md",
        )
    source = root / "Drafts/reply.md"
    before = source.read_bytes()
    destination = (root / destination_name).absolute()
    source.rename(destination)
    worker.handle_move(FileMovedEvent(str(source), str(destination)))
    assert source.read_bytes() == before
    assert not destination.exists()


def test_private_draft_retirement_is_not_reversed(worker):
    from demon_lucy.modules.email.codec import new_draft
    from demon_lucy.modules.email.storage import MailStore
    import uuid

    root = Path(worker.root)
    with MailStore(worker.root) as store:
        store.create_draft(new_draft(uuid.uuid4().hex), "Drafts/reply.md")
    source = root / "Drafts/reply.md"
    destination = root / ".email/outgoing/done.draft"
    source.rename(destination)
    worker.handle_move(FileMovedEvent(str(source), str(destination)))
    assert destination.exists() and not source.exists()
