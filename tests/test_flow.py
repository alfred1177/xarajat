import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.config import Settings
from app.db import DB
from app.texts import fmt_money, fmt_qty

TOKEN = "123456:TEST-TOKEN"
OWNER, BUYER, COOK, STRANGER = 1001, 2002, 3003, 4004


def init_data(uid: int, name: str, auth_date: int | None = None, token: str = TOKEN) -> str:
    fields = {
        "auth_date": str(auth_date or int(time.time())),
        "query_id": "AAA",
        "user": json.dumps({"id": uid, "first_name": name}, ensure_ascii=False),
    }
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


class FakeNotifier:
    def __init__(self):
        self.created, self.completed, self.pdfs, self.texts = [], [], [], []

    async def order_created(self, order):
        self.created.append(order)

    async def order_completed(self, order, pdf):
        self.completed.append((order, pdf))

    async def send_pdf(self, chat_id, order, pdf):
        self.pdfs.append((chat_id, pdf))

    async def send_texts(self, chat_id, order, texts):
        self.texts.append((chat_id, texts))


@pytest.fixture
def env():
    s = Settings(TOKEN, frozenset({OWNER}), "https://example.test", ":memory:", "0", 0, "сум", "Тест-кухня", False)
    db = DB(":memory:")
    n = FakeNotifier()
    c = TestClient(create_app(db, s, n))

    def h(uid, name="X"):
        return {"X-Init-Data": init_data(uid, name)}

    return c, db, n, h


def test_signature_required(env):
    c, *_ = env
    assert c.get("/api/me").status_code == 401
    bad = init_data(OWNER, "Хакер", token="999:OTHER")
    assert c.get("/api/me", headers={"X-Init-Data": bad}).status_code == 401
    old = init_data(OWNER, "Старый", auth_date=int(time.time()) - 3 * 24 * 3600)
    assert c.get("/api/me", headers={"X-Init-Data": old}).status_code == 401


