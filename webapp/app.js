// --- Совместимость с обычным браузером (если открыть index.html не через Telegram) ---
// Важно: скрипт telegram-web-app.js создаёт window.Telegram.WebApp даже вне самого
// Telegram, но его MainButton — нативный элемент, который рисует только настоящий
// клиент Telegram. Поэтому определяем «мы правда внутри Telegram» по непустому
// initData, а не по факту существования объекта — иначе в обычном браузере кнопка
// подтверждения будет невидимой.
const isRealTelegram = !!(window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.initData);
const tg = isRealTelegram ? window.Telegram.WebApp : {
  ready: () => {},
  expand: () => {},
  close: () => alert("В демо-режиме (вне Telegram) закрытие недоступно"),
  MainButton: {
    text: "",
    show() { this._el && (this._el.style.display = "block"); this._render(); },
    hide() { this._el && (this._el.style.display = "none"); },
    setText(t) { this.text = t; this._render(); },
    onClick(cb) { this._cb = cb; this._render(); },
    _render() {
      if (!this._el) {
        this._el = document.createElement("button");
        this._el.className = "mainbutton-fallback";
        document.body.appendChild(this._el);
        this._el.addEventListener("click", () => this._cb && this._cb());
      }
      this._el.textContent = this.text;
    },
  },
  initDataUnsafe: {},
};

tg.ready();
tg.expand();

const API_BASE = "";

// Какой бизнес обслуживать — приходит в URL мини-аппы как ?business_id=...
// (см. backend/bot.py: именно так строится ссылка в кнопке «Записаться» под каждый бот).
// Если параметра нет (например, зашли на сервер напрямую в браузере) — бэкенд сам
// подставит бизнес по умолчанию (см. resolve_business в server.py).
const urlParams = new URLSearchParams(location.search);
const businessIdParam = urlParams.get("business_id");

const state = {
  businessId: businessIdParam ? Number(businessIdParam) : null,
  // Сырая подписанная строка Telegram WebApp — сервер проверяет её HMAC-подписью
  // конкретного бота (см. backend/telegram_auth.py), чтобы доверять owner-запросам.
  // Вне настоящего Telegram её взять неоткуда — тогда админ-запросы будут отклонены,
  // если только на сервере явно не включён DEV_SKIP_INITDATA_CHECK для локальной отладки.
  initData: isRealTelegram ? tg.initData : "",
  config: { business_name: "Запись онлайн", owner_tg_id: 0 },
  isOwner: false,
  myTgId: null,

  services: [],
  dates: [],
  selectedService: null,
  selectedDate: null,
  selectedTime: null,
  quantity: 1,
  comment: "",

  admin: {
    services: [],
    orders: [],
    statusFilter: "",
    editingServiceId: null,
  },
};

const el = (id) => document.getElementById(id);

const screens = {
  services: el("screen-services"),
  dates: el("screen-dates"),
  slots: el("screen-slots"),
  details: el("screen-details"),
  done: el("screen-done"),
};

const adminScreens = {
  orders: el("admin-screen-orders"),
  services: el("admin-screen-services"),
  "service-form": el("admin-screen-service-form"),
  theme: el("admin-screen-theme"),
  schedule: el("admin-screen-schedule"),
};

function showScreen(name) {
  Object.values(screens).forEach((s) => s.classList.add("hidden"));
  screens[name].classList.remove("hidden");
}

function showAdminScreen(name) {
  Object.values(adminScreens).forEach((s) => s.classList.add("hidden"));
  adminScreens[name].classList.remove("hidden");
}

async function api(path, options = {}) {
  const res = await fetch(API_BASE + path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Ошибка сервера" }));
    throw new Error(err.detail || "Ошибка сервера");
  }
  return res.json();
}

// Собирает query-строку, пропуская пустые/отсутствующие значения.
function qs(params) {
  const usp = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== null && v !== undefined && v !== "") usp.set(k, v);
  });
  return usp.toString();
}

function formatDateLabel(isoDate) {
  const d = new Date(isoDate + "T00:00:00");
  const weekdays = ["ВС", "ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ"];
  const dayMonth = d.toLocaleDateString("ru-RU", { day: "numeric", month: "short" });
  return { top: dayMonth, weekday: weekdays[d.getDay()] };
}

// ======================================================================
// ОФОРМЛЕНИЕ (тема) — применяется ко всему приложению через CSS-переменные
// ======================================================================

