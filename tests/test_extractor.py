import io

import openpyxl
import pandas as pd

from excel_export import build_workbook
from extractor import Invoice, InvoiceLine, find_duplicates, is_valid_israeli_tax_id, validate_invoice


def make(**kw):
    base = dict(
        document_type="חשבונית מס", supplier_name="ספק", supplier_tax_id="513000000",
        invoice_number="1", invoice_date="2026-11-01", subtotal=100.0, vat_rate=18,
        vat_amount=18.0, total=118.0,
        lines=[InvoiceLine(description="x", quantity=2, unit_price=50, line_total=100)],
    )
    base.update(kw)
    return Invoice(**base)


def test_tax_id():
    assert is_valid_israeli_tax_id("513000000")
    assert is_valid_israeli_tax_id("123456782")
    assert not is_valid_israeli_tax_id("123456789")
    assert not is_valid_israeli_tax_id(None)
    assert not is_valid_israeli_tax_id("1234567890")


def test_clean_invoice_has_no_issues():
    assert validate_invoice(make()) == []


def test_detects_bad_totals_and_lines():
    inv = make(total=150.0, lines=[InvoiceLine(description="x", quantity=3, unit_price=50, line_total=100)])
    issues = validate_invoice(inv)
    assert any("שורה 1" in i for i in issues)
    assert any("סה\"כ (150.00)" in i for i in issues)


def test_detects_bad_tax_id_and_missing_fields():
    issues = validate_invoice(make(supplier_tax_id="123456789", invoice_number=None))
    assert any("ספרת ביקורת" in i for i in issues)
    assert "חסר מספר חשבונית" in issues


def test_duplicates():
    dups = find_duplicates({"a": make(), "b": make(), "c": make(invoice_number="2")})
    assert dups == {"b": "a"}


def test_workbook():
    headers = pd.DataFrame([{"file": "a.pdf", "supplier_name": "ספק", "invoice_number": "1",
                             "subtotal": 100.0, "vat_amount": 18.0, "total": 118.0, "currency": "ILS"}])
    lines = pd.DataFrame([{"file": "a.pdf", "invoice_number": "1", "description": "x", "line_total": 100.0}])
    wb = openpyxl.load_workbook(io.BytesIO(build_workbook(headers, lines)))
    assert wb.sheetnames == ["כותרות", "שורות", "סיכום לפי ספק"]
    assert wb["כותרות"].sheet_view.rightToLeft
    assert wb["כותרות"]["A2"].value == "a.pdf"
