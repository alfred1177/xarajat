"use strict";
const tg = window.Telegram && window.Telegram.WebApp;
if (tg) { tg.ready(); tg.expand(); }

const S = { me: null, currency: "сум", units: [], catalog: null, view: null, stack: [], draft: {}, edits: {} };
const $view = document.getElementById("view");
const $title = document.getElementById("title");
const $tabs = document.getElementById("tabs");
const $back = document.getElementById("back");
const DRAFT_KEY = "zakupki_draft";
try { S.draft = JSON.parse(localStorage.getItem(DRAFT_KEY) || "{}"); } catch (e) { S.draft = {}; }

// ---------- утилиты ----------
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const num = v => { const n = parseFloat(String(v ?? "").replace(",", ".").replace(/\s/g, "")); return isFinite(n) ? n : null; };
const fq = v => v == null ? "—" : String(Math.round(v * 1000) / 1000).replace(".", ",");
const fm = v => v == null ? "—" : (Math.round(v * 100) / 100).toLocaleString("ru-RU", { maximumFractionDigits: 2 }).replace(/ /g, " ");
const fd = s => s ? s.slice(8, 10) + "." + s.slice(5, 7) + "." + s.slice(0, 4) : "";
const STATUS = { new: "В работе", completed: "Завершён", cancelled: "Отменён" };
const role = () => S.me.role;
const isAdmin = () => role() === "owner";
const canOrder = () => ["owner", "cook"].includes(role());
const canBuy = () => ["owner", "buyer"].includes(role());

function toast(msg) {
  const t = document.getElementById("toast");
  t.textContent = msg; t.classList.remove("hidden");
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.add("hidden"), 2200);
}
function haptic(type) { try { tg && tg.HapticFeedback.notificationOccurred(type); } catch (e) {} }
function saveDraft() { try { localStorage.setItem(DRAFT_KEY, JSON.stringify(S.draft)); } catch (e) {} }

async function api(method, path, body) {
  const headers = { "Content-Type": "application/json" };
  if (tg && tg.initData) headers["X-Init-Data"] = tg.initData;
  const dev = new URLSearchParams(location.search).get("dev");
  if (dev) headers["X-Dev-User"] = dev;
  const r = await fetch(path, { method, headers, body: body ? JSON.stringify(body) : undefined });
  if (!r.ok) {
    let msg = "Ошибка " + r.status;
    try { const j = await r.json(); msg = typeof j.detail === "string" ? j.detail : "Проверьте введённые данные"; } catch (e) {}
    throw new Error(msg);
  }
  return r.json();
}
async function run(fn) { try { return await fn(); } catch (e) { haptic("error"); toast(e.message); } }

async function copyText(text) {
  try { await navigator.clipboard.writeText(text); toast("Скопировано"); return; } catch (e) {}
  const ta = document.createElement("textarea");
  ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
  document.body.appendChild(ta); ta.select();
  try { document.execCommand("copy"); toast("Скопировано"); } catch (e) { toast("Не удалось скопировать — выделите текст вручную"); }
  ta.remove();
}

// ---------- навигация ----------
function go(view, params = {}, push = true) {
  if (push && S.view) S.stack.push(S.view);
  S.view = { view, params };
  render();
}
function back() { if (S.stack.length) { S.view = S.stack.pop(); render(); } }
$back.onclick = back;
if (tg) tg.BackButton.onClick(back);

function tabsFor() {
  const t = [];
  if (canOrder()) t.push(["new", "📝", "Заявка"]);
  t.push(["orders", "📋", "Заказы"]);
  if (isAdmin()) t.push(["settings", "⚙️", "Настройки"]);
  return t;
}
function renderTabs() {
  const t = tabsFor();
  $tabs.classList.remove("hidden");
  $tabs.innerHTML = t.map(([v, i, n]) => `<button data-v="${v}" class="${S.view.view === v ? "active" : ""}"><b>${i}</b>${n}</button>`).join("");
  $tabs.querySelectorAll("button").forEach(b => b.onclick = () => { S.stack = []; go(b.dataset.v, {}, false); });
}

