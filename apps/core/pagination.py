"""
One page of a list, and the numbers a pager needs to draw itself.

Four registers wanted this and three of them had written it: the till, the
partners and the participants each carried a private ``_page_of`` that was the
same function to the character — same clamping, same off-by-one on ``start``,
same keys. The fourth register was about to make it four. A helper copied four
times is four places for the next fix to be applied in three of.

**Presentation only.** It slices a list the caller already holds; it issues no
query, knows no model, and decides nothing about what belongs on the page. The
services stay the source of the rows, and a register that reads everything and
shows fifty is still reading everything — this stops the page from DRAWING
thousands of rows, which is the part the reader pays for.

**A page number is never trusted.** It arrives from a URL anyone can edit, so
it is clamped into the range that exists and a nonsense value lands on page
one rather than raising. A register that 500s on ``?page=abc`` is a register
with an error page where a list should be.
"""

from __future__ import annotations

from typing import Any

#: Fifty rows. The number the three registers that had this already agreed on,
#: kept so that converting them changes nothing a reader would see.
PAGE_SIZE = 50


def page_of(rows: list[Any], raw: str, *, size: int = PAGE_SIZE) -> dict[str, Any]:
    """
    ``rows`` sliced to one page, with everything a pager template asks for.

    ``total`` is the length of the whole list rather than of the slice, because
    a count beside a search that reported the window instead of the result
    would answer «how many are there» with «fifty».
    """
    total = len(rows)
    pages = max(1, -(-total // size))
    try:
        number = min(max(int(raw or 1), 1), pages)
    except ValueError:
        number = 1
    start = (number - 1) * size
    return {
        "rows": rows[start : start + size],
        "number": number,
        "pages": pages,
        "total": total,
        # One-based for the reader, and 0 of 0 when there is nothing at all
        # rather than the «1–0 من 0» that counting from one would print.
        "start": start + 1 if total else 0,
        "end": min(start + size, total),
        "has_prev": number > 1,
        "has_next": number < pages,
        "prev": number - 1,
        "next": number + 1,
    }


__all__ = ["PAGE_SIZE", "page_of"]
