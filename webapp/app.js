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
  masters: [],
  selectedMaster: null,
  selectedDate: null,
  selectedTime: null,
  quantity: 1,
  comment: "",

  admin: {
    services: [],
    orders: [],
    statusFilter: "",
    editingServiceId: null,
    masters: [],
    editingMasterId: null,
  },
};

const el = (id) => document.getElementById(id);

const screens = {
  services: el("screen-services"),
  my: el("screen-my"),
  masters: el("screen-masters"),
  dates: el("screen-dates"),
  slots: el("screen-slots"),
  details: el("screen-details"),
  done: el("screen-done"),
};

const adminScreens = {
  orders: el("admin-screen-orders"),
  services: el("admin-screen-services"),
  "service-form": el("admin-screen-service-form"),
  masters: el("admin-screen-masters"),
  "master-form": el("admin-screen-master-form"),
  settings: el("admin-screen-settings"),
  wizard: el("admin-screen-wizard"),
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

// URL картинок строит сервер (/api/media/<id>); перед вставкой в HTML/CSS всё равно проверяем формат.
const safeMediaUrl = (url) => (typeof url === "string" && /^\/api\/media\/\d+$/.test(url) ? url : null);

// localStorage может быть недоступен (приватный режим и т.п.) — всё в try/catch.
function storageGet(key) {
  try { return localStorage.getItem(key) || ""; } catch (e) { return ""; }
}
function storageSet(key, value) {
  try { localStorage.setItem(key, value); } catch (e) { /* не критично */ }
}

// Уменьшаем картинку в браузере до загрузки (и заодно убираем EXIF/геометки — canvas их не переносит).
async function fileToDataUrl(file, { maxSide, quality = 0.82, png = false }) {
  if (!file || !file.type.startsWith("image/")) throw new Error("Выберите файл-картинку");
  let bitmap;
  try {
    bitmap = await createImageBitmap(file);
  } catch (e) {
    throw new Error("Не удалось открыть картинку (подойдут JPEG, PNG, WebP)");
  }
  const scale = Math.min(1, maxSide / Math.max(bitmap.width, bitmap.height));
  const w = Math.max(1, Math.round(bitmap.width * scale));
  const h = Math.max(1, Math.round(bitmap.height * scale));
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d");
  if (!png) {
    ctx.fillStyle = "#ffffff"; // у JPEG нет прозрачности
    ctx.fillRect(0, 0, w, h);
  }
  ctx.drawImage(bitmap, 0, 0, w, h);
  if (bitmap.close) bitmap.close();

  const LIMIT = 900000; // символов data URL; сервер принимает до ~1 000 000
  let q = quality;
  let url = png ? canvas.toDataURL("image/png") : canvas.toDataURL("image/jpeg", q);
  while (!png && url.length > LIMIT && q > 0.4) {
    q -= 0.1;
    url = canvas.toDataURL("image/jpeg", q);
  }
  if (url.length > LIMIT) throw new Error("Картинка слишком большая, выберите поменьше");
  return url;
}

async function uploadImage(file, opts) {
  const dataUrl = await fileToDataUrl(file, opts);
  return api("/api/admin/media", { method: "POST", body: JSON.stringify({ ...adminAuth(), data_url: dataUrl }) });
}

// Общий блок «загрузить / убрать картинку»: id элементов — <prefix>-btn / -file / -remove / -preview.
function showPicked(prefix, image) {
  const preview = el(`${prefix}-preview`);
  const url = image ? safeMediaUrl(image.url) : null;
  if (url) {
    preview.src = url;
    preview.classList.remove("hidden");
  } else {
    preview.classList.add("hidden");
    preview.removeAttribute("src");
  }
  el(`${prefix}-remove`).classList.toggle("hidden", !url);
}

function bindPhotoPicker(prefix, opts, onPick, onClear) {
  const btn = el(`${prefix}-btn`);
  btn.addEventListener("click", () => el(`${prefix}-file`).click());
  el(`${prefix}-file`).addEventListener("change", async (e) => {
    const file = e.target.files[0];
    e.target.value = "";
    if (!file) return;
    const label = btn.textContent;
    btn.textContent = "Загружаю…";
    btn.disabled = true;
    try {
      const uploaded = await uploadImage(file, opts);
      const image = { id: uploaded.id, url: uploaded.url };
      await onPick(image);
      showPicked(prefix, image);
    } catch (err) {
      alert(err.message);
    } finally {
      btn.textContent = label;
      btn.disabled = false;
    }
  });
  el(`${prefix}-remove`).addEventListener("click", async () => {
    try {
      await onClear();
      showPicked(prefix, null);
    } catch (err) {
      alert(err.message);
    }
  });
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

// Фон страницы: цвет (--bg на body), градиент или картинка с «вуалью» цвета фона поверх.
function buildPageBackground(theme) {
  if (theme.bg_mode === "gradient" && theme.bg_color2) {
    return `linear-gradient(${theme.bg_angle ?? 160}deg, ${theme.bg_color}, ${theme.bg_color2})`;
  }
  const image = theme.bg_mode === "image" ? safeMediaUrl(theme.bg_image_url) : null;
  if (image) {
    const veil = `color-mix(in srgb, var(--bg) ${theme.bg_overlay ?? 60}%, transparent)`;
    return `linear-gradient(${veil}, ${veil}), url("${image}") center / cover no-repeat`;
  }
  return "none";
}

function applyTheme(theme) {
  if (!theme) return;
  const root = document.documentElement;
  Object.entries(THEME_VAR_MAP).forEach(([key, cssVar]) => {
    if (theme[key]) root.style.setProperty(cssVar, theme[key]);
  });
  if (theme.radius !== undefined && theme.radius !== null) {
    root.style.setProperty("--radius", `${theme.radius}px`);
  }
  root.style.setProperty(
    "--primary-fill",
    theme.primary_color2 ? `linear-gradient(135deg, ${theme.primary_color}, ${theme.primary_color2})` : "var(--primary)",
  );
  root.dataset.cards = theme.card_style || "shadow";
  root.dataset.font = theme.font || "sans";
  el("page-bg").style.background = buildPageBackground(theme);
}

function applyBrand(businessName, logoUrl) {
  const name = businessName || "Онлайн-запись";
  el("brand-name").textContent = name;
  el("brand-mark").textContent = name.trim().charAt(0).toUpperCase() || "З";
  const logo = el("brand-logo");
  const url = safeMediaUrl(logoUrl);
  if (url) {
    logo.src = url;
    logo.classList.remove("hidden");
    el("brand-mark").classList.add("hidden");
  } else {
    logo.classList.add("hidden");
    logo.removeAttribute("src");
    el("brand-mark").classList.remove("hidden");
  }
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
  const emptyMsg = el("no-services-msg");
  emptyMsg.textContent = state.isOwner
    ? "Пока нет ни одной услуги. Добавьте их во вкладке «Управление» → «Позиции»."
    : "Запись скоро откроется — владелец ещё настраивает услуги. Загляните позже!";
  emptyMsg.classList.toggle("hidden", state.services.length > 0);
  const list = el("services-list");
  list.innerHTML = "";
  state.services.forEach((service) => {
    const card = document.createElement("div");
    card.className = "card";
    const meta = service.type === "slot" ? `${service.duration_min} мин` : "В наличии";
    const photo = safeMediaUrl(service.image_url);
    card.innerHTML = `
      ${photo ? `<img class="card-thumb" src="${photo}" loading="lazy" alt="" />` : ""}
      <div class="card-body">
        <div class="title">${escapeHtml(service.name)}</div>
        <div class="meta">${meta}</div>
        ${service.description ? `<div class="desc">${escapeHtml(service.description)}</div>` : ""}
      </div>
      <span class="price-tag">${service.price} ₽</span>
    `;
    card.addEventListener("click", () => selectService(service));
    list.appendChild(card);
  });
}

// ======================================================================
// «Мои записи» (клиент): список своих записей и отмена
// ======================================================================

const clientAuth = () => ({ init_data: state.initData, client_tg_id: state.myTgId, business_id: state.businessId });

el("my-bookings-btn").addEventListener("click", openMyBookings);
el("done-my-btn").addEventListener("click", openMyBookings);

async function openMyBookings() {
  tg.MainButton.hide();
  showScreen("my");
  await loadMyBookings();
}

function renderMyBooking(booking) {
  const card = document.createElement("div");
  card.className = "order-card" + (booking.can_cancel ? "" : " past");
  const whenText = booking.date ? `${formatDateLabel(booking.date).top} в ${booking.time}` : "разовый заказ";
  const qtyText = booking.quantity > 1 ? ` × ${booking.quantity}` : "";
  card.innerHTML = `
    <div class="order-top">
      <div>
        <div class="order-title">${escapeHtml(booking.service_name)}${qtyText}</div>
        <div class="order-meta">${whenText}</div>
        ${booking.master_name ? `<div class="order-meta">Мастер: ${escapeHtml(booking.master_name)}</div>` : ""}
        <div class="order-meta">${booking.price * booking.quantity} ₽</div>
      </div>
      <span class="status-badge status-${booking.status}">${statusLabels[booking.status] || booking.status}</span>
    </div>
    ${booking.can_cancel ? `<div class="order-actions"><button class="btn-cancel">Отменить запись</button></div>` : ""}
  `;
  const cancelBtn = card.querySelector(".btn-cancel");
  if (cancelBtn) cancelBtn.addEventListener("click", () => cancelMyBooking(booking));
  return card;
}

async function loadMyBookings() {
  const upcomingList = el("my-upcoming-list");
  const historyList = el("my-history-list");
  upcomingList.innerHTML = "";
  historyList.innerHTML = "";
  ["my-upcoming-title", "my-history-title", "my-empty-msg", "my-error-msg"].forEach((id) => el(id).classList.add("hidden"));

  let bookings;
  try {
    bookings = await api(`/api/my-bookings?${qs(clientAuth())}`);
  } catch (e) {
    el("my-error-msg").textContent = e.message;
    el("my-error-msg").classList.remove("hidden");
    return;
  }

  const upcoming = bookings.filter((b) => b.can_cancel);
  const history = bookings.filter((b) => !b.can_cancel);
  upcoming.forEach((b) => upcomingList.appendChild(renderMyBooking(b)));
  history.forEach((b) => historyList.appendChild(renderMyBooking(b)));
  el("my-upcoming-title").classList.toggle("hidden", upcoming.length === 0);
  el("my-history-title").classList.toggle("hidden", history.length === 0);
  el("my-empty-msg").classList.toggle("hidden", bookings.length > 0);
}

async function cancelMyBooking(booking) {
  if (!confirm("Отменить эту запись?")) return;
  try {
    await api(`/api/my-bookings/${booking.id}/cancel`, { method: "POST", body: JSON.stringify(clientAuth()) });
  } catch (e) {
    alert(e.message);
  }
  loadMyBookings();
}

function selectService(service) {
  state.selectedService = service;
  state.selectedMaster = null;
  state.quantity = 1;
  state.comment = "";
  if (service.type === "slot") {
    if (state.config.use_masters) {
      loadMasters();
      showScreen("masters");
    } else {
      setDatesBackTarget("services");
      loadDates();
      showScreen("dates");
    }
  } else {
    showDetailsScreen();
    showScreen("details");
  }
}

// Куда ведёт «Назад» на экране дат: к выбору мастера (если он включён) или к списку услуг.
function setDatesBackTarget(target) {
  document.querySelector("#screen-dates .back-btn").dataset.back = target;
}

async function loadMasters() {
  const list = el("masters-list");
  const noMastersMsg = el("no-masters-msg");
  list.innerHTML = "";
  noMastersMsg.classList.add("hidden");

  try {
    state.masters = await api(`/api/masters?${qs({
      service_id: state.selectedService.id,
      business_id: state.businessId,
    })}`);
  } catch (e) {
    alert(e.message);
    return;
  }
  if (state.masters.length === 0) {
    noMastersMsg.classList.remove("hidden");
    return;
  }
  state.masters.forEach((master) => {
    const card = document.createElement("div");
    card.className = "card";
    card.innerHTML = `
      <div class="title">${escapeHtml(master.name)}</div>
      <span class="chev">›</span>
    `;
    card.addEventListener("click", () => selectMaster(master));
    list.appendChild(card);
  });
}

function selectMaster(master) {
  state.selectedMaster = master;
  state.selectedDate = null;
  setDatesBackTarget("masters");
  loadDates();
  showScreen("dates");
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
    master_id: state.selectedMaster ? state.selectedMaster.id : null,
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

  const heroPhoto = safeMediaUrl(s.image_url);
  if (heroPhoto) {
    el("details-photo").src = heroPhoto;
  } else {
    el("details-photo").removeAttribute("src");
  }
  el("details-photo").classList.toggle("hidden", !heroPhoto);
  el("details-desc").textContent = s.description || "";
  el("details-desc").classList.toggle("hidden", !s.description);
  el("details-hero").classList.toggle("hidden", !heroPhoto && !s.description);

  const phoneMode = state.config.collect_phone || "off";
  el("phone-block").classList.toggle("hidden", phoneMode === "off");
  el("phone-label").textContent = phoneMode === "required" ? "Телефон" : "Телефон (необязательно)";
  if (phoneMode !== "off") {
    if (!el("phone-input").value) el("phone-input").value = storageGet("tgb_phone");
    el("consent-check").checked = false; // согласие даётся заново при каждой заявке
  }

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
  if (s.type === "slot" && state.selectedMaster) {
    rows += `<div class="row"><span class="label">Мастер</span><span>${escapeHtml(state.selectedMaster.name)}</span></div>`;
  }
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

el("privacy-link").addEventListener("click", (e) => {
  e.preventDefault();
  const url = state.config.privacy_url || `${location.origin}/privacy.html?business_id=${state.businessId}`;
  if (isRealTelegram && tg.openLink) {
    tg.openLink(url);
  } else {
    window.open(url, "_blank");
  }
});

async function submitBooking() {
  const user = tg.initDataUnsafe && tg.initDataUnsafe.user;
  const s = state.selectedService;

  const phoneMode = state.config.collect_phone || "off";
  let phone = null;
  let consent = false;
  if (phoneMode !== "off") {
    phone = el("phone-input").value.trim();
    consent = el("consent-check").checked;
    if (!phone && phoneMode === "required") {
      alert("Укажите номер телефона");
      return;
    }
    if (phone && !consent) {
      alert("Отметьте согласие на обработку персональных данных");
      return;
    }
  }

  try {
    const result = await api("/api/book", {
      method: "POST",
      body: JSON.stringify({
        business_id: state.businessId,
        service_id: s.id,
        master_id: s.type === "slot" && state.selectedMaster ? state.selectedMaster.id : null,
        date: s.type === "slot" ? state.selectedDate : null,
        time: s.type === "slot" ? state.selectedTime : null,
        quantity: state.quantity,
        comment: state.comment || null,
        client_name: user ? `${user.first_name || ""} ${user.last_name || ""}`.trim() : "Гость",
        init_data: state.initData,
        client_tg_id: user ? user.id : null,
        client_phone: phone || null,
        consent,
      }),
    });
    if (phone) storageSet("tgb_phone", phone);

    let rows = `<div class="row"><span class="label">Позиция</span><span>${escapeHtml(result.service_name)}</span></div>`;
    if (result.master_name) {
      rows += `<div class="row"><span class="label">Мастер</span><span>${escapeHtml(result.master_name)}</span></div>`;
    }
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
  btn.addEventListener("click", () => {
    const target = btn.dataset.back;
    if (state.admin.wizardActive && target === "admin-services") {
      showAdminScreen("wizard");
    } else if (target.startsWith("admin-")) {
      showAdminScreen(target.slice("admin-".length));
    } else {
      showScreen(target);
    }
  });
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

el("admin-tab-masters").addEventListener("click", () => {
  applyTheme(state.config.theme); // сбрасываем несохранённое превью оформления, если было
  setActiveAdminTab("admin-tab-masters");
  showAdminScreen("masters");
  loadAdminMasters();
});

el("admin-tab-settings").addEventListener("click", () => {
  applyTheme(state.config.theme); // сбрасываем несохранённое превью оформления, если было
  setActiveAdminTab("admin-tab-settings");
  showAdminScreen("settings");
  loadSettings();
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
  ["admin-tab-orders", "admin-tab-services", "admin-tab-masters", "admin-tab-theme", "admin-tab-schedule", "admin-tab-settings"].forEach((id) => {
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
          ${order.master_name ? `<div class="order-meta">Мастер: ${escapeHtml(order.master_name)}</div>` : ""}
          ${order.client_phone ? `<div class="order-meta">📞 ${escapeHtml(order.client_phone)}</div>` : ""}
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
    const thumb = safeMediaUrl(service.image_url);
    card.innerHTML = `
      <div style="display:flex;align-items:center;min-width:0">
        ${thumb ? `<img class="admin-thumb" src="${thumb}" alt="" />` : ""}
        <div>
          <div class="title">${escapeHtml(service.name)} — ${service.price} ₽</div>
          <div class="badge-type">${meta}${service.is_active ? "" : " · скрыта"}</div>
        </div>
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
  el("service-desc").value = service && service.description ? service.description : "";
  state.admin.serviceImage = service && service.image_id && safeMediaUrl(service.image_url)
    ? { id: service.image_id, url: service.image_url }
    : null;
  showPicked("service-photo", state.admin.serviceImage);
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

bindPhotoPicker(
  "service-photo", { maxSide: 900, quality: 0.82 },
  async (image) => { state.admin.serviceImage = image; },
  async () => { state.admin.serviceImage = null; },
);

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
    description: el("service-desc").value.trim() || null,
    image_id: state.admin.serviceImage ? state.admin.serviceImage.id : null,
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
    if (state.admin.wizardActive) {
      showWizardStep(2);
      return;
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
// АДМИН-РЕЖИМ: мастера
// ======================================================================

const adminAuth = () => ({ init_data: state.initData, owner_tg_id: state.myTgId, business_id: state.businessId });

async function loadAdminMasters() {
  const data = await api(`/api/admin/masters?${qs(adminAuth())}`);
  state.admin.masters = data.masters;
  state.config.use_masters = data.use_masters;
  el("use-masters-toggle").checked = data.use_masters;

  const list = el("admin-masters-list");
  list.innerHTML = "";
  el("no-masters-admin-msg").classList.toggle("hidden", data.masters.length > 0);

  data.masters.forEach((master) => {
    const card = document.createElement("div");
    card.className = "service-admin-card" + (master.is_active ? "" : " inactive");
    const scope = master.service_ids.length ? `услуг: ${master.service_ids.length}` : "все услуги";
    card.innerHTML = `
      <div>
        <div class="title">${escapeHtml(master.name)}</div>
        <div class="badge-type">${scope}${master.is_active ? "" : " · скрыт"}</div>
      </div>
      <span>✎</span>
    `;
    card.addEventListener("click", () => openMasterForm(master));
    list.appendChild(card);
  });
}

el("use-masters-toggle").addEventListener("change", async (e) => {
  const enabled = e.target.checked;
  try {
    await api("/api/admin/masters-settings", {
      method: "PUT",
      body: JSON.stringify({ ...adminAuth(), use_masters: enabled }),
    });
    state.config.use_masters = enabled;
  } catch (err) {
    e.target.checked = !enabled;
    alert(err.message);
  }
});

el("add-master-btn").addEventListener("click", () => openMasterForm(null));

async function openMasterForm(master) {
  state.admin.editingMasterId = master ? master.id : null;
  el("master-form-title").textContent = master ? "Редактировать мастера" : "Новый мастер";
  el("master-name").value = master ? master.name : "";
  el("master-active").checked = master ? !!master.is_active : true;
  el("delete-master-btn").classList.toggle("hidden", !master);

  const services = (await api(`/api/admin/services?${qs(adminAuth())}`)).filter((s) => s.type === "slot");
  const box = el("master-services-box");
  box.innerHTML = "";
  if (services.length === 0) {
    box.innerHTML = `<p class="hint">Пока нет позиций по расписанию — мастер будет вести все, что появятся.</p>`;
  }
  services.forEach((service) => {
    const label = document.createElement("label");
    // пустой список у мастера = ведёт все услуги
    const checked = !master || master.service_ids.length === 0 || master.service_ids.includes(service.id);
    label.innerHTML = `<input type="checkbox" value="${service.id}" ${checked ? "checked" : ""} /> <span>${escapeHtml(service.name)}</span>`;
    box.appendChild(label);
  });
  showAdminScreen("master-form");
}

el("save-master-btn").addEventListener("click", async () => {
  const name = el("master-name").value.trim();
  if (!name) {
    alert("Укажите имя мастера");
    return;
  }
  const boxes = [...el("master-services-box").querySelectorAll("input[type=checkbox]")];
  const serviceIds = boxes.filter((b) => b.checked).map((b) => Number(b.value));
  if (boxes.length > 0 && serviceIds.length === 0) {
    alert("Отметьте хотя бы одну услугу");
    return;
  }

  const payload = { ...adminAuth(), name, service_ids: serviceIds, is_active: el("master-active").checked };
  try {
    if (state.admin.editingMasterId) {
      await api(`/api/admin/masters/${state.admin.editingMasterId}`, { method: "PUT", body: JSON.stringify(payload) });
    } else {
      await api("/api/admin/masters", { method: "POST", body: JSON.stringify(payload) });
    }
    showAdminScreen("masters");
    loadAdminMasters();
  } catch (e) {
    alert(e.message);
  }
});

el("delete-master-btn").addEventListener("click", async () => {
  if (!state.admin.editingMasterId) return;
  if (!confirm("Удалить мастера? Уже созданные записи к нему останутся в истории.")) return;
  try {
    await api(`/api/admin/masters/${state.admin.editingMasterId}?${qs(adminAuth())}`, { method: "DELETE" });
    showAdminScreen("masters");
    loadAdminMasters();
  } catch (e) {
    alert(e.message);
  }
});

// ======================================================================
// АДМИН-РЕЖИМ: оформление
// ======================================================================

const THEME_EXTRA_DEFAULTS = {
  bg_mode: "color", bg_color2: "#FFFFFF", bg_angle: 160, bg_image_id: null, bg_image_url: null,
  bg_overlay: 60, primary_color2: null, card_style: "shadow", font: "sans",
};

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
  {
    name: "Закат", bg_mode: "gradient", bg_color: "#FFF1E6", bg_color2: "#FFD3E0", bg_angle: 160,
    surface_color: "#FFFFFF", text_color: "#3B1F2B", hint_color: "#8A6F78",
    primary_color: "#F0563F", primary_color2: "#FF9A3C", primary_text_color: "#FFFFFF",
    danger_color: "#C62828", success_color: "#2E7D32", radius: 20, font: "rounded",
  },
  {
    name: "Лаванда", bg_mode: "gradient", bg_color: "#F3EEFF", bg_color2: "#DDEBFF", bg_angle: 160,
    surface_color: "#FFFFFF", text_color: "#24204A", hint_color: "#74709A",
    primary_color: "#6C4CF1", primary_color2: "#9B6BFF", primary_text_color: "#FFFFFF",
    danger_color: "#D6336C", success_color: "#2B8A3E", radius: 22, font: "rounded",
  },
  {
    name: "Мята", bg_mode: "gradient", bg_color: "#E6FAF1", bg_color2: "#E4F1FF", bg_angle: 150,
    surface_color: "#FFFFFF", text_color: "#10352B", hint_color: "#5F8177",
    primary_color: "#12B981", primary_color2: "#0EA5A5", primary_text_color: "#FFFFFF",
    danger_color: "#D9480F", success_color: "#0F766E", radius: 18,
  },
  {
    name: "Ночь", bg_mode: "gradient", bg_color: "#0F1226", bg_color2: "#1E2247", bg_angle: 170,
    surface_color: "#1D2140", text_color: "#F2F4FF", hint_color: "#9AA0C7",
    primary_color: "#7C83FF", primary_color2: "#B26BFF", primary_text_color: "#FFFFFF",
    danger_color: "#FF6B7A", success_color: "#4ADE9A", radius: 16, card_style: "outline",
  },
  {
    name: "Кофе", bg_color: "#F6EFE7", surface_color: "#FFFDF9", text_color: "#3A2A1E",
    hint_color: "#8C7663", primary_color: "#7A4B2A", primary_text_color: "#FFFFFF",
    danger_color: "#B3261E", success_color: "#3F7D3A", radius: 8, font: "serif", card_style: "outline",
  },
];

function primaryFillCss(theme) {
  return theme.primary_color2
    ? `linear-gradient(135deg, ${theme.primary_color}, ${theme.primary_color2})`
    : theme.primary_color;
}

function renderThemePresets() {
  const row = el("theme-presets");
  row.innerHTML = "";
  THEME_PRESETS.forEach((preset) => {
    const btn = document.createElement("button");
    btn.className = "preset-swatch";
    btn.innerHTML = `<span class="dot" style="background:${primaryFillCss(preset)}"></span><span>${preset.name}</span>`;
    btn.addEventListener("click", () => {
      fillThemeForm(preset);
      previewTheme();
    });
    row.appendChild(btn);
  });
}

function setBgMode(mode) {
  state.admin.bgMode = mode;
  document.querySelectorAll(".bg-mode-btn").forEach((b) => b.classList.toggle("active", b.dataset.mode === mode));
  el("bg-gradient-block").classList.toggle("hidden", mode !== "gradient");
  el("bg-image-block").classList.toggle("hidden", mode !== "image");
}

function setButtonGradient(enabled) {
  el("button-gradient-toggle").checked = enabled;
  el("button-gradient-block").classList.toggle("hidden", !enabled);
}

function fillThemeForm(theme) {
  const t = { ...THEME_EXTRA_DEFAULTS, ...theme };
  Object.keys(THEME_VAR_MAP).forEach((key) => {
    el(`theme-${key}`).value = t[key];
  });
  el("theme-radius").value = t.radius;
  el("radius-value").textContent = t.radius;

  setBgMode(t.bg_mode);
  el("theme-bg_color2").value = t.bg_color2;
  el("theme-bg_angle").value = t.bg_angle;
  el("angle-value").textContent = t.bg_angle;
  el("theme-bg_overlay").value = t.bg_overlay;
  el("overlay-value").textContent = t.bg_overlay;
  state.admin.themeBg = t.bg_image_id && safeMediaUrl(t.bg_image_url) ? { id: t.bg_image_id, url: t.bg_image_url } : null;
  showPicked("bg-image", state.admin.themeBg);

  setButtonGradient(!!t.primary_color2);
  if (t.primary_color2) el("theme-primary_color2").value = t.primary_color2;
  el("theme-card_style").value = t.card_style;
  el("theme-font").value = t.font;
}

function readThemeForm() {
  const theme = { init_data: state.initData, owner_tg_id: state.myTgId, business_id: state.businessId };
  Object.keys(THEME_VAR_MAP).forEach((key) => {
    theme[key] = el(`theme-${key}`).value;
  });
  theme.radius = parseInt(el("theme-radius").value, 10);
  theme.bg_mode = state.admin.bgMode || "color";
  theme.bg_color2 = el("theme-bg_color2").value;
  theme.bg_angle = parseInt(el("theme-bg_angle").value, 10);
  theme.bg_overlay = parseInt(el("theme-bg_overlay").value, 10);
  theme.bg_image_id = state.admin.themeBg ? state.admin.themeBg.id : null;
  theme.bg_image_url = state.admin.themeBg ? state.admin.themeBg.url : null;
  theme.primary_color2 = el("button-gradient-toggle").checked ? el("theme-primary_color2").value : null;
  theme.card_style = el("theme-card_style").value;
  theme.font = el("theme-font").value;
  return theme;
}

function previewTheme() {
  applyTheme(readThemeForm());
}

function openThemeForm(theme) {
  renderThemePresets();
  fillThemeForm(theme || state.config.theme);
  showPicked("logo", state.config.logo_url ? { url: state.config.logo_url } : null);
  loadSavedThemes();
}

document.querySelectorAll('.theme-field-grid input[type="color"]').forEach((input) => {
  input.addEventListener("input", previewTheme);
});

document.querySelectorAll(".bg-mode-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    setBgMode(btn.dataset.mode);
    previewTheme();
  });
});

el("theme-bg_angle").addEventListener("input", () => {
  el("angle-value").textContent = el("theme-bg_angle").value;
  previewTheme();
});

el("theme-bg_overlay").addEventListener("input", () => {
  el("overlay-value").textContent = el("theme-bg_overlay").value;
  previewTheme();
});

el("button-gradient-toggle").addEventListener("change", () => {
  setButtonGradient(el("button-gradient-toggle").checked);
  previewTheme();
});

["theme-card_style", "theme-font"].forEach((id) => el(id).addEventListener("change", previewTheme));

el("theme-radius").addEventListener("input", () => {
  el("radius-value").textContent = el("theme-radius").value;
  previewTheme();
});

// Картинка фона: загружается сразу на сервер, но применяется к бизнесу только по «Сохранить оформление».
bindPhotoPicker(
  "bg-image", { maxSide: 1280, quality: 0.72 },
  async (image) => { state.admin.themeBg = image; previewTheme(); },
  async () => { state.admin.themeBg = null; previewTheme(); },
);

// Логотип сохраняется сразу — он не часть «цветовой схемы».
bindPhotoPicker(
  "logo", { maxSide: 256, png: true },
  async (image) => {
    await api("/api/admin/logo", { method: "PUT", body: JSON.stringify({ ...adminAuth(), media_id: image.id }) });
    state.config.logo_url = image.url;
    applyBrand(state.config.business_name, image.url);
  },
  async () => {
    await api("/api/admin/logo", { method: "PUT", body: JSON.stringify({ ...adminAuth(), media_id: null }) });
    state.config.logo_url = null;
    applyBrand(state.config.business_name, null);
  },
);

el("save-theme-btn").addEventListener("click", async () => {
  try {
    const payload = readThemeForm();
    if (payload.bg_mode === "image" && !payload.bg_image_id) {
      alert("Загрузите картинку для фона или выберите другой тип фона");
      return;
    }
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

// ---------- Сохранённые варианты оформления ----------

async function loadSavedThemes() {
  const box = el("saved-themes");
  try {
    state.admin.savedThemes = await api(`/api/admin/themes/saved?${qs(adminAuth())}`);
  } catch (e) {
    box.innerHTML = "";
    return;
  }
  box.innerHTML = "";
  state.admin.savedThemes.forEach((saved) => {
    const row = document.createElement("div");
    row.className = "saved-theme-row";
    row.innerHTML = `<span class="dot" style="background:${primaryFillCss(saved.theme)}"></span>
      <span class="name">${escapeHtml(saved.name)}</span>
      <button type="button" class="link-btn" title="Удалить">✕</button>`;
    row.addEventListener("click", () => {
      fillThemeForm(saved.theme);
      previewTheme();
    });
    row.querySelector(".link-btn").addEventListener("click", async (e) => {
      e.stopPropagation();
      if (!confirm(`Удалить вариант «${saved.name}»?`)) return;
      try {
        await api(`/api/admin/themes/saved/${saved.id}?${qs(adminAuth())}`, { method: "DELETE" });
      } catch (err) {
        alert(err.message);
      }
      loadSavedThemes();
    });
    box.appendChild(row);
  });
}

el("save-variant-btn").addEventListener("click", async () => {
  const name = el("saved-theme-name").value.trim();
  if (!name) {
    alert("Введите название варианта");
    return;
  }
  const payload = { ...readThemeForm(), name };
  if (payload.bg_mode === "image" && !payload.bg_image_id) {
    alert("Загрузите картинку для фона или выберите другой тип фона");
    return;
  }
  try {
    await api("/api/admin/themes/saved", { method: "POST", body: JSON.stringify(payload) });
    el("saved-theme-name").value = "";
    loadSavedThemes();
  } catch (e) {
    alert(e.message);
  }
});

// ======================================================================
// Мастер первого запуска: ниша → рабочие часы → готово
// ======================================================================

async function maybeStartWizard() {
  if (!state.isOwner) return;
  let onboarding;
  try {
    onboarding = await api(`/api/admin/onboarding?${qs(adminAuth())}`);
  } catch (e) {
    return; // например, нет подписи Telegram — просто без мастера
  }
  if (!onboarding.needs_setup) return;

  state.admin.wizardActive = true;
  state.admin.wizardNiches = onboarding.niches;
  state.admin.wizardSelected = null;
  state.admin.wizardSchedule = onboarding.schedule;
  document.querySelector(".admin-tabs").classList.add("hidden");
  switchMode("admin");
  showAdminScreen("wizard");
  renderWizardNiches();
  showWizardStep(1);
}

function showWizardStep(step) {
  [1, 2, 3].forEach((n) => el(`wizard-step-${n}`).classList.toggle("hidden", n !== step));
  el("wizard-step-label").textContent = `Шаг ${step} из 3`;
  showAdminScreen("wizard");
  if (step === 2) fillWizardSchedule();
  window.scrollTo(0, 0);
}

// «1 услуга, 3 услуги, 5 услуг»
function pluralRu(n, forms) {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return forms[0];
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return forms[1];
  return forms[2];
}

function renderWizardNiches() {
  const list = el("wizard-niches");
  list.innerHTML = "";
  state.admin.wizardNiches.forEach((niche) => {
    const card = document.createElement("div");
    card.className = "card";
    card.dataset.niche = niche.id;
    card.innerHTML = `
      <div class="card-body">
        <div class="title">${niche.emoji} ${escapeHtml(niche.title)}</div>
        <div class="meta">${niche.services.length} ${pluralRu(niche.services.length, ["услуга", "услуги", "услуг"])} в примере</div>
      </div>
      <span class="chev">›</span>
    `;
    card.addEventListener("click", () => selectWizardNiche(niche.id));
    list.appendChild(card);
  });
}

function selectWizardNiche(nicheId) {
  state.admin.wizardSelected = nicheId;
  const niche = state.admin.wizardNiches.find((n) => n.id === nicheId);
  document.querySelectorAll("#wizard-niches .card").forEach((c) => c.classList.toggle("selected", c.dataset.niche === nicheId));
  el("wizard-preview-list").innerHTML = niche.services
    .map((s) => `<div class="row"><span>${escapeHtml(s.name)}</span><span>${s.price} ₽ · ${s.duration_min} мин</span></div>`)
    .join("");
  el("wizard-preview").classList.remove("hidden");
  el("wizard-preview").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

el("wizard-apply-btn").addEventListener("click", async () => {
  if (!state.admin.wizardSelected) return;
  const btn = el("wizard-apply-btn");
  btn.disabled = true;
  try {
    await api("/api/admin/onboarding/apply", {
      method: "POST",
      body: JSON.stringify({ ...adminAuth(), niche_id: state.admin.wizardSelected }),
    });
    showWizardStep(2);
  } catch (e) {
    alert(e.message);
  } finally {
    btn.disabled = false;
  }
});

el("wizard-empty-btn").addEventListener("click", () => openServiceForm(null));

// Часовой пояс — те же варианты, что на вкладке «Расписание»; по умолчанию берём пояс телефона владельца.
function fillWizardSchedule() {
  const tzSelect = el("wizard-timezone");
  if (!tzSelect.options.length) tzSelect.innerHTML = el("schedule-timezone").innerHTML;
  const niche = state.admin.wizardNiches.find((n) => n.id === state.admin.wizardSelected);
  const base = { ...state.admin.wizardSchedule, ...(niche ? niche.schedule : {}) };
  let deviceTz = "";
  try { deviceTz = Intl.DateTimeFormat().resolvedOptions().timeZone; } catch (e) { /* не критично */ }
  const hasOption = (tz) => [...tzSelect.options].some((o) => o.value === tz);
  tzSelect.value = hasOption(deviceTz) ? deviceTz : base.timezone;
  el("wizard-start").value = base.work_start_hour;
  el("wizard-end").value = base.work_end_hour;
  el("wizard-step").value = base.slot_step_minutes;
  el("wizard-days").value = base.days_ahead;
}

el("wizard-save-schedule-btn").addEventListener("click", async () => {
  const payload = {
    ...adminAuth(),
    timezone: el("wizard-timezone").value,
    work_start_hour: parseInt(el("wizard-start").value, 10),
    work_end_hour: parseInt(el("wizard-end").value, 10),
    slot_step_minutes: parseInt(el("wizard-step").value, 10),
    days_ahead: parseInt(el("wizard-days").value, 10),
  };
  if (Object.values(payload).some((v) => typeof v === "number" && isNaN(v))) {
    alert("Заполните часы работы и количество дней");
    return;
  }
  if (payload.work_end_hour <= payload.work_start_hour) {
    alert("Время закрытия должно быть позже времени открытия");
    return;
  }
  try {
    await api("/api/admin/schedule", { method: "PUT", body: JSON.stringify(payload) });
    showWizardStep(3);
  } catch (e) {
    alert(e.message);
  }
});

el("wizard-skip-schedule-btn").addEventListener("click", () => showWizardStep(3));

el("wizard-finish-btn").addEventListener("click", async () => {
  state.admin.wizardActive = false;
  document.querySelector(".admin-tabs").classList.remove("hidden");
  await loadServices(); // клиентский вид теперь с услугами
  el("admin-tab-services").click();
});

// ======================================================================
// АДМИН-РЕЖИМ: настройки (телефон клиента, политика)
// ======================================================================

async function loadSettings() {
  const settings = await api(`/api/admin/settings?${qs(adminAuth())}`);
  el("settings-collect-phone").value = settings.collect_phone;
  el("settings-privacy-url").value = settings.privacy_url || "";
}

el("save-settings-btn").addEventListener("click", async () => {
  const payload = {
    ...adminAuth(),
    collect_phone: el("settings-collect-phone").value,
    privacy_url: el("settings-privacy-url").value.trim() || null,
  };
  try {
    await api("/api/admin/settings", { method: "PUT", body: JSON.stringify(payload) });
    state.config.collect_phone = payload.collect_phone;
    state.config.privacy_url = payload.privacy_url;
    el("save-settings-btn").textContent = "Сохранено ✓";
    setTimeout(() => { el("save-settings-btn").textContent = "Сохранить настройки"; }, 1500);
  } catch (e) {
    alert(e.message);
  }
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
  applyBrand(state.config.business_name, state.config.logo_url);

  const user = tg.initDataUnsafe && tg.initDataUnsafe.user;
  state.myTgId = user ? user.id : null;
  state.isOwner = !!(state.myTgId && state.config.owner_tg_id && state.myTgId === state.config.owner_tg_id);

  setupModeSwitch();
  await loadServices();
  showScreen("services");
  await maybeStartWizard();
}

init();