async function render() {
  const hasBack = S.stack.length > 0;
  $back.classList.toggle("hidden", !hasBack);
  if (tg) hasBack ? tg.BackButton.show() : tg.BackButton.hide();
  renderTabs();
  window.scrollTo(0, 0);
  const v = S.view;
  const views = { new: viewNew, orders: viewOrders, order: viewOrder, settings: viewSettings, users: viewUsers, catalog: viewCatalog };
  await run(() => views[v.view](v.params));
}

// ---------- заявка повара ----------
async function loadCatalog() { S.catalog = await api("GET", "/api/catalog"); return S.catalog; }

async function viewNew() {
  $title.textContent = "Новая заявка";
  const cat = await loadCatalog();
  if (!cat.products.length) {
    $view.innerHTML = `<div class="card center muted">Справочник товаров пуст.${isAdmin() ? " Добавьте товары в «Настройки → Товары»." : " Попросите владельца добавить товары."}</div>`;
    return;
  }
  const ids = new Set(cat.products.map(p => String(p.id)));
  Object.keys(S.draft).forEach(k => { if (!ids.has(k)) delete S.draft[k]; });

  $view.innerHTML = `
    <input class="search" id="q" placeholder="🔍 Поиск товара" autocomplete="off">
    <div id="groups"></div>
    <div class="card"><label class="f">Комментарий для закупщика</label>
      <textarea id="note" rows="2" placeholder="Необязательно">${esc(S.draft._note || "")}</textarea></div>
    <div class="bar"><div class="row">
      <button class="ghost danger" id="clear">Очистить</button>
      <button class="primary grow" id="send"></button></div></div>`;
  const draw = () => {
    const q = document.getElementById("q").value.trim().toLowerCase();
    let html = "";
    for (const c of cat.categories) {
      const ps = cat.products.filter(p => p.category_id === c.id && (!q || p.name.toLowerCase().includes(q)));
      if (!ps.length) continue;
      const cnt = ps.filter(p => S.draft[p.id]).length;
      html += `<div class="group-title"><span>${esc(c.name)}</span>${cnt ? `<span class="badge">${cnt}</span>` : ""}</div><div class="list">`;
      for (const p of ps) {
        const v = S.draft[p.id];
        html += `<div class="item row ${v ? "has" : ""}" data-id="${p.id}">
          <div class="grow">${esc(p.name)}</div>
          <div class="qty"><button data-d="-1">−</button>
            <input inputmode="decimal" value="${v ? fq(v).replace(".", ",") : ""}" placeholder="0">
            <button data-d="1">+</button></div><div class="unit">${esc(p.unit)}</div></div>`;
      }
      html += "</div>";
    }
    document.getElementById("groups").innerHTML = html || `<p class="muted center">Ничего не найдено</p>`;
    document.querySelectorAll("#groups .item").forEach(row => {
      const id = row.dataset.id, inp = row.querySelector("input");
      const set = val => {
        if (val && val > 0) S.draft[id] = Math.round(val * 1000) / 1000; else delete S.draft[id];
        row.classList.toggle("has", !!S.draft[id]); saveDraft(); updateSend();
      };
      inp.oninput = () => set(num(inp.value));
      inp.onblur = () => { inp.value = S.draft[id] ? fq(S.draft[id]) : ""; };
      row.querySelectorAll("button").forEach(b => b.onclick = () => {
        const next = Math.max(0, (S.draft[id] || 0) + Number(b.dataset.d));
        set(next); inp.value = next ? fq(next) : "";
      });
    });
  };
  const count = () => Object.keys(S.draft).filter(k => k !== "_note").length;
  const updateSend = () => {
    const n = count(), b = document.getElementById("send");
    b.disabled = !n; b.textContent = n ? `Отправить заявку (${n})` : "Выберите товары";
  };
  document.getElementById("q").oninput = draw;
  document.getElementById("note").oninput = e => { S.draft._note = e.target.value; saveDraft(); };
  document.getElementById("clear").onclick = () => {
    if (!count() || confirm("Очистить заявку?")) { S.draft = {}; saveDraft(); viewNew(); }
  };
  document.getElementById("send").onclick = () => run(async () => {
    const items = Object.entries(S.draft).filter(([k]) => k !== "_note").map(([k, v]) => ({ product_id: +k, qty: v }));
    if (!confirm(`Отправить заявку из ${items.length} позиций закупщику?`)) return;
    const r = await api("POST", "/api/orders", { items, note: S.draft._note || "" });
    S.draft = {}; saveDraft(); haptic("success");
    toast(`Заявка № ${r.number} отправлена`);
    S.stack = []; go("order", { id: r.id }, false);
  });
  draw(); updateSend();
}

