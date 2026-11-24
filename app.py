import os
from concurrent.futures import ThreadPoolExecutor, as_completed

import anthropic
import pandas as pd
import streamlit as st

from excel_export import HEADER_COLUMNS as HEADER_LABELS, LINE_COLUMNS as LINE_LABELS, build_workbook
from extractor import (
    MEDIA_TYPES,
    ExtractionError,
    Invoice,
    InvoiceLine,
    extract_invoice,
    file_hash,
    find_duplicates,
    validate_invoice,
)

st.set_page_config(page_title="Invoice GPT", page_icon="🧾", layout="wide")

st.markdown(
    """
    <style>
    .stApp, .stMarkdown, .stTextInput, .stFileUploader, [data-testid="stSidebar"],
    [data-testid="stMetric"], .stAlert, .stExpander { direction: rtl; text-align: right; }
    [data-testid="stDataFrame"], [data-testid="stDataEditor"] { direction: rtl; }
    </style>
    """,
    unsafe_allow_html=True,
)

if "results" not in st.session_state:
    # hash -> {"file": str, "invoice": dict | None, "error": str | None}
    st.session_state.results = {}
results: dict = st.session_state.results

HEADER_FIELDS = [f for f in Invoice.model_fields if f not in ("lines", "warnings")]
LINE_FIELDS = list(InvoiceLine.model_fields)


def sample_invoices() -> dict:
    a = Invoice(
        document_type="חשבונית מס", supplier_name="אופיס דיפו בע\"מ", supplier_tax_id="513000000",
        invoice_number="10452", invoice_date="2026-11-02", due_date="2026-12-31", currency="ILS",
        subtotal=1250.0, vat_rate=18, vat_amount=225.0, total=1475.0,
        lines=[
            InvoiceLine(description="נייר A4 קרטון", sku="PAP-500", quantity=10, unit_price=95, line_total=950),
            InvoiceLine(description="טונר HP 59A", sku="TON-59A", quantity=1, unit_price=300, line_total=300),
        ],
    )
    b = Invoice(
        document_type="חשבונית מס", supplier_name="חשמל ישיר", supplier_tax_id="514444441",
        invoice_number="A-7781", invoice_date="2026-11-10", currency="ILS",
        subtotal=4000.0, vat_rate=18, vat_amount=720.0, total=4800.0,
        lines=[InvoiceLine(description="מסך 27 אינץ'", quantity=4, unit_price=1000, line_total=4000)],
        warnings=["החותמת מסתירה חלק מהסכום הכולל"],
    )
    c = a.model_copy(deep=True)
    return {
        "demo-a": {"file": "office_depot_10452.pdf", "invoice": a.model_dump(), "error": None},
        "demo-b": {"file": "chashmal_A7781.jpg", "invoice": b.model_dump(), "error": None},
        "demo-c": {"file": "office_depot_10452_copy.png", "invoice": c.model_dump(), "error": None},
    }


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("⚙️ הגדרות")
    api_key = os.environ.get("ANTHROPIC_API_KEY") or st.text_input(
        "Anthropic API key", type="password", help="נשמר רק לסשן הנוכחי"
    )
    st.caption("החילוץ נעשה עם Claude (ראייה + פלט מובנה).")
    st.divider()
    if st.button("🧪 טען חשבוניות לדוגמה"):
        results.update(sample_invoices())
    if st.button("🗑️ נקה הכל"):
        results.clear()
        st.rerun()

# ---------------------------------------------------------------------------
# Upload + extract
# ---------------------------------------------------------------------------
st.title("🧾 Invoice GPT")
st.write("מעלים חשבוניות (תמונות או PDF), Claude מחלץ כותרת ושורות, בודקים, מתקנים ומורידים אקסל מוכן לקליטה.")

uploads = st.file_uploader(
    "העלאת חשבוניות", type=list(MEDIA_TYPES), accept_multiple_files=True, label_visibility="collapsed"
)

pending = []
for up in uploads or []:
    data = up.getvalue()
    key = file_hash(data)
    if key not in results:
        ext = up.name.rsplit(".", 1)[-1].lower()
        pending.append((key, up.name, data, MEDIA_TYPES[ext]))

if pending:
    if not api_key:
        st.warning("כדי לחלץ צריך API key בסרגל הצד.")
    elif st.button(f"✨ חלץ {len(pending)} חשבוניות", type="primary"):
        client = anthropic.Anthropic(api_key=api_key)
        progress = st.progress(0.0, text="מחלץ…")

        def work(item):
            key, name, data, media = item
            try:
                inv = extract_invoice(client, data, media)
                return key, {"file": name, "invoice": inv.model_dump(), "error": None}
            except (ExtractionError, anthropic.APIError) as e:
                return key, {"file": name, "invoice": None, "error": str(e)}

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(work, p) for p in pending]
            for i, fut in enumerate(as_completed(futures), 1):
                key, res = fut.result()
                results[key] = res
                progress.progress(i / len(pending), text=f"{i}/{len(pending)} · {res['file']}")
        progress.empty()

