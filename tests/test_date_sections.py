from datetime import date

import pytest

from demon_lucy.lib.date_sections import (
    DateSection,
    complete_partial_date_section_headers,
    format_date_section_header,
    format_date_section_value,
    parse_date_section_header,
    parse_date_section_value,
    parse_exact_date_section_header,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1.2.2030", date(2030, 2, 1)),
        ("01.02.2030", date(2030, 2, 1)),
        ("29.02.2028", date(2028, 2, 29)),
        ("29.02.2030", None),
        ("01.02.30", None),
        ("date 01.02.2030", None),
    ],
)
def test_parse_date_section_value(value: str, expected: date | None) -> None:
    assert parse_date_section_value(value) == expected


def test_format_date_section_value_is_canonical() -> None:
    assert format_date_section_value(date(2030, 2, 1)) == "01.02.2030"


def test_custom_date_section_header_uses_shared_value_format() -> None:
    header = format_date_section_header(
        date(2030, 2, 1),
        prefix="### ",
        suffix=" // archived",
    )

    assert header == "### 01.02.2030 // archived"
    assert parse_exact_date_section_header(
        header,
        prefix="### ",
        suffix=" // archived",
    ) == date(2030, 2, 1)


@pytest.mark.parametrize(
    ("header", "previous", "expected"),
    [
        (
            "--- 14 - 18",
            date(2026, 10, 13),
            DateSection(date(2026, 10, 14), date(2026, 10, 18)),
        ),
        (
            "---14...18\r\n",
            date(2026, 10, 13),
            DateSection(date(2026, 10, 14), date(2026, 10, 18)),
        ),
        (
            "--- 30 - 2",
            date(2026, 12, 29),
            DateSection(date(2026, 12, 30), date(2027, 1, 2)),
        ),
        (
            "--- 1 - 3",
            date(2026, 12, 31),
            DateSection(date(2027, 1, 1), date(2027, 1, 3)),
        ),
        (
            "--- 14 - 14",
            date(2026, 10, 13),
            DateSection(date(2026, 10, 14), date(2026, 10, 14)),
        ),
        (
            "--- 28 - 29",
            date(2028, 2, 27),
            DateSection(date(2028, 2, 28), date(2028, 2, 29)),
        ),
        (
            "--- 14.10.2026 - 18.10.2026 imported",
            None,
            DateSection(date(2026, 10, 14), date(2026, 10, 18)),
        ),
        ("--- 14 - 18", None, None),
        ("--- 28 - 29", date(2030, 2, 27), None),
        ("--- 31 - 2", date(2026, 4, 30), None),
        ("--- 14 - 32", date(2026, 10, 13), None),
        ("--- 0 - 2", date(2026, 10, 13), None),
        ("--- 30 - 1", date(9999, 12, 29), None),
        ("--- 18.10.2026 - 14.10.2026", None, None),
    ],
)
def test_parse_date_ranges_with_optional_context(header, previous, expected):
    assert parse_date_section_header(header, previous_date=previous) == expected


@pytest.mark.parametrize(
    ("original", "expected"),
    [
        (
            "--- 13.10.2026\r\n\t---  14\t-  018\t\r\n",
            "--- 13.10.2026\r\n\t---  14\t-  018\t\r\n",
        ),
        (
            "--- 13.10.2026\r\n\t---  14\t-  18\t\r\n",
            "--- 13.10.2026\r\n\t---  14.10.2026\t-  18.10.2026\t\r\n",
        ),
        (
            "--- 31.12.2026\n---01...03",
            "--- 31.12.2026\n---01.01.2027...03.01.2027",
        ),
        (
            "--- 13.10.2026\n--- 14 - 32\n--- 19\n--- 20.10.2026\n--- 21 - 23\n",
            "--- 13.10.2026\n--- 14 - 32\n--- 19\n"
            "--- 20.10.2026\n--- 21.10.2026 - 23.10.2026\n",
        ),
    ],
)
def test_complete_date_ranges_preserves_layout_and_resets_invalid_context(
    original, expected
):
    lines = original.splitlines(keepends=True)
    completed, changed = complete_partial_date_section_headers(lines)
    assert "".join(completed) == expected
    assert changed == (original != expected)
    assert complete_partial_date_section_headers(completed) == (completed, False)