// ---------- список заказов ----------
async function viewOrders() {
  $title.textContent = "Заказы";
  const rows = await api("GET", "/api/orders");
  if (!rows.length) { $view.innerHTML = `<div class="card center muted">Заказов пока нет</div>`; return; }
  $view.innerHTML = `<div class="list">${rows.map(o => `
    <div class="item" data-id="${o.id}" style="cursor:pointer">
      <div class="row"><b class="grow">№ ${o.number} · ${fd(o.created_at)}</b><span class="badge ${o.status}">${STATUS[o.status]}</span></div>
      <div class="row small muted"><span class="grow">${o.items_count} поз.${o.status === "new" && o.pending_count ? ` · не отмечено ${o.pending_count}` : ""}</span>
        <span>${o.total ? fm(o.total) + " " + esc(S.currency) : ""}</span></div>
    </div>`).join("")}</div>`;
  $view.querySelectorAll(".item").forEach(el => el.onclick = () => go("order", { id: +el.dataset.id }));
}

// ---------- карточка заказа / закупка ----------
async function viewOrder({ id }) {
  const o = await api("GET", "/api/orders/" + id);
  $title.textContent = `Заказ № ${o.number}`;
  const buying = canBuy() && o.status === "new";
  const total = o.items.length, done = total - o.pending_count;

  let h = `<div class="card">
    <div class="row"><div class="grow"><b>№ ${o.number}</b> от ${fd(o.created_at)}</div><span class="badge ${o.status}">${STATUS[o.status]}</span></div>
    <div class="small muted">Составил: ${esc(o.created_by_name || "—")}${o.completed_at ? ` · Закрыт ${fd(o.completed_at)}` : ""}</div>
    ${o.note ? `<div class="small" style="margin-top:6px">💬 ${esc(o.note)}</div>` : ""}
    <div class="progress"><i style="width:${total ? done / total * 100 : 0}%"></i></div>
    <div class="row"><span class="grow small muted">Отмечено ${done} из ${total}</span><span class="total" id="total">${fm(o.total)} ${esc(S.currency)}</span></div>
  </div>`;

  if (o.supplier_texts.length) {
    h += `<details class="card"${o.status === "new" && !done ? " open" : ""}><summary><b>📤 Тексты для поставщиков</b></summary>
      ${o.supplier_texts.map((t, i) => `<div style="margin-top:10px"><div class="row"><b class="grow">${esc(t.category)}</b>
        <button class="ghost" data-copy="${i}">Копировать</button></div><pre class="txt">${esc(t.text)}</pre></div>`).join("")}
      <div class="row" style="margin-top:8px"><button class="grow" id="copyall">Копировать всё</button>
        <button class="grow" id="sendtexts">Прислать в чат</button></div></details>`;
  }

  for (const [cat, items] of groupBy(o.items)) {
    const sub = items.reduce((s, i) => s + (i.sum || 0), 0);
    h += `<div class="group-title"><span>${esc(cat)}</span><span>${sub ? fm(sub) + " " + esc(S.currency) : ""}</span></div>`;
    h += items.map(it => buying ? buyCard(it) : readCard(it)).join("");
  }

  if (buying) {
    h += `<div class="card"><button class="block" id="addextra">＋ Добавить товар не из заявки</button></div>
      <div class="card"><label class="f">Кто принимает товар (ФИО для накладной)</label>
      <input id="receiver" value="${esc(o.created_by_name || "")}" placeholder="ФИО принимающего"></div>
      <div class="bar"><div><button class="ok block" id="complete" ${o.pending_count ? "disabled" : ""}>
        ${o.pending_count ? `Осталось отметить: ${o.pending_count}` : "📄 Сформировать накладную"}</button></div></div>`;
  } else if (o.status === "completed") {
    h += `<div class="card small">Принял: <b>${esc(o.receiver_name || "—")}</b></div>
      <div class="bar"><div class="row"><button class="primary grow" id="pdf">📄 Прислать PDF в чат</button>
      ${canBuy() ? `<button id="reopen">Изменить</button>` : ""}</div></div>`;
  }
  if (o.status === "new" && !done) h += `<div class="center"><button class="ghost danger" id="cancel">Отменить заказ</button></div>`;
  $view.innerHTML = h;

  // обработчики
  $view.querySelectorAll("[data-copy]").forEach(b => b.onclick = () => copyText(o.supplier_texts[+b.dataset.copy].text));
  const on = (sel, fn) => { const el = document.getElementById(sel); if (el) el.onclick = () => run(fn); };
  on("copyall", () => copyText(o.supplier_texts.map(t => t.text).join("\n\n")));
  on("sendtexts", async () => { await api("POST", `/api/orders/${id}/send_texts`); toast("Отправлено в чат с ботом — перешлите поставщикам"); });
  on("pdf", async () => { await api("POST", `/api/orders/${id}/send_pdf`); toast("PDF отправлен в чат с ботом"); });
  on("reopen", async () => { if (confirm("Открыть заказ для исправлений? После правок сформируйте накладную заново.")) { await api("POST", `/api/orders/${id}/reopen`); viewOrder({ id }); } });
  on("cancel", async () => { if (confirm("Отменить заказ?")) { await api("POST", `/api/orders/${id}/cancel`); toast("Заказ отменён"); back(); } });
  on("complete", async () => {
    const receiver = document.getElementById("receiver").value.trim();
    if (!confirm(`Завершить закупку на ${fm(o.total)} ${S.currency} и сформировать накладную?`)) return;
    await api("POST", `/api/orders/${id}/complete`, { receiver_name: receiver });
    haptic("success"); toast("Накладная отправлена в чат"); viewOrder({ id });
  });
  on("addextra", () => pickExtra(id));
  if (buying) bindBuyCards(id);
}

