"""Build a Priority-friendly Excel workbook (headers sheet + lines sheet)."""

from __future__ import annotations

import io

import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

HEADER_COLUMNS = {
    "file": "קובץ",
    "document_type": "סוג מסמך",
    "supplier_name": "שם ספק",
    "supplier_tax_id": "ח.פ. ספק",
    "invoice_number": "מספר חשבונית",
    "invoice_date": "תאריך",
    "due_date": "תאריך פירעון",
    "allocation_number": "מספר הקצאה",
    "currency": "מטבע",
    "subtotal": "לפני מע\"מ",
    "vat_rate": "% מע\"מ",
    "vat_amount": "מע\"מ",
    "total": "סה\"כ",
    "status": "סטטוס",
    "issues": "הערות בדיקה",
}

LINE_COLUMNS = {
    "file": "קובץ",
    "invoice_number": "מספר חשבונית",
    "supplier_name": "שם ספק",
    "line_no": "שורה",
    "sku": "מק\"ט",
    "description": "תיאור",
    "quantity": "כמות",
    "unit_price": "מחיר יחידה",
    "discount_percent": "% הנחה",
    "line_total": "סה\"כ שורה",
}


def _style_sheet(ws, money_cols: set[str]) -> None:
    ws.sheet_view.rightToLeft = True
    ws.freeze_panes = "A2"
    fill = PatternFill("solid", fgColor="1F4E78")
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for idx, col in enumerate(ws.iter_cols(min_row=1, max_row=ws.max_row), 1):
        header = col[0].value or ""
        width = max(len(str(c.value)) if c.value is not None else 0 for c in col)
        ws.column_dimensions[get_column_letter(idx)].width = min(max(width + 2, 10), 50)
        if header in money_cols:
            for c in col[1:]:
                c.number_format = "#,##0.00"
    ws.auto_filter.ref = ws.dimensions


def build_workbook(headers: pd.DataFrame, lines: pd.DataFrame) -> bytes:
    h = headers.reindex(columns=list(HEADER_COLUMNS)).rename(columns=HEADER_COLUMNS)
    l = lines.reindex(columns=list(LINE_COLUMNS)).rename(columns=LINE_COLUMNS)

    summary = (
        headers.groupby(["supplier_name", "currency"], dropna=False)
        .agg(count=("invoice_number", "count"), subtotal=("subtotal", "sum"),
             vat=("vat_amount", "sum"), total=("total", "sum"))
        .reset_index()
        .rename(columns={"supplier_name": "ספק", "currency": "מטבע", "count": "מס' חשבוניות",
                         "subtotal": "לפני מע\"מ", "vat": "מע\"מ", "total": "סה\"כ"})
        .sort_values("סה\"כ", ascending=False)
    )

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        h.to_excel(writer, sheet_name="כותרות", index=False)
        l.to_excel(writer, sheet_name="שורות", index=False)
        summary.to_excel(writer, sheet_name="סיכום לפי ספק", index=False)
        money = {"לפני מע\"מ", "מע\"מ", "סה\"כ", "מחיר יחידה", "סה\"כ שורה"}
        for ws in writer.book.worksheets:
            _style_sheet(ws, money)
    return buf.getvalue()