def test_full_flow(env):
    c, db, n, h = env
    # владелец определяется по OWNER_IDS
    me = c.get("/api/me", headers=h(OWNER, "Владелец")).json()
    assert me["user"]["role"] == "owner"

    # новые пользователи без роли не имеют доступа
    c.get("/api/me", headers=h(BUYER, "Закупщик Али"))
    c.get("/api/me", headers=h(COOK, "Повар Иван"))
    c.get("/api/me", headers=h(STRANGER, "Чужой"))
    assert c.get("/api/catalog", headers=h(COOK)).status_code == 403
    assert c.put(f"/api/users/{COOK}/role", json={"role": "cook"}, headers=h(BUYER)).status_code == 403

    assert c.put(f"/api/users/{BUYER}/role", json={"role": "buyer"}, headers=h(OWNER)).status_code == 200
    assert c.put(f"/api/users/{COOK}/role", json={"role": "cook"}, headers=h(OWNER)).status_code == 200
    assert c.put(f"/api/users/{OWNER}/role", json={"role": "none"}, headers=h(OWNER)).status_code == 400

    # справочник
    cats = {x["name"]: x["id"] for x in c.get("/api/catalog", headers=h(OWNER)).json()["categories"]}
    assert "Овощи" in cats and "Бакалея" in cats
    assert c.post("/api/products", json={"category_id": cats["Овощи"], "name": "Огурцы", "unit": "кг"}, headers=h(COOK)).status_code == 403
    p = {}
    for cat, name, unit in [("Овощи", "Огурцы", "кг"), ("Овощи", "Помидоры", "кг"), ("Бакалея", "Рис", "кг"),
                            ("Специи", "Зира", "г"), ("Хозтовары и моющие", "Средство для пола", "шт")]:
        p[name] = c.post("/api/products", json={"category_id": cats[cat], "name": name, "unit": unit}, headers=h(OWNER)).json()["id"]

    # закупщик не может создать заявку, повар — может
    body = {"items": [{"product_id": p["Огурцы"], "qty": 5}, {"product_id": p["Помидоры"], "qty": 3.5},
                      {"product_id": p["Рис"], "qty": 10}, {"product_id": p["Средство для пола"], "qty": 2}],
            "note": "К 10 утра"}
    assert c.post("/api/orders", json=body, headers=h(BUYER)).status_code == 403
    assert c.post("/api/orders", json={"items": [{"product_id": p["Рис"], "qty": 0}]}, headers=h(COOK)).status_code == 422
    r = c.post("/api/orders", json=body, headers=h(COOK)).json()
    oid = r["id"]
    assert r["number"] == "00001"
    assert len(n.created) == 1

    # тексты для поставщиков сгруппированы
    o = c.get(f"/api/orders/{oid}", headers=h(BUYER)).json()
    texts = {t["category"]: t["text"] for t in o["supplier_texts"]}
    assert list(texts) == ["Овощи", "Бакалея", "Хозтовары и моющие"]
    assert "1. Огурцы — 5 кг" in texts["Овощи"] and "2. Помидоры — 3,5 кг" in texts["Овощи"]
    assert "Рис" not in texts["Овощи"]

    items = {it["name"]: it["id"] for it in o["items"]}
    # нельзя завершить, пока есть неотмеченные позиции
    assert c.post(f"/api/orders/{oid}/complete", json={}, headers=h(BUYER)).status_code == 409
    # повар не может отмечать покупку
    assert c.put(f"/api/items/{items['Огурцы']}", json={"status": "bought", "qty_bought": 5, "price": 1}, headers=h(COOK)).status_code == 403
    # «куплено» без цены — ошибка
    assert c.put(f"/api/items/{items['Огурцы']}", json={"status": "bought", "qty_bought": 5}, headers=h(BUYER)).status_code == 400

    c.put(f"/api/items/{items['Огурцы']}", json={"status": "bought", "qty_bought": 5.2, "price": 12000}, headers=h(BUYER))
    c.put(f"/api/items/{items['Помидоры']}", json={"status": "bought", "qty_bought": 3.5, "price": 15500.5}, headers=h(BUYER))
    c.put(f"/api/items/{items['Рис']}", json={"status": "bought", "qty_bought": 10, "price": 18000}, headers=h(BUYER))
    o = c.put(f"/api/items/{items['Средство для пола']}", json={"status": "missing"}, headers=h(BUYER)).json()
    # доп. позиция, не из заявки
    o = c.post(f"/api/orders/{oid}/extra", json={"product_id": p["Зира"]}, headers=h(BUYER)).json()
    extra = next(it for it in o["items"] if it["extra"])
    assert o["pending_count"] == 1
    o = c.put(f"/api/items/{extra['id']}", json={"status": "bought", "qty_bought": 200, "price": 90}, headers=h(BUYER)).json()

    expected = 5.2 * 12000 + 3.5 * 15500.5 + 10 * 18000 + 200 * 90  # 62400 + 54251.75 + 180000 + 18000
    assert o["total"] == round(expected, 2) == 314651.75
    assert o["pending_count"] == 0

    done = c.post(f"/api/orders/{oid}/complete", json={"receiver_name": "Иванов И."}, headers=h(BUYER)).json()
    assert done["status"] == "completed" and done["receiver_name"] == "Иванов И."
    assert len(n.completed) == 1 and n.completed[0][1].startswith(b"%PDF")

    # после завершения правки запрещены
    assert c.put(f"/api/items/{items['Рис']}", json={"status": "pending"}, headers=h(BUYER)).status_code == 409

    # история и повторная отправка PDF
    hist = c.get("/api/orders", headers=h(COOK)).json()
    assert hist[0]["status"] == "completed" and hist[0]["total"] == 314651.75
    assert c.post(f"/api/orders/{oid}/send_pdf", headers=h(COOK)).status_code == 200
    assert n.pdfs[0][0] == COOK

    # можно открыть заново для исправления
    assert c.post(f"/api/orders/{oid}/reopen", headers=h(BUYER)).json()["status"] == "new"

    # удалённый из справочника товар остаётся в истории
    c.delete(f"/api/products/{p['Рис']}", headers=h(OWNER))
    assert any(it["name"] == "Рис" for it in c.get(f"/api/orders/{oid}", headers=h(OWNER)).json()["items"])
    assert all(x["name"] != "Рис" for x in c.get("/api/catalog", headers=h(COOK)).json()["products"])


def test_formatting():
    assert fmt_qty(5.0) == "5" and fmt_qty(3.5) == "3,5" and fmt_qty(0.25) == "0,25"
    assert fmt_money(314651.75) == "314 651,75" and fmt_money(62400) == "62 400"
