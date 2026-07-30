from __future__ import annotations

import os
from dataclasses import replace

from demon_lucy.lib.args.models import ArgSource
from demon_lucy.modules.abstract_module import Context
from demon_lucy.modules.archive import notify
from demon_lucy.modules.archive.types import ArchiveOutputMode, ArchiveRequest


def arg_values(ctx: Context, name: str) -> list[str]:
    values: list[str] = ctx.args.require(name).value
    return [item.strip() for item in values if item.strip()]


def flag_present(ctx: Context, name: str) -> bool:
    return ctx.args.require(name).source is not ArgSource.DEFAULT


def local_archive_exists(ctx: Context) -> bool:
    archive_dir = os.path.join(os.path.dirname(ctx.path), ".archive")
    return os.path.isdir(archive_dir) and not os.path.islink(archive_dir)


def _output_mode(value: str) -> ArchiveOutputMode | None:
    try:
        return ArchiveOutputMode(value.strip().casefold())
    except ValueError:
        return None


def route_mode_from_values(
    ctx: Context,
    *,
    flag: str,
    values: list[str],
    fallback_mode: ArchiveOutputMode,
) -> ArchiveOutputMode | None:
    if not values:
        return fallback_mode
    if len(values) == 1:
        output_mode = _output_mode(values[0])
        if output_mode is not None:
            return output_mode
    notify.invalid_rule(
        ctx,
        flag=flag,
        values=values,
        reason="expected_optional_mode",
    )
    return None


def idle_and_mode_from_tail_values(
    ctx: Context,
    *,
    flag: str,
    values: list[str],
    fallback_idle_hours: float,
    fallback_mode: ArchiveOutputMode,
) -> tuple[float, ArchiveOutputMode] | None:
    idle_hours = fallback_idle_hours
    output_mode = fallback_mode

    for token in values:
        parsed_mode = _output_mode(token)
        if parsed_mode is not None:
            output_mode = parsed_mode
            continue
        try:
            idle_hours = float(token)
            continue
        except ValueError:
            notify.invalid_rule(
                ctx,
                flag=flag,
                token=token,
                reason="invalid_trailing_token",
            )
            return None

    return idle_hours, output_mode


def auto_pair_request(ctx: Context) -> ArchiveRequest | None:
    values = arg_values(ctx, "archive-auto-pair")
    if not values:
        return None
    if len(values) < 2:
        notify.invalid_rule(
            ctx,
            flag="--archive-auto-pair",
            reason="missing_src_or_dest",
        )
        return None

    tail = idle_and_mode_from_tail_values(
        ctx,
        flag="--archive-auto-pair",
        values=values[2:],
        fallback_idle_hours=ctx.args.require("archive-idle-hours").value,
        fallback_mode=ctx.args.require("archive-default-mode").value,
    )
    if tail is None:
        return None
    idle_hours, output_mode = tail
    return ArchiveRequest(
        route="pair",
        output_mode=output_mode,
        src_selector=values[0],
        dest_selector=values[1],
        idle_hours=idle_hours,
        force=False,
    )


def auto_local_request(ctx: Context) -> ArchiveRequest | None:
    values = arg_values(ctx, "archive-auto-local")
    if not values:
        return None
    tail = idle_and_mode_from_tail_values(
        ctx,
        flag="--archive-auto-local",
        values=values[1:],
        fallback_idle_hours=ctx.args.require("archive-idle-hours").value,
        fallback_mode=ctx.args.require("archive-default-mode").value,
    )
    if tail is None:
        return None
    idle_hours, output_mode = tail
    return ArchiveRequest(
        route="local",
        output_mode=output_mode,
        src_selector=values[0],
        dest_selector=None,
        idle_hours=idle_hours,
        force=False,
    )


def auto_global_request(ctx: Context) -> ArchiveRequest | None:
    values = arg_values(ctx, "archive-auto-global")
    if not values:
        return None
    tail = idle_and_mode_from_tail_values(
        ctx,
        flag="--archive-auto-global",
        values=values[1:],
        fallback_idle_hours=ctx.args.require("archive-idle-hours").value,
        fallback_mode=ctx.args.require("archive-default-mode").value,
    )
    if tail is None:
        return None
    idle_hours, output_mode = tail
    return ArchiveRequest(
        route="global",
        output_mode=output_mode,
        src_selector=values[0],
        dest_selector=None,
        idle_hours=idle_hours,
        force=False,
    )


def auto_requests(ctx: Context) -> list[ArchiveRequest]:
    requests: list[ArchiveRequest] = []
    for builder in (
        auto_pair_request,
        auto_local_request,
        auto_global_request,
    ):
        request = builder(ctx)
        if request is not None:
            requests.append(request)
    return requests


def present_manual_routes(ctx: Context) -> list[tuple[str, str, str]]:
    route_specs = [
        ("pair", "archive-pair", "--archive-pair"),
        ("local", "archive-local", "--archive-local"),
        ("global", "archive-global", "--archive-global"),
    ]
    return [
        (route, key, flag) for route, key, flag in route_specs if flag_present(ctx, key)
    ]


def manual_flag_present(ctx: Context) -> bool:
    if ctx.args.require("archive").value:
        return True
    for name in ("archive-pair", "archive-local", "archive-global"):
        if flag_present(ctx, name):
            return True
    return False


def manual_requests(ctx: Context) -> list[ArchiveRequest]:
    archive_present = ctx.args.require("archive").value
    present_routes = present_manual_routes(ctx)
    if archive_present and present_routes:
        flags = ["--archive", *[flag for _route, _key, flag in present_routes]]
        notify.invalid_rule(
            ctx,
            flag=",".join(flags),
            reason="multiple_manual_routes",
        )
        return []
    if archive_present:
        return archive_command_requests(ctx)
    if not present_routes:
        return []
    if len(present_routes) > 1:
        notify.invalid_rule(
            ctx,
            flag=",".join(flag for _route, _key, flag in present_routes),
            reason="multiple_manual_routes",
        )
        return []

    route, key, flag = present_routes[0]
    mode = route_mode_from_values(
        ctx,
        flag=flag,
        values=arg_values(ctx, key),
        fallback_mode=ctx.args.require("archive-default-mode").value,
    )
    if mode is None:
        return []

    if route == "pair":
        pair_request = auto_pair_request(ctx)
        if pair_request is None:
            notify.invalid_rule(
                ctx,
                flag="--archive-pair",
                reason="missing_archive_auto_pair",
            )
            return []
        return [replace(pair_request, output_mode=mode, force=True)]

    return [
        ArchiveRequest(
            route=route,
            output_mode=mode,
            src_selector=os.path.basename(ctx.path),
            dest_selector=None,
            idle_hours=ctx.args.require("archive-idle-hours").value,
            force=True,
        )
    ]


def archive_command_requests(ctx: Context) -> list[ArchiveRequest]:
    if arg_values(ctx, "archive-auto-pair"):
        pair_request = auto_pair_request(ctx)
        if pair_request is None:
            return []
        return [replace(pair_request, force=True)]

    route = "local" if local_archive_exists(ctx) else "global"
    return [
        ArchiveRequest(
            route=route,
            output_mode=ctx.args.require("archive-default-mode").value,
            src_selector=os.path.basename(ctx.path),
            dest_selector=None,
            idle_hours=ctx.args.require("archive-idle-hours").value,
            force=True,
        )
    ]


def requests_for_context(ctx: Context) -> list[ArchiveRequest]:
    manual = manual_requests(ctx)
    if manual or manual_flag_present(ctx):
        return manual
    return auto_requests(ctx)