function groupBy(items) {
  const m = new Map();
  for (const it of items) { if (!m.has(it.category)) m.set(it.category, []); m.get(it.category).push(it); }
  return m;
}

function readCard(it) {
  const st = it.status === "bought" ? `${fq(it.qty_bought)} ${esc(it.unit)} × ${fm(it.price)} = <b>${fm(it.sum)}</b>`
    : it.status === "missing" ? "нет в наличии" : "ожидает";
  return `<div class="card buy ${it.status}"><div class="row"><span class="name grow">${it.status === "bought" ? "✅ " : ""}${esc(it.name)}${it.extra ? " <span class='badge'>доп.</span>" : ""}</span>
    <span class="small muted">${it.qty_ordered ? `заказ ${fq(it.qty_ordered)} ${esc(it.unit)}` : ""}</span></div>
    <div class="small">${st}</div></div>`;
}

function buyCard(it) {
  const e = S.edits[it.id] || {};
  const qty = e.qty ?? (it.qty_bought ?? it.qty_ordered ?? "");
  const price = e.price ?? (it.price ?? "");
  const head = `<div class="row"><span class="name grow">${it.status === "bought" ? "✅ " : it.status === "missing" ? "🚫 " : ""}${esc(it.name)}${it.extra ? " <span class='badge'>доп.</span>" : ""}</span>
    <span class="small muted">${it.qty_ordered ? `заказ: ${fq(it.qty_ordered)} ${esc(it.unit)}` : ""}</span></div>`;
  if (it.status !== "pending") {
    return `<div class="card buy ${it.status}" data-id="${it.id}">${head}
      <div class="row small"><span class="grow">${it.status === "bought" ? `${fq(it.qty_bought)} ${esc(it.unit)} × ${fm(it.price)} = <b>${fm(it.sum)} ${esc(S.currency)}</b>` : "Нет в наличии"}</span>
      <button class="ghost" data-act="undo">Изменить</button></div></div>`;
  }
  return `<div class="card buy" data-id="${it.id}">${head}
    <div class="grid">
      <div><label class="f">Куплено, ${esc(it.unit)}</label><input inputmode="decimal" data-f="qty" value="${esc(fq(num(qty)) === "—" ? "" : fq(num(qty)))}"></div>
      <div><label class="f">Цена за ${esc(it.unit)}, ${esc(S.currency)}</label><input inputmode="decimal" data-f="price" value="${esc(price === "" ? "" : fq(num(price)))}" placeholder="0"></div>
    </div>
    <div class="row"><span class="grow">Сумма: <span class="sum" data-sum>${calcSum(qty, price)}</span></span>
      ${it.extra ? `<button class="ghost danger" data-act="del">Удалить</button>` : `<button data-act="missing">Нет</button>`}
      <button class="ok" data-act="bought">✓ Куплено</button></div></div>`;
}
function calcSum(q, p) { const a = num(q), b = num(p); return a != null && b != null ? fm(a * b) + " " + esc(S.currency) : "—"; }

