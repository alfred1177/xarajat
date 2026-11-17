"""Локальный просмотр интерфейса БЕЗ Telegram (подпись не проверяется).

    python scripts/dev_server.py
    открыть http://localhost:8080/?dev=1   (1 — владелец, 2 — закупщик, 3 — повар)

Использует отдельную базу data/dev.db с демо-товарами. Не запускайте на сервере!
"""
import logging
import sys
from pathlib import Path

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.api import create_app  # noqa: E402
from app.config import BASE_DIR, Settings  # noqa: E402
from app.db import DB  # noqa: E402

DEMO = {
    "Овощи": [("Огурцы", "кг"), ("Помидоры", "кг"), ("Картофель", "кг"), ("Лук репчатый", "кг"), ("Морковь", "кг")],
    "Фрукты": [("Лимоны", "кг"), ("Яблоки", "кг")],
    "Зелень": [("Укроп", "пучок"), ("Кинза", "пучок")],
    "Бакалея": [("Рис девзира", "кг"), ("Мука", "мешок"), ("Масло подсолнечное", "л")],
    "Специи": [("Зира", "г"), ("Перец чёрный молотый", "г")],
    "Средства гигиены": [("Перчатки одноразовые", "уп")],
    "Хозтовары и моющие": [("Средство для мытья полов", "шт"), ("Губки", "уп")],
}


class PrintNotifier:
    async def order_created(self, o): print(f"[бот] новая заявка {o['number']}")
    async def order_completed(self, o, pdf): print(f"[бот] накладная {o['number']}, {len(pdf)} байт")
    async def send_pdf(self, chat_id, o, pdf): print(f"[бот] PDF -> {chat_id}")
    async def send_texts(self, chat_id, o, t): print(f"[бот] {len(t)} текстов -> {chat_id}")


def main():
    logging.basicConfig(level=logging.INFO)
    db = DB(str(BASE_DIR / "data" / "dev.db"))
    for uid, name, role in [(1, "Владелец", "owner"), (2, "Закупщик Али", "buyer"), (3, "Повар Иван", "cook")]:
        db.upsert_user(uid, name, "")
        db.set_role(uid, role)
    if not db.list_products():
        cats = {c["name"]: c["id"] for c in db.list_categories()}
        for cat, items in DEMO.items():
            for name, unit in items:
                db.add_product(cats[cat], name, unit)
    s = Settings("0:dev", frozenset({1}), "http://localhost:8080", db.path, "127.0.0.1", 8080, "сум", "Кухня (демо)", True)
    uvicorn.run(create_app(db, s, PrintNotifier()), host="127.0.0.1", port=8080)


if __name__ == "__main__":
    main()