const THEME_VAR_MAP = {
  bg_color: "--bg",
  surface_color: "--surface",
  text_color: "--text",
  hint_color: "--hint",
  primary_color: "--primary",
  primary_text_color: "--primary-text",
  danger_color: "--danger",
  success_color: "--success",
};

function applyTheme(theme) {
  if (!theme) return;
  const root = document.documentElement.style;
  Object.entries(THEME_VAR_MAP).forEach(([key, cssVar]) => {
    if (theme[key]) root.setProperty(cssVar, theme[key]);
  });
  if (theme.radius !== undefined && theme.radius !== null) {
    root.setProperty("--radius", `${theme.radius}px`);
  }
}

function applyBrand(businessName) {
  const name = businessName || "Онлайн-запись";
  el("brand-name").textContent = name;
  el("brand-mark").textContent = name.trim().charAt(0).toUpperCase() || "З";
}

// ======================================================================
// РЕЖИМ: переключатель Клиент / Админ
// ======================================================================

function setupModeSwitch() {
  if (!state.isOwner) return;
  el("mode-switch").classList.remove("hidden");
  el("tab-client").addEventListener("click", () => switchMode("client"));
  el("tab-admin").addEventListener("click", () => switchMode("admin"));
}

function switchMode(mode) {
  const isClient = mode === "client";
  el("client-mode").classList.toggle("hidden", !isClient);
  el("admin-mode").classList.toggle("hidden", isClient);
  el("tab-client").classList.toggle("active", isClient);
  el("tab-admin").classList.toggle("active", !isClient);

  applyTheme(state.config.theme); // сбрасываем несохранённое превью оформления, если было

  if (isClient) {
    tg.MainButton.hide();
  } else {
    tg.MainButton.hide();
    loadAdminOrders();
  }
}

// ======================================================================
// КЛИЕНТСКИЙ РЕЖИМ
// ======================================================================

async function loadServices() {
  state.services = await api(`/api/services?${qs({ business_id: state.businessId })}`);
  el("business-title").textContent = state.config.business_name || "Выберите услугу";
  const list = el("services-list");
  list.innerHTML = "";
  state.services.forEach((service) => {
    const card = document.createElement("div");
    card.className = "card";
    const meta = service.type === "slot" ? `${service.duration_min} мин` : "В наличии";
    card.innerHTML = `
      <div>
        <div class="title">${escapeHtml(service.name)}</div>
        <div class="meta">${meta}</div>
      </div>
      <span class="price-tag">${service.price} ₽</span>
    `;
    card.addEventListener("click", () => selectService(service));
    list.appendChild(card);
  });
}

function selectService(service) {
  state.selectedService = service;
  state.quantity = 1;
  state.comment = "";
  if (service.type === "slot") {
    loadDates();
    showScreen("dates");
  } else {
    showDetailsScreen();
    showScreen("details");
  }
}

async function loadDates() {
  state.dates = await api(`/api/dates?${qs({ business_id: state.businessId })}`);
  const list = el("dates-list");
  list.innerHTML = "";
  state.dates.forEach((date) => {
    const { top, weekday } = formatDateLabel(date);
    const pill = document.createElement("div");
    pill.className = "pill";
    pill.innerHTML = `<span class="weekday">${weekday}</span>${top}`;
    pill.addEventListener("click", () => selectDate(date, pill));
    list.appendChild(pill);
  });
}

function selectDate(date, pillEl) {
  document.querySelectorAll("#dates-list .pill").forEach((p) => p.classList.remove("selected"));
  pillEl.classList.add("selected");
  state.selectedDate = date;
  loadSlots();
  showScreen("slots");
}

async function loadSlots() {
  const slots = await api(`/api/slots?${qs({
    service_id: state.selectedService.id,
    date: state.selectedDate,
    business_id: state.businessId,
  })}`);
  const list = el("slots-list");
  const noSlotsMsg = el("no-slots-msg");
  list.innerHTML = "";

  if (slots.length === 0) {
    noSlotsMsg.classList.remove("hidden");
    return;
  }
  noSlotsMsg.classList.add("hidden");

  slots.forEach((time) => {
    const slotEl = document.createElement("div");
    slotEl.className = "slot";
    slotEl.textContent = time;
    slotEl.addEventListener("click", () => selectTime(time, slotEl));
    list.appendChild(slotEl);
  });
}

