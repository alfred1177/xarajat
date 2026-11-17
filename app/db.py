"""Работа с базой SQLite: схема и все запросы к данным."""
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

DEFAULT_CATEGORIES = [
    "Овощи",
    "Фрукты",
    "Зелень",
    "Мясо и птица",
    "Молочные продукты",
    "Бакалея",
    "Специи",
    "Средства гигиены",
    "Хозтовары и моющие",
    "Прочее",
]

UNITS = ["кг", "г", "шт", "л", "уп", "пачка", "банка", "пучок", "мешок"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    tg_id      INTEGER PRIMARY KEY,
    name       TEXT NOT NULL DEFAULT '',
    username   TEXT NOT NULL DEFAULT '',
    role       TEXT NOT NULL DEFAULT 'none',   -- owner | buyer | cook | none
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS categories (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name  TEXT NOT NULL UNIQUE,
    sort  INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS products (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id INTEGER NOT NULL REFERENCES categories(id),
    name        TEXT NOT NULL,
    unit        TEXT NOT NULL DEFAULT 'кг',
    active      INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS orders (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    status        TEXT NOT NULL DEFAULT 'new',  -- new | completed | cancelled
    created_by    INTEGER NOT NULL,
    created_at    TEXT NOT NULL,
    note          TEXT NOT NULL DEFAULT '',
    completed_at  TEXT,
    completed_by  INTEGER,
    receiver_name TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS order_items (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id      INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    product_id    INTEGER,
    name          TEXT NOT NULL,
    unit          TEXT NOT NULL,
    category      TEXT NOT NULL,
    category_sort INTEGER NOT NULL DEFAULT 0,
    qty_ordered   REAL NOT NULL DEFAULT 0,
    qty_bought    REAL,
    price         REAL,
    status        TEXT NOT NULL DEFAULT 'pending', -- pending | bought | missing
    extra         INTEGER NOT NULL DEFAULT 0       -- 1 = добавлено закупщиком, не было в заявке
);
"""


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class DB:
    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA)
        if not self._conn.execute("SELECT 1 FROM categories LIMIT 1").fetchone():
            for i, name in enumerate(DEFAULT_CATEGORIES):
                self._conn.execute("INSERT INTO categories(name, sort) VALUES (?, ?)", (name, i))
        self._conn.commit()

    @contextmanager
    def tx(self):
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def q(self, sql, args=()):
        return [dict(r) for r in self._conn.execute(sql, args).fetchall()]

    def one(self, sql, args=()):
        r = self._conn.execute(sql, args).fetchone()
        return dict(r) if r else None

    # ---------- пользователи ----------
    def upsert_user(self, tg_id: int, name: str, username: str) -> dict:
        with self.tx() as c:
            c.execute(
                """INSERT INTO users(tg_id, name, username, created_at) VALUES (?, ?, ?, ?)
                   ON CONFLICT(tg_id) DO UPDATE SET name=excluded.name, username=excluded.username""",
                (tg_id, name, username, now()),
            )
        return self.get_user(tg_id)

    def get_user(self, tg_id: int):
        return self.one("SELECT * FROM users WHERE tg_id=?", (tg_id,))

    def list_users(self):
        return self.q("SELECT * FROM users ORDER BY created_at")

    def set_role(self, tg_id: int, role: str):
        with self.tx() as c:
            c.execute("UPDATE users SET role=? WHERE tg_id=?", (role, tg_id))

    def ids_by_role(self, *roles: str) -> list[int]:
        marks = ",".join("?" * len(roles))
        return [r["tg_id"] for r in self.q(f"SELECT tg_id FROM users WHERE role IN ({marks})", roles)]

    # ---------- категории ----------
    def list_categories(self):
        return self.q("SELECT * FROM categories ORDER BY sort, id")

    def add_category(self, name: str) -> int:
        with self.tx() as c:
            sort = c.execute("SELECT COALESCE(MAX(sort), -1) + 1 FROM categories").fetchone()[0]
            return c.execute("INSERT INTO categories(name, sort) VALUES (?, ?)", (name, sort)).lastrowid

    def rename_category(self, cid: int, name: str):
        with self.tx() as c:
            c.execute("UPDATE categories SET name=? WHERE id=?", (name, cid))

    def delete_category(self, cid: int) -> bool:
        if self.one("SELECT 1 FROM products WHERE category_id=? AND active=1", (cid,)):
            return False
        with self.tx() as c:
            c.execute("DELETE FROM products WHERE category_id=?", (cid,))
            c.execute("DELETE FROM categories WHERE id=?", (cid,))
        return True

    # ---------- товары ----------
    def list_products(self, include_inactive=False):
        sql = """SELECT p.*, c.name AS category, c.sort AS category_sort FROM products p
                 JOIN categories c ON c.id = p.category_id"""
        if not include_inactive:
            sql += " WHERE p.active = 1"
        return self.q(sql + " ORDER BY c.sort, c.id, p.name COLLATE NOCASE")

    def add_product(self, category_id: int, name: str, unit: str) -> int:
        with self.tx() as c:
            return c.execute(
                "INSERT INTO products(category_id, name, unit) VALUES (?, ?, ?)", (category_id, name, unit)
            ).lastrowid

    def update_product(self, pid: int, category_id: int, name: str, unit: str):
        with self.tx() as c:
            c.execute(
                "UPDATE products SET category_id=?, name=?, unit=? WHERE id=?", (category_id, name, unit, pid)
            )

    def deactivate_product(self, pid: int):
        # не удаляем физически, чтобы не ломать историю
        with self.tx() as c:
            c.execute("UPDATE products SET active=0 WHERE id=?", (pid,))

    # ---------- заказы ----------
    def create_order(self, user_id: int, items: list[dict], note: str = "") -> int:
        """items: [{product_id, qty}] — название/ед./группа копируются из справочника."""
        with self.tx() as c:
            oid = c.execute(
                "INSERT INTO orders(created_by, created_at, note) VALUES (?, ?, ?)", (user_id, now(), note)
            ).lastrowid
            for it in items:
                p = c.execute(
                    """SELECT p.name, p.unit, c.name AS category, c.sort FROM products p
                       JOIN categories c ON c.id=p.category_id WHERE p.id=? AND p.active=1""",
                    (it["product_id"],),
                ).fetchone()
                if p is None:
                    raise ValueError(f"Товар {it['product_id']} не найден")
                c.execute(
                    """INSERT INTO order_items(order_id, product_id, name, unit, category, category_sort, qty_ordered)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (oid, it["product_id"], p["name"], p["unit"], p["category"], p["sort"], it["qty"]),
                )
        return oid

    def get_order(self, oid: int):
        o = self.one(
            """SELECT o.*, u.name AS created_by_name, b.name AS completed_by_name FROM orders o
               LEFT JOIN users u ON u.tg_id=o.created_by LEFT JOIN users b ON b.tg_id=o.completed_by
               WHERE o.id=?""",
            (oid,),
        )
        if o:
            o["items"] = self.q(
                "SELECT * FROM order_items WHERE order_id=? ORDER BY category_sort, category, extra, id", (oid,)
            )
        return o

    def list_orders(self, limit=100):
        return self.q(
            """SELECT o.id, o.status, o.created_at, o.completed_at, u.name AS created_by_name,
                      COUNT(i.id) AS items_count,
                      SUM(CASE WHEN i.status='pending' THEN 1 ELSE 0 END) AS pending_count,
                      COALESCE(SUM(CASE WHEN i.status='bought' THEN i.qty_bought*i.price END), 0) AS total
               FROM orders o LEFT JOIN users u ON u.tg_id=o.created_by
               LEFT JOIN order_items i ON i.order_id=o.id
               GROUP BY o.id ORDER BY o.id DESC LIMIT ?""",
            (limit,),
        )

    def get_item(self, item_id: int):
        return self.one("SELECT * FROM order_items WHERE id=?", (item_id,))

    def update_item(self, item_id: int, qty_bought, price, status: str):
        with self.tx() as c:
            c.execute(
                "UPDATE order_items SET qty_bought=?, price=?, status=? WHERE id=?",
                (qty_bought, price, status, item_id),
            )

    def add_extra_item(self, oid: int, product_id: int) -> int:
        with self.tx() as c:
            p = c.execute(
                """SELECT p.name, p.unit, c.name AS category, c.sort FROM products p
                   JOIN categories c ON c.id=p.category_id WHERE p.id=?""",
                (product_id,),
            ).fetchone()
            if p is None:
                raise ValueError("Товар не найден")
            return c.execute(
                """INSERT INTO order_items(order_id, product_id, name, unit, category, category_sort, qty_ordered, extra)
                   VALUES (?, ?, ?, ?, ?, ?, 0, 1)""",
                (oid, product_id, p["name"], p["unit"], p["category"], p["sort"]),
            ).lastrowid

    def delete_item(self, item_id: int):
        with self.tx() as c:
            c.execute("DELETE FROM order_items WHERE id=?", (item_id,))

    def complete_order(self, oid: int, user_id: int, receiver_name: str):
        with self.tx() as c:
            c.execute(
                "UPDATE orders SET status='completed', completed_at=?, completed_by=?, receiver_name=? WHERE id=?",
                (now(), user_id, receiver_name, oid),
            )

    def cancel_order(self, oid: int):
        with self.tx() as c:
            c.execute("UPDATE orders SET status='cancelled' WHERE id=?", (oid,))
