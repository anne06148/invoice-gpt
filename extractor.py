"""Invoice extraction with Claude vision + structured outputs, plus sanity checks."""

from __future__ import annotations

import base64
import hashlib
from typing import Literal, Optional

import anthropic
from pydantic import BaseModel, Field

MODEL = "claude-opus-5-5"

SYSTEM_PROMPT = """את/ה מומחה/ית לקליטת חשבוניות ספקים בישראל עבור הנהלת חשבונות.
קבל/י תמונה או PDF של מסמך ספק וחלץ/י את הנתונים בדיוק כפי שהם מופיעים במסמך.

כללים:
- אל תמציא/י ערכים. שדה שלא מופיע או לא קריא - השאר/י null, וציין/י זאת ב-warnings.
- תאריכים בפורמט YYYY-MM-DD.
- סכומים כמספרים בלבד (בלי ₪, בלי פסיקים של אלפים).
- supplier_tax_id: מספר עוסק / ח.פ. של הספק (לא של הלקוח), ספרות בלבד.
- vat_rate באחוזים (למשל 18).
- lines: כל שורת פריט בחשבונית, לפי הסדר. אם אין פירוט שורות - רשימה ריקה.
- warnings: כל דבר חשוד או לא ודאי (טקסט מטושטש, סכומים שלא מסתדרים, מסמך שאינו חשבונית וכו'), בעברית.
"""


class InvoiceLine(BaseModel):
    description: str = Field(description="תיאור הפריט")
    sku: Optional[str] = Field(None, description="מק\"ט, אם מופיע")
    quantity: Optional[float] = None
    unit_price: Optional[float] = Field(None, description="מחיר ליחידה לפני מע\"מ")
    discount_percent: Optional[float] = None
    line_total: Optional[float] = Field(None, description="סה\"כ שורה לפני מע\"מ")


class Invoice(BaseModel):
    document_type: Literal[
        "חשבונית מס", "חשבונית מס קבלה", "חשבונית זיכוי", "קבלה", "חשבון עסקה", "תעודת משלוח", "אחר"
    ]
    supplier_name: Optional[str] = None
    supplier_tax_id: Optional[str] = Field(None, description="ח.פ. / עוסק מורשה של הספק")
    customer_name: Optional[str] = None
    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = Field(None, description="YYYY-MM-DD")
    due_date: Optional[str] = Field(None, description="YYYY-MM-DD")
    allocation_number: Optional[str] = Field(None, description="מספר הקצאה מרשות המסים, אם מופיע")
    currency: str = Field("ILS", description="ISO 4217")
    subtotal: Optional[float] = Field(None, description="סה\"כ לפני מע\"מ")
    vat_rate: Optional[float] = None
    vat_amount: Optional[float] = None
    total: Optional[float] = Field(None, description="סה\"כ לתשלום כולל מע\"מ")
    lines: list[InvoiceLine] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


MEDIA_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "gif": "image/gif",
    "pdf": "application/pdf",
}


def file_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def _content_block(data: bytes, media_type: str) -> dict:
    b64 = base64.standard_b64encode(data).decode("utf-8")
    block_type = "document" if media_type == "application/pdf" else "image"
    return {"type": block_type, "source": {"type": "base64", "media_type": media_type, "data": b64}}


class ExtractionError(Exception):
    pass


def extract_invoice(client: anthropic.Anthropic, data: bytes, media_type: str) -> Invoice:
    """Send one invoice file to Claude and get back a validated Invoice."""
    response = client.beta.messages.parse(
        model=MODEL,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        output_config={"effort": "medium"},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_format=Invoice,
        messages=[
            {
                "role": "user",
                "content": [
                    _content_block(data, media_type),
                    {"type": "text", "text": "חלץ/י את נתוני החשבונית."},
                ],
            }
        ],
    )
    if response.stop_reason == "refusal":
        raise ExtractionError("המודל סירב לעבד את הקובץ.")
    if response.stop_reason == "max_tokens":
        raise ExtractionError("החשבונית ארוכה מדי - התשובה נקטעה.")
    if response.parsed_output is None:
        raise ExtractionError("לא התקבל פלט תקין מהמודל.")
    return response.parsed_output


# ---------------------------------------------------------------------------
# Sanity checks (pure Python, no API)
# ---------------------------------------------------------------------------

def is_valid_israeli_tax_id(tax_id: Optional[str]) -> bool:
    """Check digit for Israeli ID / company / VAT numbers (9 digits)."""
    if not tax_id:
        return False
    digits = "".join(c for c in tax_id if c.isdigit())
    if not digits or len(digits) > 9:
        return False
    digits = digits.zfill(9)
    total = 0
    for i, ch in enumerate(digits):
        n = int(ch) * (1 if i % 2 == 0 else 2)
        total += n if n < 10 else n - 9
    return total % 10 == 0


def _close(a: float, b: float, tol: float = 1.0) -> bool:
    return abs(a - b) <= tol


def validate_invoice(inv: Invoice) -> list[str]:
    """Return a list of Hebrew issue descriptions (empty = all good)."""
    issues: list[str] = []

    if not inv.invoice_number:
        issues.append("חסר מספר חשבונית")
    if not inv.invoice_date:
        issues.append("חסר תאריך")
    if inv.total is None:
        issues.append("חסר סכום כולל")

    if inv.supplier_tax_id and inv.currency == "ILS" and not is_valid_israeli_tax_id(inv.supplier_tax_id):
        issues.append(f"ח.פ. ספק {inv.supplier_tax_id} לא עובר ספרת ביקורת")

    line_totals = [l.line_total for l in inv.lines if l.line_total is not None]
    if inv.subtotal is not None and line_totals and len(line_totals) == len(inv.lines):
        s = sum(line_totals)
        if not _close(s, inv.subtotal):
            issues.append(f"סכום השורות ({s:,.2f}) שונה מסה\"כ לפני מע\"מ ({inv.subtotal:,.2f})")

    for i, l in enumerate(inv.lines, 1):
        if l.quantity is not None and l.unit_price is not None and l.line_total is not None:
            expected = l.quantity * l.unit_price * (1 - (l.discount_percent or 0) / 100)
            if not _close(expected, l.line_total):
                issues.append(f"שורה {i}: כמות × מחיר ({expected:,.2f}) ≠ סה\"כ שורה ({l.line_total:,.2f})")

    if inv.subtotal is not None and inv.vat_amount is not None and inv.total is not None:
        if not _close(inv.subtotal + inv.vat_amount, inv.total):
            issues.append(
                f"לפני מע\"מ + מע\"מ ({inv.subtotal + inv.vat_amount:,.2f}) ≠ סה\"כ ({inv.total:,.2f})"
            )
    if inv.subtotal and inv.vat_rate is not None and inv.vat_amount is not None:
        if not _close(inv.subtotal * inv.vat_rate / 100, inv.vat_amount):
            issues.append(f"מע\"מ {inv.vat_amount:,.2f} לא תואם {inv.vat_rate:g}% מהסכום")

    return issues


def find_duplicates(invoices: dict[str, Invoice]) -> dict[str, str]:
    """Map key -> key of an earlier invoice with the same supplier + number."""
    seen: dict[tuple, str] = {}
    dups: dict[str, str] = {}
    for key, inv in invoices.items():
        if not inv.invoice_number:
            continue
        sig = ("".join(c for c in (inv.supplier_tax_id or inv.supplier_name or "") if c.isalnum()), inv.invoice_number.strip())
        if sig in seen:
            dups[key] = seen[sig]
        else:
            seen[sig] = key
    return dups