function selectTime(time, slotEl) {
  document.querySelectorAll("#slots-list .slot").forEach((s) => s.classList.remove("selected"));
  slotEl.classList.add("selected");
  state.selectedTime = time;
  showDetailsScreen();
  showScreen("details");
}

function showDetailsScreen() {
  const s = state.selectedService;
  const isOrder = s.type === "order";

  el("quantity-block").classList.toggle("hidden", !isOrder);
  el("qty-value").textContent = state.quantity;
  el("comment-input").value = state.comment;

  renderOrderSummary();

  const backBtn = document.querySelector("#screen-details [data-back-dynamic]");
  backBtn.textContent = isOrder ? "← Назад" : "← Назад";
  backBtn.onclick = () => {
    if (isOrder) {
      showScreen("services");
    } else {
      showScreen("slots");
    }
  };

  tg.MainButton.setText("Подтвердить заявку");
  tg.MainButton.show();
  tg.MainButton.onClick(submitBooking);
}

function renderOrderSummary() {
  const s = state.selectedService;
  let rows = `<div class="row"><span class="label">Позиция</span><span>${escapeHtml(s.name)}</span></div>`;
  if (s.type === "slot") {
    const { top } = formatDateLabel(state.selectedDate);
    rows += `<div class="row"><span class="label">Дата</span><span>${top}</span></div>`;
    rows += `<div class="row"><span class="label">Время</span><span>${state.selectedTime}</span></div>`;
  }
  const total = s.type === "order" ? s.price * state.quantity : s.price;
  rows += `<div class="row"><span class="label">Цена</span><span>${total} ₽</span></div>`;
  el("order-summary").innerHTML = rows;
}

el("qty-minus").addEventListener("click", () => {
  state.quantity = Math.max(1, state.quantity - 1);
  el("qty-value").textContent = state.quantity;
  renderOrderSummary();
});
el("qty-plus").addEventListener("click", () => {
  state.quantity += 1;
  el("qty-value").textContent = state.quantity;
  renderOrderSummary();
});
el("comment-input").addEventListener("input", (e) => {
  state.comment = e.target.value;
});

async function submitBooking() {
  const user = tg.initDataUnsafe && tg.initDataUnsafe.user;
  const s = state.selectedService;
  try {
    const result = await api("/api/book", {
      method: "POST",
      body: JSON.stringify({
        business_id: state.businessId,
        service_id: s.id,
        date: s.type === "slot" ? state.selectedDate : null,
        time: s.type === "slot" ? state.selectedTime : null,
        quantity: state.quantity,
        comment: state.comment || null,
        client_name: user ? `${user.first_name || ""} ${user.last_name || ""}`.trim() : "Гость",
        init_data: state.initData,
        client_tg_id: user ? user.id : null,
      }),
    });

    let rows = `<div class="row"><span class="label">Позиция</span><span>${escapeHtml(result.service_name)}</span></div>`;
    if (result.date) {
      rows += `<div class="row"><span class="label">Дата</span><span>${result.date}</span></div>`;
      rows += `<div class="row"><span class="label">Время</span><span>${result.time}</span></div>`;
    }
    if (result.quantity > 1) {
      rows += `<div class="row"><span class="label">Количество</span><span>${result.quantity}</span></div>`;
    }
    rows += `<div class="row"><span class="label">Цена</span><span>${result.price * result.quantity} ₽</span></div>`;
    el("done-summary").innerHTML = rows;

    tg.MainButton.hide();
    showScreen("done");
  } catch (e) {
    alert(e.message);
    if (state.selectedService.type === "slot") {
      loadSlots();
      showScreen("slots");
    }
  }
}

// Кнопки "назад" со статичной целью (data-back="...")
document.querySelectorAll("[data-back]").forEach((btn) => {
  btn.addEventListener("click", () => showScreen(btn.dataset.back));
});

// ======================================================================
// АДМИН-РЕЖИМ: заявки
// ======================================================================

el("admin-tab-orders").addEventListener("click", () => {
  applyTheme(state.config.theme); // сбрасываем несохранённое превью оформления, если было
  setActiveAdminTab("admin-tab-orders");
  showAdminScreen("orders");
  loadAdminOrders();
});

el("admin-tab-services").addEventListener("click", () => {
  applyTheme(state.config.theme); // сбрасываем несохранённое превью оформления, если было
  setActiveAdminTab("admin-tab-services");
  showAdminScreen("services");
  loadAdminServices();
});

