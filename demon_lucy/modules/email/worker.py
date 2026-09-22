"""Email's observer and maintenance loop, hosted by the existing Lucy daemon."""

from __future__ import annotations

import atexit
import logging
import os
import queue
import sqlite3
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from demon_lucy.lib.args.models import ParsedArgs
from demon_lucy.lib.args.sources import load_args
from demon_lucy.lib.logfmt import log_record, next_event_id
from demon_lucy.lib.path import abs_expand_path, path_is_inside, path_matches_selector
from demon_lucy.modules.abstract_module import Context, System
from demon_lucy.modules.email.config import TEMPLATE
from demon_lucy.modules.email.errors import EmailError
from demon_lucy.modules.email.files import FileBusyError, safe_path
from demon_lucy.modules.email.repair import Structure
from demon_lucy.modules.email.scaffold import initialize

if TYPE_CHECKING:
    from demon_lucy.modules.email.module import Email

logger = logging.getLogger(__name__)
_workers: dict[str, EmailWorker] = {}


class EmailWorker(FileSystemEventHandler):
    def __init__(self, module: Email, args: ParsedArgs):
        self.module = module
        self.args = args
        self.root = abs_expand_path(args.require("email-root").value)
        self.fetch_interval = args.require("email-fetch-interval-seconds").value
        self.repair_interval = args.require("email-repair-interval-seconds").value
        if self.fetch_interval < 0 or self.repair_interval <= 0:
            raise EmailError(
                "Email fetch interval must be nonnegative and repair interval must be positive.",
                reason="invalid_config",
            )
        if not any(
            path_matches_selector(self.root, path)
            for path in args.require("sys-ignore-paths").value
        ):
            raise EmailError(
                "Add the email account to --sys-ignore-paths so ordinary note modules do not process mail. Email observes it inside the same Lucy process.",
                reason="invalid_config",
            )
        self.structure = Structure(self.root, args)
        self.system = System(global_template=TEMPLATE, modules=[module])
        self.events: queue.Queue = queue.Queue()
        self.stopping = threading.Event()
        self.observer = Observer()
        self.thread = threading.Thread(target=self.run, name="lucy-email", daemon=True)
        self._repair_failure: tuple[type, str] | None = None
        self._needs_upgrade = False

    def context(self, path: str | None = None, event=None) -> Context:
        return Context(
            path=path or self.root,
            args=self.args,
            run_mode="daemon",
            event_id=next_event_id(),
            event=event,
        )

    def on_moved(self, event):
        if event.is_synthetic:
            return
        source, destination = os.fsdecode(event.src_path), os.fsdecode(event.dest_path)
        if path_is_inside(source, self.root) or path_is_inside(destination, self.root):
            self.events.put(event)

    def handle_move(self, event) -> None:
        source = os.path.abspath(os.fsdecode(event.src_path))
        destination = os.path.abspath(os.fsdecode(event.dest_path))
        ctx = self.context(destination, event)
        try:
            if self.structure.restore_move(source, destination, event_id=ctx.event_id):
                return
            if event.is_directory:
                return
            # Never parse a message as Lucy flags. Account settings come from config.
            self.module.moved(ctx, self.system)
        except FileBusyError:
            self.events.put(event)
        except (EmailError, OSError, ValueError, sqlite3.Error) as error:
            self.report(ctx, error)

    def report(self, ctx: Context, error: Exception) -> None:
        failure = (
            error
            if isinstance(error, EmailError)
            else EmailError(
                "Email structure could not be repaired safely. Existing files were preserved; check paths and permissions.",
                reason="structure_failed",
            )
        )
        self.module._report_error(ctx, self.root, failure)

    def start(self) -> None:
        if not os.path.isfile(safe_path(self.root, ".email/.state.sqlite3")):
            raise EmailError(
                "Restore the email database from backup before starting the worker; delivery records must be retained.",
                reason="state_missing",
            )
        try:
            initialize(self.root, self.args)
        except FileBusyError:
            self._needs_upgrade = True
            logger.info(
                log_record("email.skip", account=self.root, reason="account_busy")
            )
        # Observe the account's parent even when the root itself is renamed.
        # Other configured watch trees allow refresh.md to travel between them.
        paths = {str(Path(self.root).parent)}
        paths.update(
            abs_expand_path(path)
            for path in self.args.require("sys-watch-paths").value
            if os.path.isdir(abs_expand_path(path))
        )
        minimal = [
            path
            for path in paths
            if not any(other != path and path_is_inside(path, other) for other in paths)
        ]
        for path in minimal:
            self.observer.schedule(self, path, recursive=True)
        self.observer.start()
        self.thread.start()
        logger.info(
            log_record(
                "email.worker_started",
                account=self.root,
                fetch_interval_seconds=self.fetch_interval,
                repair_interval_seconds=self.repair_interval,
            )
        )

    def run(self) -> None:
        next_fetch = time.monotonic() + min(30, self.fetch_interval)
        next_repair = time.monotonic() + self.repair_interval
        while not self.stopping.is_set():
            try:
                event = self.events.get(timeout=min(0.25, self.repair_interval))
            except queue.Empty:
                event = None
            if event is not None:
                self.handle_move(event)
                # Finish queued user moves before filling temporarily missing paths.
                continue
            now = time.monotonic()
            if now >= next_repair:
                try:
                    if self._needs_upgrade:
                        initialize(self.root, self.args)
                        self._needs_upgrade = False
                    self.structure.repair()
                    self._repair_failure = None
                except FileBusyError:
                    pass
                except (EmailError, OSError, ValueError, sqlite3.Error) as error:
                    failure = (type(error), str(error))
                    if failure != self._repair_failure:
                        self.report(self.context(), error)
                        self._repair_failure = failure
                next_repair = time.monotonic() + self.repair_interval
            if self.fetch_interval and now >= next_fetch:
                self.module._execute(
                    self.context(), "email-fetch", dropped=False, account_root=self.root
                )
                next_fetch = time.monotonic() + self.fetch_interval

    def stop(self) -> None:
        self.stopping.set()
        self.observer.stop()
        if self.observer.is_alive():
            self.observer.join(timeout=2)
        if self.thread.is_alive() and threading.current_thread() is not self.thread:
            self.thread.join(timeout=2)
        if _workers.get(self.root) is self:
            del _workers[self.root]


def start_daemon_worker(module: Email) -> EmailWorker | None:
    # The engine constructs modules without runtime arguments or lifecycle hooks.
    # Bootstrap only in its daemon entry point; CLI/oneshot imports stay inert.
    if Path(sys.argv[0]).name != "main_daemon.py":
        return None
    from demon_lucy.runtime import DEMON_LUCY_STARTUP_TEMPLATE

    args = load_args([*DEMON_LUCY_STARTUP_TEMPLATE, *TEMPLATE])
    root_value = args.require("email-root").value
    if not root_value:
        return None
    root = abs_expand_path(root_value)
    if root in _workers:
        return _workers[root]
    worker = None
    try:
        worker = EmailWorker(module, args)
        worker.start()
    except (EmailError, OSError, ValueError, sqlite3.Error) as error:
        if worker is not None:
            worker.stop()
        ctx = Context(path=root, args=args, run_mode="daemon", event_id=next_event_id())
        module._report_error(
            ctx,
            root,
            (
                error
                if isinstance(error, EmailError)
                else EmailError(
                    "Email worker could not start. Check the account paths and permissions.",
                    reason="worker_failed",
                )
            ),
        )
        return None
    _workers[root] = worker
    atexit.register(worker.stop)
    return worker
