"""HTTP API для Mini App + раздача статики интерфейса."""
import logging
import time
from pathlib import Path
from typing import Literal, Protocol

from aiogram.utils.web_app import safe_parse_webapp_init_data
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import Settings
from .db import UNITS, DB
from .pdf import build_invoice_pdf
from .texts import item_sum, order_number, supplier_texts

log = logging.getLogger(__name__)
WEBAPP_DIR = Path(__file__).resolve().parent.parent / "webapp"
INIT_DATA_MAX_AGE = 24 * 3600  # сек.

ROLES = ("owner", "buyer", "cook", "none")


class Notifier(Protocol):
    async def order_created(self, order: dict) -> None: ...
    async def order_completed(self, order: dict, pdf: bytes) -> None: ...
    async def send_pdf(self, chat_id: int, order: dict, pdf: bytes) -> None: ...
    async def send_texts(self, chat_id: int, order: dict, texts: list[dict]) -> None: ...


# ---------- схемы запросов ----------
class CategoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)


class ProductIn(BaseModel):
    category_id: int
    name: str = Field(min_length=1, max_length=100)
    unit: str = Field(min_length=1, max_length=15)


class RoleIn(BaseModel):
    role: Literal["owner", "buyer", "cook", "none"]


class OrderItemIn(BaseModel):
    product_id: int
    qty: float = Field(gt=0, le=100000)


class OrderIn(BaseModel):
    items: list[OrderItemIn] = Field(min_length=1)
    note: str = Field(default="", max_length=500)


class ItemUpdateIn(BaseModel):
    status: Literal["pending", "bought", "missing"]
    qty_bought: float | None = Field(default=None, ge=0, le=100000)
    price: float | None = Field(default=None, ge=0, le=1e10)


class ExtraIn(BaseModel):
    product_id: int


class CompleteIn(BaseModel):
    receiver_name: str = Field(default="", max_length=100)


