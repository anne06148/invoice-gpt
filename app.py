import streamlit as st
import pandas as pd
from PIL import Image
import pytesseract
import re

st.set_page_config(page_title="Invoice GPT", layout="centered")

if "messages" not in st.session_state:
    st.session_state.messages = []

st.title("🤖 Invoice GPT – צ'אט לחילוץ חשבוניות לאקסל")

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

with st.chat_message("assistant"):
    st.markdown("היי אנה, תעלי חשבונית (תמונה), ואני אחַלֵּץ ממנה נתונים ואכין לך אקסל.")

uploaded_file = st.file_uploader("העלאת חשבונית", type=["png", "jpg", "jpeg"])

def extract_invoice_data(text):
    data = {}
    data["מספר חשבונית"] = re.search(r"חשבונית[:\-]?\s*(\d+)", text).group(1) if re.search(r"חשבונית[:\-]?\s*(\d+)", text) else ""
    data["תאריך"] = re.search(r"(\d{1,2}[\/\.-]\d{1,2}[\/\.-]\d{2,4})", text).group(1) if re.search(r"(\d{1,2}[\/\.-]\d{1,2}[\/\.-]\d{2,4})", text) else ""
    data["סכום כולל"] = re.search(r"סה\"כ[:\-]?\s*([\d,\.]+)", text).group(1) if re.search(r"סה\"כ[:\-]?\s*([\d,\.]+)", text) else ""
    return data

if uploaded_file:
    st.session_state.messages.append({"role": "user", "content": "הנה החשבונית שלי 😊"})
    with st.chat_message("user"):
        st.markdown("הנה החשבונית שלי 😊")

    image = Image.open(uploaded_file)
    text = pytesseract.image_to_string(image, lang="heb+eng")

    with st.chat_message("assistant"):
        st.markdown("מחלצת נתונים מהחשבונית…")

        data = extract_invoice_data(text)

        st.markdown("### 📄 פרטי חשבונית שמצאתי:")
        st.table(pd.DataFrame([data]))

        df = pd.DataFrame([data])
        df.to_excel("invoice.xlsx", index=False)

        st.success("יצרתי עבורך קובץ אקסל ✅")
        with open("invoice.xlsx", "rb") as f:
            st.download_button("⬇️ הורדת אקסל", data=f, file_name="invoice.xlsx")

    st.session_state.messages.append({
        "role": "assistant",
        "content": f"מצאתי חשבונית עם מספר: {data.get('מספר חשבונית','')} וסכום כולל: {data.get('סכום כולל','')}."
    })