function bindBuyCards(orderId) {
  $view.querySelectorAll(".buy[data-id]").forEach(card => {
    const id = +card.dataset.id;
    card.querySelectorAll("input").forEach(inp => inp.oninput = () => {
      S.edits[id] = S.edits[id] || {};
      S.edits[id][inp.dataset.f] = inp.value;
      const q = card.querySelector('[data-f="qty"]').value, p = card.querySelector('[data-f="price"]').value;
      card.querySelector("[data-sum]").innerHTML = calcSum(q, p);
    });
    card.querySelectorAll("button[data-act]").forEach(b => b.onclick = () => run(async () => {
      const act = b.dataset.act;
      if (act === "bought") {
        const qty = num(card.querySelector('[data-f="qty"]').value), price = num(card.querySelector('[data-f="price"]').value);
        if (!qty || qty <= 0) throw new Error("Укажите, сколько куплено");
        if (price == null || price < 0) throw new Error("Укажите цену");
        await api("PUT", `/api/items/${id}`, { status: "bought", qty_bought: qty, price });
        delete S.edits[id]; haptic("success");
      } else if (act === "missing") {
        await api("PUT", `/api/items/${id}`, { status: "missing" });
      } else if (act === "undo") {
        await api("PUT", `/api/items/${id}`, { status: "pending" });
      } else if (act === "del") {
        if (!confirm("Удалить позицию?")) return;
        await api("DELETE", `/api/items/${id}`);
      }
      const y = window.scrollY; await viewOrder({ id: orderId }); window.scrollTo(0, y);
    }));
  });
}

async function pickExtra(orderId) {
  const cat = S.catalog || await loadCatalog();
  const opts = cat.categories.map(c => {
    const ps = cat.products.filter(p => p.category_id === c.id);
    return ps.length ? `<optgroup label="${esc(c.name)}">${ps.map(p => `<option value="${p.id}">${esc(p.name)} (${esc(p.unit)})</option>`).join("")}</optgroup>` : "";
  }).join("");
  const box = document.createElement("div");
  box.className = "card";
  box.innerHTML = `<label class="f">Товар</label><select id="exsel">${opts}</select>
    <div class="row" style="margin-top:8px"><button class="grow" id="excancel">Отмена</button><button class="primary grow" id="exok">Добавить</button></div>`;
  const btn = document.getElementById("addextra").parentElement;
  btn.replaceWith(box);
  document.getElementById("excancel").onclick = () => viewOrder({ id: orderId });
  document.getElementById("exok").onclick = () => run(async () => {
    await api("POST", `/api/orders/${orderId}/extra`, { product_id: +document.getElementById("exsel").value });
    const y = window.scrollY; await viewOrder({ id: orderId }); window.scrollTo(0, y);
  });
}