def create_app(db: DB, settings: Settings, notifier: Notifier) -> FastAPI:
    app = FastAPI(title="Закупки", docs_url=None, redoc_url=None)

    # ---------- авторизация через подпись Telegram ----------
    def current_user(x_init_data: str = Header(default=""), x_dev_user: str = Header(default="")) -> dict:
        if settings.dev_mode and x_dev_user:
            uid = int(x_dev_user)
            u = db.get_user(uid) or db.upsert_user(uid, f"Dev {uid}", "")
        else:
            try:
                data = safe_parse_webapp_init_data(settings.bot_token, x_init_data)
            except ValueError:
                raise HTTPException(401, "Неверная подпись Telegram. Откройте приложение из бота.")
            if time.time() - data.auth_date.timestamp() > INIT_DATA_MAX_AGE:
                raise HTTPException(401, "Сессия устарела. Закройте и откройте приложение заново.")
            tu = data.user
            name = " ".join(x for x in (tu.first_name, tu.last_name) if x)
            u = db.upsert_user(tu.id, name, tu.username or "")
        if u["tg_id"] in settings.owner_ids and u["role"] != "owner":
            db.set_role(u["tg_id"], "owner")
            u["role"] = "owner"
        return u

    def need(*roles: str):
        def dep(u: dict = Depends(current_user)) -> dict:
            if u["role"] not in roles:
                raise HTTPException(403, "Недостаточно прав")
            return u
        return dep

    any_role = need("owner", "buyer", "cook")
    admin = need("owner")
    cook = need("owner", "cook")
    buyer = need("owner", "buyer")

    def load_order(oid: int) -> dict:
        o = db.get_order(oid)
        if not o:
            raise HTTPException(404, "Заказ не найден")
        return o

    def editable(o: dict):
        if o["status"] != "new":
            raise HTTPException(409, "Заказ уже закрыт")

    def order_view(o: dict) -> dict:
        for it in o["items"]:
            it["sum"] = item_sum(it)
        o["number"] = order_number(o["id"])
        o["total"] = round(sum(it["sum"] for it in o["items"]), 2)
        o["pending_count"] = sum(1 for it in o["items"] if it["status"] == "pending")
        o["supplier_texts"] = supplier_texts(o)
        return o

    # ---------- общие ----------
    @app.get("/api/me")
    def me(u: dict = Depends(current_user)):
        return {"user": u, "units": UNITS, "currency": settings.currency, "org_name": settings.org_name}

    @app.get("/api/catalog")
    def catalog(_: dict = Depends(any_role)):
        return {"categories": db.list_categories(), "products": db.list_products()}

    # ---------- администрирование ----------
    @app.get("/api/users")
    def users(_: dict = Depends(admin)):
        return db.list_users()

    @app.put("/api/users/{tg_id}/role")
    def set_role(tg_id: int, body: RoleIn, u: dict = Depends(admin)):
        if not db.get_user(tg_id):
            raise HTTPException(404, "Пользователь не найден")
        if tg_id in settings.owner_ids and body.role != "owner":
            raise HTTPException(400, "Владелец задаётся в настройках (OWNER_IDS)")
        db.set_role(tg_id, body.role)
        return {"ok": True}

    @app.post("/api/categories")
    def add_category(body: CategoryIn, _: dict = Depends(admin)):
        try:
            return {"id": db.add_category(body.name.strip())}
        except Exception:
            raise HTTPException(400, "Такая группа уже есть")

    @app.put("/api/categories/{cid}")
    def rename_category(cid: int, body: CategoryIn, _: dict = Depends(admin)):
        db.rename_category(cid, body.name.strip())
        return {"ok": True}

    @app.delete("/api/categories/{cid}")
    def delete_category(cid: int, _: dict = Depends(admin)):
        if not db.delete_category(cid):
            raise HTTPException(409, "В группе есть товары — сначала удалите или перенесите их")
        return {"ok": True}

    @app.get("/api/products")
    def products_all(_: dict = Depends(admin)):
        return db.list_products(include_inactive=False)

    @app.post("/api/products")
    def add_product(body: ProductIn, _: dict = Depends(admin)):
        return {"id": db.add_product(body.category_id, body.name.strip(), body.unit.strip())}

    @app.put("/api/products/{pid}")
    def update_product(pid: int, body: ProductIn, _: dict = Depends(admin)):
        db.update_product(pid, body.category_id, body.name.strip(), body.unit.strip())
        return {"ok": True}

    @app.delete("/api/products/{pid}")
    def delete_product(pid: int, _: dict = Depends(admin)):
        db.deactivate_product(pid)
        return {"ok": True}

    # ---------- заказы ----------
    @app.post("/api/orders")
    async def create_order(body: OrderIn, u: dict = Depends(cook)):
        try:
            oid = db.create_order(u["tg_id"], [i.model_dump() for i in body.items], body.note.strip())
        except ValueError as e:
            raise HTTPException(400, str(e))
        o = order_view(load_order(oid))
        try:
            await notifier.order_created(o)
        except Exception:
            log.exception("Не удалось отправить уведомление о заказе %s", oid)
        return {"id": oid, "number": o["number"]}

    @app.get("/api/orders")
    def list_orders(_: dict = Depends(any_role)):
        rows = db.list_orders()
        for r in rows:
            r["number"] = order_number(r["id"])
        return rows

    @app.get("/api/orders/{oid}")
    def get_order(oid: int, _: dict = Depends(any_role)):
        return order_view(load_order(oid))

    @app.put("/api/items/{item_id}")
    def update_item(item_id: int, body: ItemUpdateIn, _: dict = Depends(buyer)):
        it = db.get_item(item_id)
        if not it:
            raise HTTPException(404, "Позиция не найдена")
        editable(load_order(it["order_id"]))
        if body.status == "bought":
            if not body.qty_bought or body.price is None:
                raise HTTPException(400, "Укажите купленное количество и цену")
            db.update_item(item_id, body.qty_bought, body.price, "bought")
        elif body.status == "missing":
            db.update_item(item_id, None, None, "missing")
        else:
            db.update_item(item_id, body.qty_bought, body.price, "pending")
        return order_view(load_order(it["order_id"]))

    @app.post("/api/orders/{oid}/extra")
    def add_extra(oid: int, body: ExtraIn, _: dict = Depends(buyer)):
        editable(load_order(oid))
        try:
            db.add_extra_item(oid, body.product_id)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return order_view(load_order(oid))

    @app.delete("/api/items/{item_id}")
    def delete_item(item_id: int, _: dict = Depends(buyer)):
        it = db.get_item(item_id)
        if not it:
            raise HTTPException(404, "Позиция не найдена")
        editable(load_order(it["order_id"]))
        if not it["extra"]:
            raise HTTPException(400, "Удалять можно только добавленные закупщиком позиции. Для остальных — «Нет в наличии».")
        db.delete_item(item_id)
        return order_view(load_order(it["order_id"]))

    @app.post("/api/orders/{oid}/complete")
    async def complete(oid: int, body: CompleteIn, u: dict = Depends(buyer)):
        o = load_order(oid)
        editable(o)
        if any(it["status"] == "pending" for it in o["items"]):
            raise HTTPException(409, "Отметьте все позиции: «Куплено» или «Нет в наличии»")
        receiver = body.receiver_name.strip() or (o.get("created_by_name") or "")
        db.complete_order(oid, u["tg_id"], receiver)
        o = order_view(load_order(oid))
        pdf = build_invoice_pdf(o, settings.org_name, settings.currency)
        try:
            await notifier.order_completed(o, pdf)
        except Exception:
            log.exception("Не удалось отправить накладную %s", oid)
        return o

    @app.post("/api/orders/{oid}/reopen")
    def reopen(oid: int, _: dict = Depends(buyer)):
        o = load_order(oid)
        if o["status"] != "completed":
            raise HTTPException(409, "Открыть заново можно только завершённый заказ")
        with db.tx() as c:
            c.execute("UPDATE orders SET status='new', completed_at=NULL WHERE id=?", (oid,))
        return order_view(load_order(oid))

    @app.post("/api/orders/{oid}/cancel")
    def cancel(oid: int, _: dict = Depends(need("owner", "buyer", "cook"))):
        o = load_order(oid)
        editable(o)
        db.cancel_order(oid)
        return {"ok": True}

    @app.post("/api/orders/{oid}/send_pdf")
    async def send_pdf(oid: int, u: dict = Depends(any_role)):
        o = order_view(load_order(oid))
        if o["status"] != "completed":
            raise HTTPException(409, "Накладная формируется после завершения закупки")
        await notifier.send_pdf(u["tg_id"], o, build_invoice_pdf(o, settings.org_name, settings.currency))
        return {"ok": True}

    @app.post("/api/orders/{oid}/send_texts")
    async def send_texts(oid: int, u: dict = Depends(any_role)):
        o = order_view(load_order(oid))
        await notifier.send_texts(u["tg_id"], o, o["supplier_texts"])
        return {"ok": True}

    # для просмотра в браузере в DEV_MODE: /api/orders/1/pdf
    @app.get("/api/orders/{oid}/pdf")
    def pdf_file(oid: int, _: dict = Depends(any_role)):
        o = order_view(load_order(oid))
        return Response(
            build_invoice_pdf(o, settings.org_name, settings.currency),
            media_type="application/pdf",
            headers={"Content-Disposition": f'inline; filename="nakladnaya_{o["number"]}.pdf"'},
        )

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/")
    def index():
        return FileResponse(WEBAPP_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=WEBAPP_DIR), name="static")
    return app
