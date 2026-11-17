"""Генерация PDF-накладной (reportlab, шрифт DejaVu с поддержкой кириллицы)."""
from io import BytesIO
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .texts import fmt_date, fmt_money, fmt_qty, group_items, item_sum, order_number

FONT_DIR = Path(__file__).parent / "fonts"
pdfmetrics.registerFont(TTFont("DejaVu", str(FONT_DIR / "DejaVuSans.ttf")))
pdfmetrics.registerFont(TTFont("DejaVu-Bold", str(FONT_DIR / "DejaVuSans-Bold.ttf")))

S_TITLE = ParagraphStyle("t", fontName="DejaVu-Bold", fontSize=15, leading=19, alignment=1)
S_TEXT = ParagraphStyle("p", fontName="DejaVu", fontSize=10, leading=13)
S_CELL = ParagraphStyle("c", fontName="DejaVu", fontSize=9, leading=11)
S_CELL_MUTED = ParagraphStyle("cm", parent=S_CELL, textColor=colors.grey)


def build_invoice_pdf(order: dict, org_name: str = "", currency: str = "сум") -> bytes:
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=14 * mm, bottomMargin=14 * mm,
        title=f"Накладная № {order_number(order['id'])}",
    )
    date = fmt_date(order.get("completed_at") or order["created_at"])
    story = [
        Paragraph(f"НАКЛАДНАЯ № {order_number(order['id'])} от {date}", S_TITLE),
        Spacer(1, 4 * mm),
    ]
    if org_name:
        story.append(Paragraph(f"Получатель: <b>{_esc(org_name)}</b>", S_TEXT))
    story.append(Paragraph(f"Заявка от {fmt_date(order['created_at'])}, составил: {_esc(order.get('created_by_name') or '—')}", S_TEXT))
    story.append(Paragraph(f"Закупщик: {_esc(order.get('completed_by_name') or '—')}", S_TEXT))
    story.append(Spacer(1, 4 * mm))

    header = ["№", "Наименование", "Ед.", "Заказано", "Куплено", f"Цена, {currency}", f"Сумма, {currency}"]
    rows = [header]
    style = [
        ("FONT", (0, 0), (-1, -1), "DejaVu", 9),
        ("FONT", (0, 0), (-1, 0), "DejaVu-Bold", 9),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8E8E8")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#999999")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
    ]
    total = 0.0
    n = 0
    for cat, items in group_items(order["items"]):
        r = len(rows)
        rows.append([cat, "", "", "", "", "", ""])
        style += [
            ("SPAN", (0, r), (-1, r)),
            ("FONT", (0, r), (-1, r), "DejaVu-Bold", 9),
            ("BACKGROUND", (0, r), (-1, r), colors.HexColor("#F4F4F4")),
            ("ALIGN", (0, r), (-1, r), "LEFT"),
        ]
        subtotal = 0.0
        for it in items:
            n += 1
            s = item_sum(it)
            subtotal += s
            if it["status"] == "missing":
                name = Paragraph(f"{_esc(it['name'])} — нет в наличии", S_CELL_MUTED)
                bought, price, summ = "—", "—", "0"
            else:
                label = _esc(it["name"]) + (" <i>(доп.)</i>" if it["extra"] else "")
                name = Paragraph(label, S_CELL)
                bought, price, summ = fmt_qty(it["qty_bought"]), fmt_money(it["price"]), fmt_money(s)
            ordered = fmt_qty(it["qty_ordered"]) if it["qty_ordered"] else "—"
            rows.append([str(n), name, it["unit"], ordered, bought, price, summ])
        r = len(rows)
        rows.append(["", f"Итого: {cat}", "", "", "", "", fmt_money(subtotal)])
        style += [("SPAN", (1, r), (5, r)), ("FONT", (0, r), (-1, r), "DejaVu-Bold", 9), ("ALIGN", (1, r), (5, r), "RIGHT")]
        total += subtotal

    r = len(rows)
    rows.append(["", "ВСЕГО", "", "", "", "", fmt_money(total)])
    style += [
        ("SPAN", (1, r), (5, r)), ("FONT", (0, r), (-1, r), "DejaVu-Bold", 10),
        ("ALIGN", (1, r), (5, r), "RIGHT"), ("BACKGROUND", (0, r), (-1, r), colors.HexColor("#E8E8E8")),
    ]

    widths = [9 * mm, 63 * mm, 13 * mm, 20 * mm, 20 * mm, 27 * mm, 30 * mm]
    t = Table(rows, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle(style))
    story += [t, Spacer(1, 6 * mm)]
    bought = sum(1 for it in order["items"] if it["status"] == "bought")
    story.append(Paragraph(
        f"Всего позиций: {n}, куплено: {bought}. На сумму: <b>{fmt_money(total)} {currency}</b>", S_TEXT))
    story.append(Spacer(1, 12 * mm))

    sign = Table(
        [
            ["Сдал (закупщик):", "_______________________", f"/ {order.get('completed_by_name') or '________________'} /"],
            ["", "подпись", ""],
            ["", "", ""],
            ["Принял:", "_______________________", f"/ {order.get('receiver_name') or '________________'} /"],
            ["", "подпись", ""],
        ],
        colWidths=[35 * mm, 60 * mm, 80 * mm],
    )
    sign.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, -1), "DejaVu", 10),
        ("FONT", (1, 1), (1, 1), "DejaVu", 7), ("FONT", (1, 4), (1, 4), "DejaVu", 7),
        ("TEXTCOLOR", (1, 1), (1, 1), colors.grey), ("TEXTCOLOR", (1, 4), (1, 4), colors.grey),
        ("ALIGN", (1, 0), (1, -1), "CENTER"),
    ]))
    story.append(sign)
    doc.build(story)
    return buf.getvalue()


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
