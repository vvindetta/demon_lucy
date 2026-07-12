from datetime import date

import pytest

from demon_lucy.lib.date_sections import (
    format_date_section_header,
    format_date_section_value,
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
