"""Форматирование чисел и текстов для поставщиков."""
from datetime import datetime
from itertools import groupby


def fmt_qty(x) -> str:
    """1.0 -> '1', 2.5 -> '2,5', 0.25 -> '0,25'."""
    if x is None:
        return "—"
    s = f"{float(x):.3f}".rstrip("0").rstrip(".")
    return s.replace(".", ",")


def fmt_money(x) -> str:
    """12500 -> '12 500', 12500.5 -> '12 500,50'."""
    if x is None:
        return "—"
    x = round(float(x), 2)
    if x == int(x):
        return f"{int(x):,}".replace(",", " ")
    return f"{x:,.2f}".replace(",", " ").replace(".", ",")


def fmt_date(s: str | None) -> str:
    if not s:
        return ""
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").strftime("%d.%m.%Y")


def order_number(oid: int) -> str:
    return f"{oid:05d}"


def item_sum(it: dict) -> float:
    if it["status"] != "bought" or it["qty_bought"] is None or it["price"] is None:
        return 0.0
    return round(float(it["qty_bought"]) * float(it["price"]), 2)


def group_items(items: list[dict]) -> list[tuple[str, list[dict]]]:
    items = sorted(items, key=lambda i: (i["category_sort"], i["category"]))
    return [(cat, list(g)) for cat, g in groupby(items, key=lambda i: i["category"])]


def supplier_text(category: str, items: list[dict], date: str = "") -> str:
    """Текст для отправки поставщику одной группы. Позиции без количества пропускаются."""
    lines = [f"Заявка{(' на ' + date) if date else ''} — {category}:"]
    n = 0
    for it in items:
        if it["qty_ordered"] and not it.get("extra"):
            n += 1
            lines.append(f"{n}. {it['name']} — {fmt_qty(it['qty_ordered'])} {it['unit']}")
    return "\n".join(lines) if n else ""


def supplier_texts(order: dict) -> list[dict]:
    date = fmt_date(order["created_at"])
    out = []
    for cat, items in group_items(order["items"]):
        t = supplier_text(cat, items, date)
        if t:
            out.append({"category": cat, "text": t})
    return out
