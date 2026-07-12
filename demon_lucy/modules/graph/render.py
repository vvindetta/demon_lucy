from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta

from demon_lucy.modules.graph.params import GraphPeriod

BAR_SEGMENT_COUNT = 6
BAR_SEGMENT_WIDTH = 6


@dataclass(frozen=True)
class RenderBucket:
    label: str
    count: int


@dataclass(frozen=True)
class RenderSeries:
    buckets: list[RenderBucket]
    range_start: str
    range_end: str


def _iter_days(start: date, end: date) -> list[date]:
    days: list[date] = []
    current = start
    while current <= end:
        days.append(current)
        current += timedelta(days=1)
    return days


def _month_start(value: date) -> date:
    return date(value.year, value.month, 1)


def _next_month(value: date) -> date:
    if value.month == 12:
        return date(value.year + 1, 1, 1)
    return date(value.year, value.month + 1, 1)


def _day_series(
    counts_by_date: dict[date, int],
    *,
    start: date,
    end: date,
) -> RenderSeries:
    buckets = [
        RenderBucket(label=day.isoformat(), count=int(counts_by_date.get(day, 0)))
        for day in _iter_days(start, end)
    ]
    return RenderSeries(
        buckets=buckets,
        range_start=start.isoformat(),
        range_end=end.isoformat(),
    )


def _year_series(counts_by_date: dict[date, int], *, year: int) -> RenderSeries:
    month_counts: Counter[tuple[int, int]] = Counter()
    for day, count in counts_by_date.items():
        if day.year != year:
            continue
        month_counts[(day.year, day.month)] += int(count)

    buckets = [
        RenderBucket(
            label=f"{year:04d}-{month:02d}",
            count=int(month_counts.get((year, month), 0)),
        )
        for month in range(1, 13)
    ]
    return RenderSeries(
        buckets=buckets,
        range_start=f"{year:04d}-01",
        range_end=f"{year:04d}-12",
    )


def _weekly_all_series(counts_by_date: dict[date, int]) -> RenderSeries:
    start = min(counts_by_date)
    end = max(counts_by_date)
    buckets: list[RenderBucket] = []
    current = start
    while current <= end:
        bucket_end = min(current + timedelta(days=6), end)
        count = sum(
            int(counts_by_date.get(day, 0)) for day in _iter_days(current, bucket_end)
        )
        buckets.append(RenderBucket(label=current.isoformat(), count=count))
        current = bucket_end + timedelta(days=1)
    return RenderSeries(
        buckets=buckets,
        range_start=start.isoformat(),
        range_end=end.isoformat(),
    )


def _monthly_all_series(counts_by_date: dict[date, int]) -> RenderSeries:
    start = _month_start(min(counts_by_date))
    end_day = max(counts_by_date)
    end = _month_start(end_day)
    buckets: list[RenderBucket] = []
    current = start
    while current <= end:
        next_month = _next_month(current)
        count = sum(
            int(value)
            for day, value in counts_by_date.items()
            if current <= day < next_month
        )
        buckets.append(
            RenderBucket(label=f"{current.year:04d}-{current.month:02d}", count=count)
        )
        current = next_month
    return RenderSeries(
        buckets=buckets,
        range_start=f"{start.year:04d}-{start.month:02d}",
        range_end=f"{end.year:04d}-{end.month:02d}",
    )


def build_series(
    counts_by_date: dict[date, int],
    period: GraphPeriod,
) -> RenderSeries | None:
    if not counts_by_date:
        return None

    latest = max(counts_by_date)
    if period is GraphPeriod.WEEK:
        return _day_series(
            counts_by_date,
            start=latest - timedelta(days=6),
            end=latest,
        )
    if period is GraphPeriod.MONTH:
        return _day_series(
            counts_by_date,
            start=latest - timedelta(days=29),
            end=latest,
        )
    if period is GraphPeriod.YEAR:
        return _year_series(counts_by_date, year=latest.year)

    earliest = min(counts_by_date)
    span_days = (latest - earliest).days + 1
    if span_days <= 60:
        return _day_series(counts_by_date, start=earliest, end=latest)
    if span_days <= 365:
        return _weekly_all_series(counts_by_date)
    return _monthly_all_series(counts_by_date)


def _bar_for_count(count: int, max_count: int) -> str:
    if count <= 0 or max_count <= 0:
        return "|"
    fill_width = max(1, round((count / max_count) * BAR_SEGMENT_WIDTH))
    segment = f"[{'#' * int(fill_width)}]"
    return segment * BAR_SEGMENT_COUNT


def render_text_graph(
    *,
    series: RenderSeries,
    updated_at: str,
    updated_ago: str,
) -> str:
    max_count = max((bucket.count for bucket in series.buckets), default=0)
    label_width = max(4, max(len(bucket.label) for bucket in series.buckets))
    lines = [
        f"last updated: {updated_at}\n",
        f"updated ago: {updated_ago}\n",
        "\n",
        f"{'time':<{label_width}} {'count':>6}  graph\n",
    ]
    for bucket in series.buckets:
        lines.append(
            f"{bucket.label:<{label_width}} {bucket.count:>6}  "
            f"{_bar_for_count(bucket.count, max_count)}\n"
        )
    return "".join(lines)


def render_markdown_graph(
    *,
    series: RenderSeries,
    updated_at: str,
    updated_ago: str,
) -> str:
    max_count = max((bucket.count for bucket in series.buckets), default=0)
    lines = [
        f"- last updated: `{updated_at}`\n",
        f"- updated ago: {updated_ago}\n",
        "\n",
        "| time | count | graph |\n",
        "|---|---:|---|\n",
    ]
    for bucket in series.buckets:
        bar = _bar_for_count(bucket.count, max_count)
        lines.append(f"| {bucket.label} | {bucket.count} | `{bar}` |\n")
    return "".join(lines)
