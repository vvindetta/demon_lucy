from __future__ import annotations

import os
from dataclasses import replace

from demon_lucy.lib.args.parser import parse_enum_value
from demon_lucy.modules.abstract_module import Context
from demon_lucy.modules.archive import notify
from demon_lucy.modules.archive.types import ArchiveOutputMode, ArchiveRequest


def config_values(ctx: Context, key: str, flag: str) -> list[str]:
    value = ctx.config.get(key, [])
    if value is None:
        return []
    if not isinstance(value, list):
        notify.invalid_rule(
            ctx,
            flag=flag,
            reason="invalid_config_type",
            value=value,
        )
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def flag_present(ctx: Context, key: str, flag: str) -> bool:
    return key in ctx.arg_lines or bool(config_values(ctx, key, flag))


def bool_flag_present(ctx: Context, key: str) -> bool:
    return key in ctx.arg_lines or bool(ctx.config.get(key))


def auto_pair_configured(ctx: Context) -> bool:
    value = ctx.config.get("archive_auto_pair")
    if isinstance(value, list):
        return any(str(item).strip() for item in value)
    return bool(value)


def local_archive_exists(ctx: Context) -> bool:
    archive_dir = os.path.join(os.path.dirname(ctx.path), ".archive")
    return os.path.isdir(archive_dir) and not os.path.islink(archive_dir)


def _output_mode(value: object) -> ArchiveOutputMode | None:
    try:
        return parse_enum_value(ArchiveOutputMode, value)
    except ValueError:
        return None


def default_output_mode(ctx: Context) -> ArchiveOutputMode | None:
    output_mode = _output_mode(ctx.config["archive_default_mode"])
    if output_mode is not None:
        return output_mode
    notify.invalid_rule(
        ctx,
        flag="--archive-default-mode",
        value=ctx.config["archive_default_mode"],
        reason="unsupported_mode",
    )
    return None


def default_idle_hours(ctx: Context) -> float | None:
    try:
        return float(ctx.config["archive_idle_hours"])
    except (TypeError, ValueError):
        notify.invalid_rule(
            ctx,
            flag="--archive-idle-hours",
            value=ctx.config.get("archive_idle_hours"),
            reason="invalid_number",
        )
        return None


def route_mode_from_config_values(
    ctx: Context,
    *,
    flag: str,
    values: list[str],
    fallback_mode: ArchiveOutputMode | None,
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
    fallback_idle_hours: float | None,
    fallback_mode: ArchiveOutputMode | None,
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
        except (TypeError, ValueError):
            notify.invalid_rule(
                ctx,
                flag=flag,
                token=token,
                reason="invalid_trailing_token",
            )
            return None

    if idle_hours is None or output_mode is None:
        return None
    return idle_hours, output_mode


def auto_pair_request(ctx: Context) -> ArchiveRequest | None:
    values = config_values(ctx, "archive_auto_pair", "--archive-auto-pair")
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
        fallback_idle_hours=default_idle_hours(ctx),
        fallback_mode=default_output_mode(ctx),
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
    values = config_values(ctx, "archive_auto_local", "--archive-auto-local")
    if not values:
        return None
    tail = idle_and_mode_from_tail_values(
        ctx,
        flag="--archive-auto-local",
        values=values[1:],
        fallback_idle_hours=default_idle_hours(ctx),
        fallback_mode=default_output_mode(ctx),
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
    values = config_values(ctx, "archive_auto_global", "--archive-auto-global")
    if not values:
        return None
    tail = idle_and_mode_from_tail_values(
        ctx,
        flag="--archive-auto-global",
        values=values[1:],
        fallback_idle_hours=default_idle_hours(ctx),
        fallback_mode=default_output_mode(ctx),
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
        ("pair", "archive_pair", "--archive-pair"),
        ("local", "archive_local", "--archive-local"),
        ("global", "archive_global", "--archive-global"),
    ]
    return [
        (route, key, flag)
        for route, key, flag in route_specs
        if flag_present(ctx, key, flag)
    ]


def manual_flag_present(ctx: Context) -> bool:
    if bool_flag_present(ctx, "archive"):
        return True
    for key in ("archive_pair", "archive_local", "archive_global"):
        if key in ctx.arg_lines:
            return True
        value = ctx.config.get(key)
        if isinstance(value, list):
            if any(str(item).strip() for item in value):
                return True
            continue
        if value:
            return True
    return False


def manual_requests(ctx: Context) -> list[ArchiveRequest]:
    archive_present = bool_flag_present(ctx, "archive")
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
    mode = route_mode_from_config_values(
        ctx,
        flag=flag,
        values=config_values(ctx, key, flag),
        fallback_mode=default_output_mode(ctx),
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

    idle_hours = default_idle_hours(ctx)
    if idle_hours is None:
        return []
    return [
        ArchiveRequest(
            route=route,
            output_mode=mode,
            src_selector=os.path.basename(ctx.path),
            dest_selector=None,
            idle_hours=idle_hours,
            force=True,
        )
    ]


def archive_command_requests(ctx: Context) -> list[ArchiveRequest]:
    if auto_pair_configured(ctx):
        pair_request = auto_pair_request(ctx)
        if pair_request is None:
            return []
        return [replace(pair_request, force=True)]

    mode = default_output_mode(ctx)
    idle_hours = default_idle_hours(ctx)
    if mode is None or idle_hours is None:
        return []

    route = "local" if local_archive_exists(ctx) else "global"
    return [
        ArchiveRequest(
            route=route,
            output_mode=mode,
            src_selector=os.path.basename(ctx.path),
            dest_selector=None,
            idle_hours=idle_hours,
            force=True,
        )
    ]


def requests_for_context(ctx: Context) -> list[ArchiveRequest]:
    manual = manual_requests(ctx)
    if manual or manual_flag_present(ctx):
        return manual
    return auto_requests(ctx)