// ---------- настройки (владелец) ----------
async function viewSettings() {
  $title.textContent = "Настройки";
  $view.innerHTML = `<div class="list">
    <div class="item row" id="s-cat" style="cursor:pointer"><span class="grow">📦 Группы и товары</span><span class="muted">›</span></div>
    <div class="item row" id="s-users" style="cursor:pointer"><span class="grow">👥 Пользователи и роли</span><span class="muted">›</span></div>
  </div>
  <p class="small muted" style="margin:12px 4px">Чтобы добавить повара или закупщика: попросите его открыть бота и нажать «Старт». Он появится в списке пользователей — назначьте ему роль.</p>`;
  document.getElementById("s-cat").onclick = () => go("catalog");
  document.getElementById("s-users").onclick = () => go("users");
}

async function viewUsers() {
  $title.textContent = "Пользователи";
  const users = await api("GET", "/api/users");
  const names = { owner: "Владелец", buyer: "Закупщик", cook: "Повар", none: "Нет доступа" };
  $view.innerHTML = `<div class="list">${users.map(u => `<div class="item row">
    <div class="grow"><div>${esc(u.name || "—")}</div><div class="small muted">${u.username ? "@" + esc(u.username) + " · " : ""}ID ${u.tg_id}</div></div>
    <select data-id="${u.tg_id}" style="width:140px" ${u.tg_id === S.me.tg_id ? "disabled" : ""}>
      ${Object.entries(names).map(([k, v]) => `<option value="${k}" ${u.role === k ? "selected" : ""}>${v}</option>`).join("")}</select></div>`).join("")}</div>`;
  $view.querySelectorAll("select").forEach(s => s.onchange = () => run(async () => {
    await api("PUT", `/api/users/${s.dataset.id}/role`, { role: s.value }); toast("Роль сохранена");
  }));
}

