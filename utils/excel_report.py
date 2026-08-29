"""
Excel writer for feedback reports.

Responsibilities kept OUT of handlers/report.py so the handler stays thin:
    * create the workbook with a styled header row if missing;
    * append a new row;
    * turn a local media reference into a clickable hyperlink cell;
    * (re)apply table formatting (header style, borders, auto width,
      freeze panes, auto-filter) after every write.

The public entry point is ``append_row(path, data)`` which is fully blocking
and therefore meant to be called via ``asyncio.to_thread`` from the handler.
"""

from __future__ import annotations

import os
from typing import List

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

SHEET_TITLE = "Feedback"
COLUMNS: List[str] = [
    "Timestamp", "ID1", "ID2", "ID3", "ID4", "Media_Ref", "Contact", "User_Hash",
    "DeepLink",
]
MEDIA_COL_IDX = COLUMNS.index("Media_Ref") + 1  # 1-based for openpyxl

# --- Styling constants ------------------------------------------------------ #
_HEADER_FILL = PatternFill("solid", fgColor="1F4E78")
_HEADER_FONT = Font(bold=True, color="FFFFFF", size=11)
_HEADER_ALIGN = Alignment(horizontal="center", vertical="center")
_CELL_ALIGN = Alignment(vertical="top", wrap_text=True)
_LINK_FONT = Font(color="0563C1", underline="single")
_STRIPE_FILL = PatternFill("solid", fgColor="F2F6FC")
_THIN = Side(style="thin", color="D0D7E2")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)

# Per-column preferred widths (fallback is auto-computed, capped by _MAX_WIDTH).
_MIN_WIDTH = 12
_MAX_WIDTH = 60


def _is_local_media(ref: str) -> bool:
    """A media ref is a downloadable file (vs a bare Telegram file_id) if it
    points at an existing path on disk."""
    return bool(ref) and os.path.exists(ref)


def _ensure_workbook(path: str) -> None:
    if os.path.exists(path):
        return
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET_TITLE
    ws.append(COLUMNS)
    wb.save(path)


def _ensure_header(ws: Worksheet) -> None:
    """Fill in any missing header labels (e.g. a newly added trailing column
    like DeepLink) without clobbering existing header text."""
    for idx, name in enumerate(COLUMNS, start=1):
        cell = ws.cell(row=1, column=idx)
        if cell.value in (None, ""):
            cell.value = name


def _write_media_cell(ws: Worksheet, row_idx: int, ref: str) -> None:
    """Store the media reference; if it is a real local file, make it a
    clickable hyperlink that opens the file, showing just the file name."""
    cell = ws.cell(row=row_idx, column=MEDIA_COL_IDX)
    if not ref:
        cell.value = ""
        return
    if _is_local_media(ref):
        abs_path = os.path.abspath(ref)
        cell.value = os.path.basename(ref)
        cell.hyperlink = abs_path
        cell.font = _LINK_FONT
    else:
        # A raw file_id (media saving disabled or download failed) — keep as text.
        cell.value = ref


def _apply_formatting(ws: Worksheet) -> None:
    """Idempotent table styling applied after every append."""
    max_row = ws.max_row
    max_col = len(COLUMNS)

    # Header row.
    for col in range(1, max_col + 1):
        cell = ws.cell(row=1, column=col)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = _HEADER_ALIGN
        cell.border = _BORDER

    # Body rows: borders, alignment, zebra striping (skip the media link font).
    for row in range(2, max_row + 1):
        stripe = (row % 2 == 0)
        for col in range(1, max_col + 1):
            cell = ws.cell(row=row, column=col)
            cell.border = _BORDER
            cell.alignment = _CELL_ALIGN
            if stripe and not (col == MEDIA_COL_IDX and cell.hyperlink):
                cell.fill = _STRIPE_FILL

    # Column widths based on the longest value seen (capped).
    for col in range(1, max_col + 1):
        letter = get_column_letter(col)
        longest = len(str(COLUMNS[col - 1]))
        for row in range(2, max_row + 1):
            val = ws.cell(row=row, column=col).value
            if val is not None:
                longest = max(longest, len(str(val)))
        ws.column_dimensions[letter].width = max(_MIN_WIDTH, min(longest + 2, _MAX_WIDTH))

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(max_col)}{max_row}"


def append_row(path: str, data: dict) -> None:
    """Blocking: append one feedback row and (re)format the whole table."""
    _ensure_workbook(path)
    wb = load_workbook(path)
    try:
        ws = wb[SHEET_TITLE] if SHEET_TITLE in wb.sheetnames else wb.active
        _ensure_header(ws)
        ws.append([
            data.get("timestamp", ""),
            data.get("id1", ""),
            data.get("id2", ""),
            data.get("id3", ""),
            data.get("id4", ""),
            "",  # media cell is written separately (may become a hyperlink)
            data.get("contact", ""),
            data.get("user_hash", ""),
            data.get("deeplink", ""),
        ])
        _write_media_cell(ws, ws.max_row, data.get("media", ""))
        _apply_formatting(ws)
        wb.save(path)
    finally:
        wb.close()