el("admin-tab-theme").addEventListener("click", () => {
  setActiveAdminTab("admin-tab-theme");
  showAdminScreen("theme");
  openThemeForm(state.config.theme);
});

el("admin-tab-schedule").addEventListener("click", () => {
  applyTheme(state.config.theme); // сбрасываем несохранённое превью оформления, если было
  setActiveAdminTab("admin-tab-schedule");
  showAdminScreen("schedule");
  loadSchedule();
});

function setActiveAdminTab(activeId) {
  ["admin-tab-orders", "admin-tab-services", "admin-tab-theme", "admin-tab-schedule"].forEach((id) => {
    el(id).classList.toggle("active", id === activeId);
  });
}

document.querySelectorAll(".filter-pill").forEach((pill) => {
  pill.addEventListener("click", () => {
    document.querySelectorAll(".filter-pill").forEach((p) => p.classList.remove("active"));
    pill.classList.add("active");
    state.admin.statusFilter = pill.dataset.status;
    loadAdminOrders();
  });
});

const statusLabels = { new: "Новая", confirmed: "Подтверждена", done: "Выполнена", cancelled: "Отменена" };

async function loadAdminOrders() {
  const orders = await api(`/api/admin/bookings?${qs({
    init_data: state.initData,
    owner_tg_id: state.myTgId,
    business_id: state.businessId,
    status: state.admin.statusFilter,
  })}`);
  state.admin.orders = orders;
  const list = el("orders-list");
  const noOrdersMsg = el("no-orders-msg");
  list.innerHTML = "";

  if (orders.length === 0) {
    noOrdersMsg.classList.remove("hidden");
    return;
  }
  noOrdersMsg.classList.add("hidden");

  orders.forEach((order) => {
    const card = document.createElement("div");
    card.className = "order-card";
    const whenText = order.date
      ? `${order.date} в ${order.time}`
      : "разовый заказ";
    const qtyText = order.quantity > 1 ? ` × ${order.quantity}` : "";

    card.innerHTML = `
      <div class="order-top">
        <div>
          <div class="order-title">${escapeHtml(order.service_name)}${qtyText}</div>
          <div class="order-meta">${escapeHtml(order.client_name) || "Без имени"} · ${whenText}</div>
          <div class="order-meta">${order.price * order.quantity} ₽</div>
        </div>
        <span class="status-badge status-${order.status}">${statusLabels[order.status] || order.status}</span>
      </div>
      ${order.comment ? `<div class="order-comment">💬 ${escapeHtml(order.comment)}</div>` : ""}
      <div class="order-actions">
        ${order.status === "new" ? `<button class="btn-confirm" data-id="${order.id}" data-action="confirmed">Подтвердить</button>` : ""}
        <button class="btn-done" data-id="${order.id}" data-action="done">Выполнено</button>
        <button class="btn-cancel" data-id="${order.id}" data-action="cancelled">Отменить</button>
      </div>
    `;
    list.appendChild(card);
  });

  list.querySelectorAll("[data-action]").forEach((btn) => {
    btn.addEventListener("click", () => updateOrderStatus(btn.dataset.id, btn.dataset.action));
  });
}

function escapeHtml(str) {
  const d = document.createElement("div");
  d.textContent = str;
  return d.innerHTML;
}