async function viewCatalog() {
  $title.textContent = "Группы и товары";
  const cat = await loadCatalog();
  const unitOpts = sel => S.units.map(u => `<option ${u === sel ? "selected" : ""}>${esc(u)}</option>`).join("");
  let h = `<div class="card"><b>Новый товар</b>
    <div style="margin-top:8px"><input id="np-name" placeholder="Название, напр. Огурцы"></div>
    <div class="row" style="margin-top:8px">
      <select id="np-cat" class="grow">${cat.categories.map(c => `<option value="${c.id}">${esc(c.name)}</option>`).join("")}</select>
      <select id="np-unit" style="width:100px">${unitOpts("кг")}</select></div>
    <button class="primary block" style="margin-top:8px" id="np-add">Добавить товар</button></div>`;
  for (const c of cat.categories) {
    const ps = cat.products.filter(p => p.category_id === c.id);
    h += `<div class="group-title"><span>${esc(c.name)} · ${ps.length}</span>
      <span><button class="ghost" data-rc="${c.id}">Переим.</button>${ps.length ? "" : `<button class="ghost danger" data-dc="${c.id}">Удалить</button>`}</span></div>
      <div class="list">${ps.map(p => `<div class="item row"><span class="grow">${esc(p.name)}</span>
        <span class="small muted">${esc(p.unit)}</span><button class="ghost" data-ep="${p.id}">✎</button></div>`).join("") || `<div class="item small muted">Нет товаров</div>`}</div>`;
  }
  h += `<div class="card" style="margin-top:16px"><b>Новая группа</b><div class="row" style="margin-top:8px">
    <input id="nc-name" class="grow" placeholder="Напр. Рыба"><button class="primary" id="nc-add">Добавить</button></div></div>`;
  $view.innerHTML = h;
  const remember = () => { S.lastCat = document.getElementById("np-cat").value; S.lastUnit = document.getElementById("np-unit").value; };
  if (S.lastCat) document.getElementById("np-cat").value = S.lastCat;
  if (S.lastUnit) document.getElementById("np-unit").value = S.lastUnit;

  document.getElementById("np-add").onclick = () => run(async () => {
    const name = document.getElementById("np-name").value.trim();
    if (!name) throw new Error("Введите название");
    remember();
    await api("POST", "/api/products", { name, category_id: +document.getElementById("np-cat").value, unit: document.getElementById("np-unit").value });
    toast("Добавлено: " + name); await viewCatalog(); document.getElementById("np-name").focus();
  });
  document.getElementById("nc-add").onclick = () => run(async () => {
    const name = document.getElementById("nc-name").value.trim();
    if (!name) throw new Error("Введите название группы");
    await api("POST", "/api/categories", { name }); viewCatalog();
  });
  $view.querySelectorAll("[data-rc]").forEach(b => b.onclick = () => run(async () => {
    const c = cat.categories.find(x => x.id === +b.dataset.rc);
    const name = prompt("Новое название группы", c.name);
    if (name && name.trim()) { await api("PUT", `/api/categories/${c.id}`, { name: name.trim() }); viewCatalog(); }
  }));
  $view.querySelectorAll("[data-dc]").forEach(b => b.onclick = () => run(async () => {
    if (confirm("Удалить пустую группу?")) { await api("DELETE", `/api/categories/${b.dataset.dc}`); viewCatalog(); }
  }));
  $view.querySelectorAll("[data-ep]").forEach(b => b.onclick = () => {
    const p = cat.products.find(x => x.id === +b.dataset.ep);
    const row = b.parentElement;
    row.classList.remove("row");
    row.innerHTML = `<input data-f="name" value="${esc(p.name)}">
      <div class="row" style="margin-top:6px"><select data-f="cat" class="grow">${cat.categories.map(c => `<option value="${c.id}" ${c.id === p.category_id ? "selected" : ""}>${esc(c.name)}</option>`).join("")}</select>
      <select data-f="unit" style="width:90px">${unitOpts(p.unit)}</select></div>
      <div class="row" style="margin-top:6px"><button class="ghost danger" data-a="del">Удалить</button><span class="grow"></span>
      <button data-a="cancel">Отмена</button><button class="primary" data-a="save">Сохранить</button></div>`;
    const v = f => row.querySelector(`[data-f="${f}"]`).value;
    row.querySelector('[data-a="cancel"]').onclick = () => viewCatalog();
    row.querySelector('[data-a="save"]').onclick = () => run(async () => {
      await api("PUT", `/api/products/${p.id}`, { name: v("name").trim(), category_id: +v("cat"), unit: v("unit") }); viewCatalog();
    });
    row.querySelector('[data-a="del"]').onclick = () => run(async () => {
      if (confirm(`Удалить «${p.name}» из справочника? В истории заказов он останется.`)) { await api("DELETE", `/api/products/${p.id}`); viewCatalog(); }
    });
  });
}

// ---------- старт ----------
(async function init() {
  try {
    const r = await api("GET", "/api/me");
    S.me = r.user; S.currency = r.currency; S.units = r.units;
  } catch (e) {
    $view.innerHTML = `<div class="card center">${esc(e.message)}</div>`; return;
  }
  if (role() === "none") {
    $view.innerHTML = `<div class="card center">У вас пока нет доступа.<br><span class="muted small">Ваш ID: ${S.me.tg_id}. Владелец назначит вам роль.</span></div>`;
    return;
  }
  const qs = new URLSearchParams(location.search);
  const startOrder = qs.get("order") || (tg && tg.initDataUnsafe && tg.initDataUnsafe.start_param || "").replace("order", "");
  if (startOrder && +startOrder) { S.view = { view: canOrder() && role() === "cook" ? "new" : "orders", params: {} }; go("order", { id: +startOrder }); return; }
  go(role() === "cook" ? "new" : "orders", {}, false);
})();