if not results:
    st.info("עוד אין חשבוניות. העלו קבצים, או נסו את הדוגמה מסרגל הצד.")
    st.stop()

for key, r in list(results.items()):
    if r["error"]:
        st.error(f"❌ {r['file']}: {r['error']}")

ok = {k: r for k, r in results.items() if r["invoice"]}
if not ok:
    st.stop()

# ---------------------------------------------------------------------------
# Editable tables
# ---------------------------------------------------------------------------
header_rows, line_rows = [], []
for key, r in ok.items():
    inv = r["invoice"]
    header_rows.append({"key": key, "file": r["file"], **{f: inv.get(f) for f in HEADER_FIELDS}})
    for n, line in enumerate(inv.get("lines", []), 1):
        line_rows.append({"key": key, "file": r["file"], "line_no": n, **line})

headers_df = pd.DataFrame(header_rows)
lines_df = pd.DataFrame(line_rows, columns=["key", "file", "line_no", *LINE_FIELDS])

st.subheader("📋 כותרות חשבוניות")
st.caption("אפשר לערוך כל תא. הבדיקות מתעדכנות אחרי כל שינוי.")
edited_headers = st.data_editor(
    headers_df, hide_index=True, width="stretch", disabled=["key", "file"],
    column_config={"key": None, **HEADER_LABELS},
    key="headers_editor",
)

with st.expander(f"📦 שורות פריטים ({len(lines_df)})"):
    edited_lines = st.data_editor(
        lines_df, hide_index=True, width="stretch", num_rows="dynamic",
        column_config={"key": None, **LINE_LABELS}, key="lines_editor",
    )


def _clean(v):
    return None if pd.isna(v) else v


# Rebuild invoices from the (possibly edited) tables and validate
invoices: dict[str, Invoice] = {}
for _, row in edited_headers.iterrows():
    key = row["key"]
    lines = [
        InvoiceLine(**{f: _clean(lr[f]) for f in LINE_FIELDS})
        for _, lr in edited_lines[edited_lines["key"] == key].iterrows()
        if _clean(lr["description"])
    ]
    fields = {f: _clean(row[f]) for f in HEADER_FIELDS}
    for f in ("supplier_tax_id", "invoice_number", "allocation_number"):
        if fields[f] is not None:
            fields[f] = str(fields[f])
    invoices[key] = Invoice(**fields, lines=lines, warnings=ok[key]["invoice"].get("warnings", []))

dups = find_duplicates(invoices)
issues = {k: validate_invoice(inv) for k, inv in invoices.items()}
for k, other in dups.items():
    issues[k].append(f"כפילות אפשרית של {ok[other]['file']}")

# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
n_bad = sum(1 for k in invoices if issues[k] or invoices[k].warnings)
ils_total = sum(inv.total or 0 for inv in invoices.values() if inv.currency == "ILS")
c1, c2, c3, c4 = st.columns(4)
c1.metric("חשבוניות", len(invoices))
c2.metric("סה\"כ ₪", f"{ils_total:,.2f}")
c3.metric("ספקים", len({inv.supplier_name for inv in invoices.values()}))
c4.metric("דורשות בדיקה ⚠️" if n_bad else "דורשות בדיקה", n_bad)

st.subheader("🔎 בדיקות")
for k, inv in invoices.items():
    probs, warns = issues[k], inv.warnings
    icon = "✅" if not probs and not warns else "⚠️"
    title = f"{icon} {ok[k]['file']} · {inv.supplier_name or '?'} · #{inv.invoice_number or '?'} · {inv.total or 0:,.2f} {inv.currency}"
    with st.expander(title, expanded=bool(probs)):
        if not probs and not warns:
            st.success("הכל מסתדר: סכומים, מע\"מ וספרת ביקורת של ח.פ.")
        for p in probs:
            st.error(p)
        for w in warns:
            st.warning(f"הערת Claude: {w}")

# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------
export_headers = edited_headers.copy()
export_headers["status"] = ["לבדיקה" if (issues[k] or invoices[k].warnings) else "תקין" for k in export_headers["key"]]
export_headers["issues"] = [" | ".join(issues[k] + invoices[k].warnings) for k in export_headers["key"]]
export_lines = edited_lines.merge(
    edited_headers[["key", "invoice_number", "supplier_name"]], on="key", how="left"
)

st.download_button(
    "⬇️ הורדת אקסל (כותרות + שורות + סיכום)",
    data=build_workbook(export_headers, export_lines),
    file_name="invoices.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    type="primary",
)