async function updateOrderStatus(id, status) {
  await api(`/api/admin/bookings/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ status, init_data: state.initData, owner_tg_id: state.myTgId, business_id: state.businessId }),
  });
  loadAdminOrders();
}

// ======================================================================
// АДМИН-РЕЖИМ: позиции (услуги/товары)
// ======================================================================

async function loadAdminServices() {
  state.admin.services = await api(`/api/admin/services?${qs({
    init_data: state.initData,
    owner_tg_id: state.myTgId,
    business_id: state.businessId,
  })}`);
  const list = el("admin-services-list");
  list.innerHTML = "";

  state.admin.services.forEach((service) => {
    const card = document.createElement("div");
    card.className = "service-admin-card" + (service.is_active ? "" : " inactive");
    const meta = service.type === "slot" ? `${service.duration_min} мин · по расписанию` : "разовый заказ";
    card.innerHTML = `
      <div>
        <div class="title">${escapeHtml(service.name)} — ${service.price} ₽</div>
        <div class="badge-type">${meta}${service.is_active ? "" : " · скрыта"}</div>
      </div>
      <span>✎</span>
    `;
    card.addEventListener("click", () => openServiceForm(service));
    list.appendChild(card);
  });
}

el("add-service-btn").addEventListener("click", () => openServiceForm(null));

function openServiceForm(service) {
  state.admin.editingServiceId = service ? service.id : null;
  el("service-form-title").textContent = service ? "Редактировать позицию" : "Новая позиция";
  el("service-name").value = service ? service.name : "";
  el("service-price").value = service ? service.price : "";
  el("service-duration").value = service ? service.duration_min : "";
  setServiceType(service ? service.type : "slot");
  el("delete-service-btn").classList.toggle("hidden", !service);
  showAdminScreen("service-form");
}

function setServiceType(type) {
  el("type-slot").classList.toggle("active", type === "slot");
  el("type-order").classList.toggle("active", type === "order");
  el("duration-block").classList.toggle("hidden", type === "order");
  el("service-form-title").dataset.type = type;
}

el("type-slot").addEventListener("click", () => setServiceType("slot"));
el("type-order").addEventListener("click", () => setServiceType("order"));

el("save-service-btn").addEventListener("click", async () => {
  const name = el("service-name").value.trim();
  const price = parseInt(el("service-price").value, 10);
  const type = el("service-form-title").dataset.type || "slot";
  const duration = type === "slot" ? parseInt(el("service-duration").value || "0", 10) : 0;

  if (!name || isNaN(price) || price < 0) {
    alert("Заполни название и укажи цену не меньше нуля");
    return;
  }
  if (type === "slot" && (isNaN(duration) || duration <= 0)) {
    alert("Укажи длительность в минутах для позиции по расписанию");
    return;
  }

  const payload = {
    name, price, duration_min: duration, type,
    is_active: true, init_data: state.initData, owner_tg_id: state.myTgId, business_id: state.businessId,
  };

  try {
    if (state.admin.editingServiceId) {
      await api(`/api/admin/services/${state.admin.editingServiceId}`, {
        method: "PUT",
        body: JSON.stringify(payload),
      });
    } else {
      await api(`/api/admin/services`, {
        method: "POST",
        body: JSON.stringify(payload),
      });
    }
    showAdminScreen("services");
    loadAdminServices();
  } catch (e) {
    alert(e.message);
  }
});

el("delete-service-btn").addEventListener("click", async () => {
  if (!state.admin.editingServiceId) return;
  if (!confirm("Удалить эту позицию? Это действие необратимо.")) return;
  await api(`/api/admin/services/${state.admin.editingServiceId}?${qs({
    init_data: state.initData,
    owner_tg_id: state.myTgId,
    business_id: state.businessId,
  })}`, {
    method: "DELETE",
  });
  showAdminScreen("services");
  loadAdminServices();
});

// ======================================================================
// АДМИН-РЕЖИМ: оформление
// ======================================================================

const THEME_PRESETS = [
  {
    name: "Фуд-сервис", bg_color: "#FAFAFA", surface_color: "#FFFFFF", text_color: "#1A1A1A",
    hint_color: "#767676", primary_color: "#E1251B", primary_text_color: "#FFFFFF",
    danger_color: "#D92D20", success_color: "#12805C", radius: 16,
  },
  {
    name: "Океан", bg_color: "#F3F7FA", surface_color: "#FFFFFF", text_color: "#0F1E2E",
    hint_color: "#6B7A88", primary_color: "#0A6CFF", primary_text_color: "#FFFFFF",
    danger_color: "#E5484D", success_color: "#18794E", radius: 14,
  },
  {
    name: "Природа", bg_color: "#F5F8F3", surface_color: "#FFFFFF", text_color: "#1B2A1A",
    hint_color: "#6E7C6B", primary_color: "#2F9E44", primary_text_color: "#FFFFFF",
    danger_color: "#D9480F", success_color: "#2B8A3E", radius: 20,
  },
  {
    name: "Премиум", bg_color: "#121212", surface_color: "#1E1E1E", text_color: "#F5F5F5",
    hint_color: "#9A9A9A", primary_color: "#D4AF37", primary_text_color: "#121212",
    danger_color: "#FF6B6B", success_color: "#4CAF7D", radius: 10,
  },
  {
    name: "Минимал", bg_color: "#FFFFFF", surface_color: "#F5F5F5", text_color: "#111111",
    hint_color: "#8A8A8A", primary_color: "#111111", primary_text_color: "#FFFFFF",
    danger_color: "#C0392B", success_color: "#1E824C", radius: 4,
  },
];

function renderThemePresets() {
  const row = el("theme-presets");
  row.innerHTML = "";
  THEME_PRESETS.forEach((preset) => {
    const btn = document.createElement("button");
    btn.className = "preset-swatch";
    btn.innerHTML = `<span class="dot" style="background:${preset.primary_color}"></span><span>${preset.name}</span>`;
    btn.addEventListener("click", () => {
      fillThemeForm(preset);
      previewTheme();
    });
    row.appendChild(btn);
  });
}

function fillThemeForm(theme) {
  Object.keys(THEME_VAR_MAP).forEach((key) => {
    el(`theme-${key}`).value = theme[key];
  });
  el("theme-radius").value = theme.radius;
  el("radius-value").textContent = theme.radius;
}

function readThemeForm() {
  const theme = { init_data: state.initData, owner_tg_id: state.myTgId, business_id: state.businessId };
  Object.keys(THEME_VAR_MAP).forEach((key) => {
    theme[key] = el(`theme-${key}`).value;
  });
  theme.radius = parseInt(el("theme-radius").value, 10);
  return theme;
}

function previewTheme() {
  applyTheme(readThemeForm());
}

function openThemeForm(theme) {
  renderThemePresets();
  fillThemeForm(theme || state.config.theme);
}

document.querySelectorAll('.theme-field-grid input[type="color"]').forEach((input) => {
  input.addEventListener("input", previewTheme);
});

el("theme-radius").addEventListener("input", () => {
  el("radius-value").textContent = el("theme-radius").value;
  previewTheme();
});

el("save-theme-btn").addEventListener("click", async () => {
  try {
    const payload = readThemeForm();
    const result = await api("/api/admin/theme", { method: "PUT", body: JSON.stringify(payload) });
    state.config.theme = result.theme;
    applyTheme(result.theme);
    el("save-theme-btn").textContent = "Сохранено ✓";
    setTimeout(() => { el("save-theme-btn").textContent = "Сохранить оформление"; }, 1500);
  } catch (e) {
    alert(e.message);
  }
});

el("reset-theme-btn").addEventListener("click", () => {
  fillThemeForm(THEME_PRESETS[0]);
  previewTheme();
});

// ======================================================================
// АДМИН-РЕЖИМ: расписание
// ======================================================================

async function loadSchedule() {
  const schedule = await api(`/api/admin/schedule?${qs({
    init_data: state.initData,
    owner_tg_id: state.myTgId,
    business_id: state.businessId,
  })}`);
  el("schedule-timezone").value = schedule.timezone;
  el("schedule-start").value = schedule.work_start_hour;
  el("schedule-end").value = schedule.work_end_hour;
  el("schedule-step").value = schedule.slot_step_minutes;
  el("schedule-days").value = schedule.days_ahead;
}

el("save-schedule-btn").addEventListener("click", async () => {
  const payload = {
    business_id: state.businessId,
    init_data: state.initData,
    owner_tg_id: state.myTgId,
    timezone: el("schedule-timezone").value,
    work_start_hour: parseInt(el("schedule-start").value, 10),
    work_end_hour: parseInt(el("schedule-end").value, 10),
    slot_step_minutes: parseInt(el("schedule-step").value, 10),
    days_ahead: parseInt(el("schedule-days").value, 10),
  };

  if (payload.work_end_hour <= payload.work_start_hour) {
    alert("Время закрытия должно быть позже времени открытия");
    return;
  }

  try {
    await api("/api/admin/schedule", { method: "PUT", body: JSON.stringify(payload) });
    el("save-schedule-btn").textContent = "Сохранено ✓";
    setTimeout(() => { el("save-schedule-btn").textContent = "Сохранить расписание"; }, 1500);
  } catch (e) {
    alert(e.message);
  }
});

// ======================================================================
// СТАРТ
// ======================================================================

async function init() {
  state.config = await api(`/api/config?${qs({ business_id: state.businessId })}`);
  state.businessId = state.config.business_id; // синхронизируем с тем, что реально отдал бэкенд (если в URL параметра не было)
  applyTheme(state.config.theme);
  applyBrand(state.config.business_name);

  const user = tg.initDataUnsafe && tg.initDataUnsafe.user;
  state.myTgId = user ? user.id : null;
  state.isOwner = !!(state.myTgId && state.config.owner_tg_id && state.myTgId === state.config.owner_tg_id);

  setupModeSwitch();
  await loadServices();
  showScreen("services");
}

init();
