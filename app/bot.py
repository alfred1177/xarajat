"""Telegram-бот: /start, кнопка открытия Mini App и уведомления."""
import html
import logging

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import (
    BufferedInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MenuButtonWebApp,
    Message,
    WebAppInfo,
)

from .config import Settings
from .db import DB
from .texts import fmt_date, fmt_money

log = logging.getLogger(__name__)

ROLE_NAMES = {"owner": "владелец", "buyer": "закупщик", "cook": "повар", "none": "нет доступа"}


def open_button(settings: Settings, text: str, order_id: int | None = None) -> InlineKeyboardMarkup:
    url = settings.webapp_url + "/" + (f"?order={order_id}" if order_id else "")
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=text, web_app=WebAppInfo(url=url))]])


def build_dispatcher(db: DB, settings: Settings) -> Dispatcher:
    dp = Dispatcher()

    @dp.message(CommandStart())
    async def start(m: Message, bot: Bot):
        u = m.from_user
        name = " ".join(x for x in (u.first_name, u.last_name) if x)
        existed = db.get_user(u.id) is not None
        user = db.upsert_user(u.id, name, u.username or "")
        if u.id in settings.owner_ids and user["role"] != "owner":
            db.set_role(u.id, "owner")
            user["role"] = "owner"

        if user["role"] == "none":
            await m.answer(
                f"Здравствуйте, {html.escape(name)}!\n\nУ вас пока нет доступа. "
                f"Ваш Telegram ID: <code>{u.id}</code>\nВладелец получил уведомление и назначит вам роль."
            )
            if not existed:
                for oid in settings.owner_ids:
                    try:
                        await bot.send_message(
                            oid,
                            f"👤 Новый пользователь: <b>{html.escape(name)}</b>"
                            f"{' @' + u.username if u.username else ''} (ID <code>{u.id}</code>).\n"
                            "Назначьте роль: Приложение → Настройки → Пользователи.",
                            reply_markup=open_button(settings, "Открыть приложение"),
                        )
                    except Exception:
                        log.exception("Не удалось уведомить владельца %s", oid)
            return

        await m.answer(
            f"Здравствуйте, {html.escape(name)}! Ваша роль: <b>{ROLE_NAMES[user['role']]}</b>.\n"
            "Нажмите кнопку ниже, чтобы открыть приложение закупок.",
            reply_markup=open_button(settings, "🛒 Открыть закупки"),
        )

    @dp.message(F.text)
    async def fallback(m: Message):
        await m.answer("Откройте приложение кнопкой ниже.", reply_markup=open_button(settings, "🛒 Открыть закупки"))

    return dp


async def setup_menu_button(bot: Bot, settings: Settings):
    if settings.webapp_url.startswith("https://"):
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(text="Закупки", web_app=WebAppInfo(url=settings.webapp_url + "/"))
        )
    else:
        log.warning("WEBAPP_URL не https — кнопка меню не установлена (Telegram требует HTTPS)")


class TelegramNotifier:
    def __init__(self, bot: Bot, db: DB, settings: Settings):
        self.bot, self.db, self.s = bot, db, settings

    async def _safe(self, coro):
        try:
            await coro
        except Exception:
            log.exception("Ошибка отправки в Telegram")

    async def order_created(self, order: dict) -> None:
        recipients = set(self.db.ids_by_role("buyer", "owner")) - {order["created_by"]}
        head = (
            f"🛒 <b>Новая заявка № {order['number']}</b> от {fmt_date(order['created_at'])}\n"
            f"Составил: {html.escape(order.get('created_by_name') or '')}\n"
            f"Позиций: {len(order['items'])}"
        )
        if order.get("note"):
            head += f"\nКомментарий: {html.escape(order['note'])}"
        head += "\n\nНиже — списки по группам, их можно сразу переслать поставщикам."
        for uid in recipients:
            await self._safe(self.bot.send_message(
                uid, head, reply_markup=open_button(self.s, "Открыть заказ", order["id"])))
            for t in order["supplier_texts"]:
                await self._safe(self.bot.send_message(uid, html.escape(t["text"])))

    async def order_completed(self, order: dict, pdf: bytes) -> None:
        recipients = set(self.db.ids_by_role("buyer", "owner")) | {order["created_by"]}
        for uid in recipients:
            await self.send_pdf(uid, order, pdf)

    async def send_pdf(self, chat_id: int, order: dict, pdf: bytes) -> None:
        caption = (
            f"📄 Накладная № {order['number']} от {fmt_date(order['completed_at'])}\n"
            f"Итого: <b>{fmt_money(order['total'])} {self.s.currency}</b>"
        )
        await self._safe(self.bot.send_document(
            chat_id, BufferedInputFile(pdf, filename=f"nakladnaya_{order['number']}.pdf"), caption=caption))

    async def send_texts(self, chat_id: int, order: dict, texts: list[dict]) -> None:
        if not texts:
            await self._safe(self.bot.send_message(chat_id, "В заказе нет позиций для поставщиков."))
        for t in texts:
            await self._safe(self.bot.send_message(chat_id, html.escape(t["text"])))
