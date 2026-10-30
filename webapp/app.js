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
  close: () => toast("В демо-режиме (вне Telegram) закрытие недоступно"),
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
    clients: [],
    clientId: null,
    manualClientId: null,
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
  reschedule: el("screen-reschedule"),
  service: el("screen-service"),
  dates: el("screen-dates"),
  slots: el("screen-slots"),
  details: el("screen-details"),
  done: el("screen-done"),
};

const adminScreens = {
  orders: el("admin-screen-orders"),
  clients: el("admin-screen-clients"),
  stats: el("admin-screen-stats"),
  reschedule: el("admin-screen-reschedule"),
  client: el("admin-screen-client"),
  manual: el("admin-screen-manual"),
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
      toast(err.message);
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
      toast(err.message);
    }
  });
}

// Иконки из спрайта в index.html (<symbol id="i-имя">).
const icon = (name) => `<svg class="icon" aria-hidden="true"><use href="#i-${String(name).replace(/[^a-z-]/g, "")}"/></svg>`;

// Короткое уведомление вместо системного toast(): не блокирует страницу и выглядит как часть приложения.
let toastTimer = null;
function toast(message) {
  const box = el("toast");
  box.textContent = message;
  box.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => box.classList.add("hidden"), Math.max(2800, String(message).length * 55));
}

// Подтверждение вместо системного confirm(): возвращает Promise<boolean>.
function askConfirm(text, yesLabel = "Да") {
  return new Promise((resolve) => {
    el("confirm-text").textContent = text;
    el("confirm-yes").textContent = yesLabel;
    el("confirm-modal").classList.remove("hidden");
    const finish = (answer) => {
      el("confirm-modal").classList.add("hidden");
      el("confirm-yes").onclick = null;
      el("confirm-no").onclick = null;
      resolve(answer);
    };
    el("confirm-yes").onclick = () => finish(true);
    el("confirm-no").onclick = () => finish(false);
  });
}

// «Сохранено» на кнопке на полторы секунды.
function flashSaved(btn, label) {
  btn.innerHTML = `${icon("check")} Сохранено`;
  setTimeout(() => { btn.textContent = label; }, 1500);
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
  if (state.config.suspended) {
    // Пробный период/подписка закончились. Клиенту про оплату не пишем — только что запись пока недоступна
    // (сервер тоже не примет заявку: см. /api/book). «Мои записи» остаются: отменить свою запись можно всегда.
    emptyMsg.textContent = "Запись временно недоступна. Попробуйте позже.";
    emptyMsg.classList.remove("hidden");
    el("services-list").innerHTML = "";
    return;
  }
  emptyMsg.textContent = state.isOwner
    ? "Пока нет ни одной услуги. Добавьте их во вкладке «Управление», раздел «Позиции»."
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
    card.addEventListener("click", () => openService(service));
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
    ${booking.can_cancel ? `<div class="order-actions">${booking.can_reschedule ? `<button class="btn-move btn-reschedule">Перенести</button>` : ""}<button class="btn-cancel">Отменить запись</button></div>` : ""}
  `;
  const cancelBtn = card.querySelector(".btn-cancel");
  if (cancelBtn) cancelBtn.addEventListener("click", () => cancelMyBooking(booking));
  const moveBtn = card.querySelector(".btn-reschedule");
  if (moveBtn) moveBtn.addEventListener("click", () => startReschedule(booking));
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

// ----- перенос записи клиентом -----

const reschedule = { booking: null, date: null, time: null };

async function startReschedule(booking) {
  Object.assign(reschedule, { booking, date: null, time: null });
  const whenText = `${formatDateLabel(booking.date).top} в ${booking.time}`;
  el("reschedule-info").textContent = `${booking.service_name}${booking.master_name ? ` · ${booking.master_name}` : ""}. Сейчас: ${whenText}.`;
  el("reschedule-slots").innerHTML = "";
  el("reschedule-no-slots").classList.add("hidden");
  el("reschedule-confirm").disabled = true;
  let dates;
  try {
    dates = await api(`/api/dates?${qs({ business_id: state.businessId })}`);
  } catch (e) {
    toast(e.message);
    return;
  }
  const list = el("reschedule-dates");
  list.innerHTML = "";
  dates.forEach((date) => {
    const { top, weekday } = formatDateLabel(date);
    const pill = document.createElement("div");
    pill.className = "pill";
    pill.innerHTML = `<span class="weekday">${weekday}</span>${top}`;
    pill.addEventListener("click", () => pickRescheduleDate(date, pill));
    list.appendChild(pill);
  });
  showScreen("reschedule");
}

async function pickRescheduleDate(date, pillEl) {
  document.querySelectorAll("#reschedule-dates .pill").forEach((p) => p.classList.remove("selected"));
  pillEl.classList.add("selected");
  Object.assign(reschedule, { date, time: null });
  el("reschedule-confirm").disabled = true;
  let slots = [];
  try {
    slots = await api(`/api/my-bookings/${reschedule.booking.id}/slots?${qs({ ...clientAuth(), date })}`);
  } catch (e) {
    toast(e.message);
  }
  const list = el("reschedule-slots");
  list.innerHTML = "";
  el("reschedule-no-slots").classList.toggle("hidden", slots.length > 0);
  slots.forEach((time) => {
    const slotEl = document.createElement("div");
    slotEl.className = "slot";
    slotEl.textContent = time;
    slotEl.addEventListener("click", () => {
      document.querySelectorAll("#reschedule-slots .slot").forEach((s) => s.classList.remove("selected"));
      slotEl.classList.add("selected");
      reschedule.time = time;
      el("reschedule-confirm").disabled = false;
    });
    list.appendChild(slotEl);
  });
}

el("reschedule-confirm").addEventListener("click", async () => {
  if (!reschedule.booking || !reschedule.date || !reschedule.time) return;
  const btn = el("reschedule-confirm");
  btn.disabled = true;
  try {
    await api(`/api/my-bookings/${reschedule.booking.id}/reschedule`, {
      method: "POST",
      body: JSON.stringify({ ...clientAuth(), date: reschedule.date, time: reschedule.time }),
    });
    toast("Запись перенесена");
    openMyBookings();
  } catch (e) {
    toast(e.message);
    btn.disabled = false;
    // время могли занять, пока выбирали: обновляем список времени выбранной даты
    const pill = [...document.querySelectorAll("#reschedule-dates .pill")].find((p) => p.classList.contains("selected"));
    if (pill) pickRescheduleDate(reschedule.date, pill);
  }
});

async function cancelMyBooking(booking) {
  if (!(await askConfirm("Отменить эту запись?", "Отменить запись"))) return;
  try {
    await api(`/api/my-bookings/${booking.id}/cancel`, { method: "POST", body: JSON.stringify(clientAuth()) });
  } catch (e) {
    toast(e.message);
  }
  loadMyBookings();
}

// Страница услуги: показываем, только если есть что показать (описание или фото), иначе сразу запись, как раньше.
function openService(service) {
  const images = (service.images || []).map((i) => safeMediaUrl(i.url)).filter(Boolean);
  if (!service.description && images.length === 0) {
    selectService(service);
    return;
  }
  state.servicePage = service;
  el("service-page-title").textContent = service.name;
  el("service-page-meta").textContent = [service.type === "slot" ? `${service.duration_min} мин` : null, `${service.price} ₽`]
    .filter(Boolean).join(" · ");
  const desc = el("service-page-desc");
  desc.textContent = service.description || "";
  desc.classList.toggle("hidden", !service.description);

  const track = el("service-gallery-track");
  const counter = el("service-gallery-counter");
  track.innerHTML = "";
  images.forEach((url) => {
    const img = document.createElement("img");
    img.src = url;
    img.alt = "";
    img.loading = "lazy";
    track.appendChild(img);
  });
  el("service-gallery-view").classList.toggle("hidden", images.length === 0);
  counter.classList.toggle("hidden", images.length < 2);
  counter.textContent = `1 / ${images.length}`;
  track.onscroll = () => {
    const index = Math.round(track.scrollLeft / Math.max(1, track.clientWidth));
    counter.textContent = `${Math.min(images.length, index + 1)} / ${images.length}`;
  };
  showScreen("service");
  track.scrollLeft = 0;
}

el("service-page-book").addEventListener("click", () => {
  if (state.servicePage) selectService(state.servicePage);
});

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
    toast(e.message);
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
      <span class="chev">${icon("chevron-right")}</span>
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
  backBtn.innerHTML = `${icon("arrow-left")}Назад`;
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
      toast("Укажите номер телефона");
      return;
    }
    if (phone && !consent) {
      toast("Отметьте согласие на обработку персональных данных");
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
    toast(e.message);
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
  loadShare("share");
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
  ["admin-tab-orders", "admin-tab-clients", "admin-tab-stats", "admin-tab-services", "admin-tab-masters", "admin-tab-theme", "admin-tab-schedule", "admin-tab-settings"].forEach((id) => {
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
          ${order.client_phone ? `<div class="order-meta">${icon("phone")}${escapeHtml(order.client_phone)}</div>` : ""}
          <div class="order-meta">${order.price * order.quantity} ₽</div>
        </div>
        <span class="status-badge status-${order.status}">${statusLabels[order.status] || order.status}</span>
      </div>
      ${order.comment ? `<div class="order-comment">${icon("message")}${escapeHtml(order.comment)}</div>` : ""}
      <div class="order-actions">
        ${order.status === "new" ? `<button class="btn-confirm" data-id="${order.id}" data-action="confirmed">Подтвердить</button>` : ""}
        ${(order.status === "new" || order.status === "confirmed") && order.date && order.time ? `<button class="btn-move" data-id="${order.id}">Перенести</button>` : ""}
        <button class="btn-done" data-id="${order.id}" data-action="done">Выполнено</button>
        <button class="btn-cancel" data-id="${order.id}" data-action="cancelled">Отменить</button>
      </div>
    `;
    list.appendChild(card);
  });

  list.querySelectorAll("[data-action]").forEach((btn) => {
    btn.addEventListener("click", () => updateOrderStatus(btn.dataset.id, btn.dataset.action));
  });
  list.querySelectorAll(".btn-move").forEach((btn) => {
    btn.addEventListener("click", () => openAdminReschedule(orders.find((o) => String(o.id) === btn.dataset.id)));
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
// АДМИН-РЕЖИМ: статистика
// ======================================================================

const stats = { period: 30, metric: "revenue", data: null, request: 0 };
const SVG_NS = "http://www.w3.org/2000/svg";
const WEEKDAYS_SHORT = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"];

function svgEl(name, attrs, parent) {
  const node = document.createElementNS(SVG_NS, name);
  Object.entries(attrs || {}).forEach(([key, value]) => node.setAttribute(key, value));
  if (parent) parent.appendChild(node);
  return node;
}

function compactNumber(n) {
  const fmt = (value, unit) => `${(Math.round(value * 10) / 10).toString().replace(".", ",")} ${unit}`;
  if (n >= 1e6) return fmt(n / 1e6, "млн");
  if (n >= 1000) return fmt(n / 1000, "тыс.");
  return String(n);
}

// «Круглая» верхняя граница оси и шаг делений: 1, 2, 2.5, 5 × 10^k; для счётчиков шаг не меньше 1.
function niceScale(maxValue, integer) {
  if (maxValue <= 0) return { max: integer ? 4 : 1000, step: integer ? 1 : 250 };
  const rough = maxValue / 4;
  const pow = Math.pow(10, Math.floor(Math.log10(rough)));
  let unit = ([1, 2, 2.5, 5, 10].find((u) => u * pow >= rough) || 10) * pow;
  if (integer) unit = Math.max(1, Math.ceil(unit));
  return { max: Math.ceil(maxValue / unit) * unit, step: unit };
}

function bucketTitle(iso, bucket) {
  const date = new Date(`${iso}T00:00:00`);
  if (bucket === "month") {
    const text = date.toLocaleDateString("ru-RU", { month: "long", year: "numeric" });
    return text.charAt(0).toUpperCase() + text.slice(1);
  }
  const day = formatDateLabel(iso).top;
  return bucket === "week" ? `Неделя с ${day}` : day;
}

function bucketTick(iso, bucket) {
  const date = new Date(`${iso}T00:00:00`);
  if (bucket === "month") return `${date.toLocaleDateString("ru-RU", { month: "short" }).replace(".", "")} ${String(date.getFullYear()).slice(2)}`;
  return formatDateLabel(iso).top.replace(".", "");
}

// ----- подсказка над столбиком (один элемент на всё приложение; текст только через textContent) -----
function chartTip() {
  let tip = document.getElementById("chart-tip");
  if (!tip) {
    tip = document.createElement("div");
    tip.id = "chart-tip";
    tip.setAttribute("role", "status");
    tip.classList.add("hidden");
    document.body.appendChild(tip);
  }
  return tip;
}

function showChartTip(anchorX, anchorY, title, rows) {
  const tip = chartTip();
  tip.innerHTML = "";
  const head = document.createElement("div");
  head.className = "tip-title";
  head.textContent = title;
  tip.appendChild(head);
  rows.forEach((row) => {
    const line = document.createElement("div");
    line.className = "tip-row";
    if (row.color) {
      const key = document.createElement("span");
      key.className = "tip-key";
      key.style.background = row.color;
      line.appendChild(key);
    }
    const value = document.createElement("b");
    value.textContent = row.value;
    line.appendChild(value);
    if (row.name) {
      const name = document.createElement("span");
      name.className = "tip-name";
      name.textContent = row.name;
      line.appendChild(name);
    }
    tip.appendChild(line);
  });
  tip.classList.remove("hidden");
  const box = tip.getBoundingClientRect();
  const left = Math.min(window.innerWidth - box.width - 8, Math.max(8, anchorX - box.width / 2));
  const above = anchorY - box.height - 10;
  tip.style.left = `${left}px`;
  tip.style.top = `${above >= 8 ? above : anchorY + 14}px`;
}

function hideChartTip() {
  chartTip().classList.add("hidden");
}

const CHART_COLORS = { main: "var(--primary)", gray: "color-mix(in srgb, var(--text) 30%, var(--surface))" };

// ----- столбчатый график: один ряд или стопка из нескольких; bars: [{tick, title, values: [..]}] -----
function columnChart(host, o) {
  host.innerHTML = "";
  const W = Math.max(240, host.clientWidth || 320);
  const padL = 36, padR = 6, padT = 18, axisH = 22, plotH = 150;
  const plotW = W - padL - padR;
  const totals = o.bars.map((b) => b.values.reduce((a, v) => a + v, 0));
  const peak = Math.max(0, ...totals);
  const { max, step } = niceScale(peak, o.integer);
  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${padT + plotH + axisH}`, width: "100%", class: "chart-svg", role: "img",
    "aria-label": o.label }, host);
  const yOf = (v) => padT + plotH - (v / max) * plotH;

  for (let v = 0; v <= max + 1e-9; v += step) {
    svgEl("line", { x1: padL, x2: W - padR, y1: yOf(v), y2: yOf(v), class: "grid-line" }, svg);
    const label = svgEl("text", { x: padL - 6, y: yOf(v) + 3, "text-anchor": "end", class: "axis-text" }, svg);
    label.textContent = o.yFormat(v);
  }

  const n = o.bars.length;
  const slot = plotW / n;
  const barW = Math.min(24, Math.max(3, slot - 2));
  const groups = [];
  const maxIndex = peak > 0 ? totals.indexOf(peak) : -1;
  o.bars.forEach((bar, i) => {
    const g = svgEl("g", { class: "bar-g" }, svg);
    groups.push(g);
    const x = padL + i * slot + (slot - barW) / 2;
    let used = 0;
    const live = bar.values.map((v, k) => ({ v, k })).filter((s) => s.v > 0);
    live.forEach((seg, order) => {
      const isTop = order === live.length - 1;
      let h = Math.max(2, (seg.v / max) * plotH);
      const y = padT + plotH - used - h;
      used += h;
      if (!isTop) h -= 2; // 2px просвета цвета поверхности между сегментами стопки
      const cls = o.series[seg.k].cls || (o.emphasizeMax && i !== maxIndex ? "bar-quiet" : "bar-main");
      if (isTop) {
        const r = Math.min(4, barW / 2, h);
        svgEl("path", { d: `M${x} ${y + h}V${y + r}Q${x} ${y} ${x + r} ${y}H${x + barW - r}Q${x + barW} ${y} ${x + barW} ${y + r}V${y + h}Z`,
          class: cls }, g);
      } else {
        svgEl("rect", { x, y: y + 2, width: barW, height: Math.max(1, h), class: cls }, g);
      }
    });
  });

  if (maxIndex >= 0) { // подписываем только самый высокий столбец; остальное в подсказке и таблице
    const cx = Math.min(W - padR - 14, Math.max(padL + 14, padL + maxIndex * slot + slot / 2));
    const label = svgEl("text", { x: cx, y: yOf(peak) - 5, "text-anchor": "middle", class: "bar-label" }, svg);
    label.textContent = o.yFormat(peak);
  }

  const every = Math.ceil(n / (o.maxTicks || 6));
  o.bars.forEach((bar, i) => {
    if (i % every !== 0) return;
    const t = svgEl("text", { x: padL + i * slot + slot / 2, y: padT + plotH + 15, "text-anchor": "middle", class: "axis-text" }, svg);
    t.textContent = bar.tick;
  });

  const show = (i, clientX, clientY) => {
    groups.forEach((g, idx) => g.classList.toggle("hot", idx === i));
    const bar = o.bars[i];
    const rows = o.series.length > 1
      ? bar.values.map((v, k) => ({ value: o.valueFormat(v), name: o.series[k].name, color: o.series[k].color }))
      : [{ value: o.valueFormat(bar.values[0]), name: o.series[0].name, color: o.series[0].color }];
    showChartTip(clientX, clientY, bar.title, rows);
  };
  const hide = () => { groups.forEach((g) => g.classList.remove("hot")); hideChartTip(); };
  const indexAt = (clientX) => {
    const box = svg.getBoundingClientRect();
    const x = ((clientX - box.left) / box.width) * W - padL;
    return Math.min(n - 1, Math.max(0, Math.floor(x / slot)));
  };
  const hit = svgEl("rect", { x: padL, y: 0, width: plotW, height: padT + plotH + axisH, fill: "transparent" }, svg);
  const point = (e) => { const i = indexAt(e.clientX); show(i, e.clientX, e.clientY - 4); };
  hit.addEventListener("pointermove", point);
  hit.addEventListener("pointerdown", point);
  hit.addEventListener("pointerleave", hide);
  hit.addEventListener("pointercancel", hide);

  // клавиатура: каждый столбец получает фокус и показывает ту же подсказку
  o.bars.forEach((bar, i) => {
    const k = svgEl("rect", { x: padL + i * slot, y: padT, width: slot, height: plotH, class: "kbd", tabindex: 0,
      "aria-label": `${bar.title}: ${bar.values.map((v, j) => `${o.series[j].name} ${o.valueFormat(v)}`).join(", ")}` }, svg);
    k.addEventListener("focus", () => {
      const box = k.getBoundingClientRect();
      show(i, box.left + box.width / 2, box.top);
    });
    k.addEventListener("blur", hide);
  });
}

// ----- горизонтальные полосы (услуги, мастера): значение у конца полосы, одна краска -----
function hbarChart(host, rows, empty) {
  host.innerHTML = "";
  if (!rows.length) {
    const p = document.createElement("p");
    p.className = "hint";
    p.textContent = empty;
    host.appendChild(p);
    return;
  }
  const peak = Math.max(...rows.map((r) => r.revenue), 1);
  rows.forEach((row) => {
    const wrap = document.createElement("div");
    wrap.setAttribute("role", "img");
    wrap.setAttribute("aria-label", `${row.name}: ${moneyText(row.revenue)}, записей ${row.count}`);
    const top = document.createElement("div");
    top.className = "hbar-row-top";
    const name = document.createElement("span");
    name.className = "hbar-name";
    name.textContent = row.name;
    const value = document.createElement("span");
    value.className = "hbar-value";
    value.textContent = moneyText(row.revenue);
    const sub = document.createElement("span");
    sub.className = "hbar-sub";
    sub.textContent = ` · ${row.count} зап.`;
    value.appendChild(sub);
    top.append(name, value);
    const track = document.createElement("div");
    track.className = "hbar-track";
    const fill = document.createElement("div");
    fill.className = "hbar-fill";
    fill.style.width = `${Math.max(1, (row.revenue / peak) * 100)}%`;
    track.appendChild(fill);
    wrap.append(top, track);
    host.appendChild(wrap);
  });
}

// ----- таблица-двойник графика -----
function fillTable(card, headers, rows) {
  const host = card.querySelector(".table-view");
  host.innerHTML = "";
  const table = document.createElement("table");
  const head = table.createTHead().insertRow();
  headers.forEach((h) => { const th = document.createElement("th"); th.textContent = h; head.appendChild(th); });
  const body = table.createTBody();
  rows.forEach((cells) => {
    const tr = body.insertRow();
    cells.forEach((c) => { tr.insertCell().textContent = c; });
  });
  host.appendChild(table);
}

document.querySelectorAll("[data-table-toggle]").forEach((btn) => {
  btn.addEventListener("click", () => {
    const card = btn.closest(".stat-card");
    const showTable = card.querySelector(".table-view").classList.contains("hidden");
    card.querySelector(".table-view").classList.toggle("hidden", !showTable);
    card.querySelector(".chart-view").classList.toggle("hidden", showTable);
    btn.textContent = showTable ? "График" : "Таблицей";
    btn.setAttribute("aria-pressed", String(showTable));
    hideChartTip();
    if (!showTable && stats.data) renderStats(stats.data); // график был скрыт: перерисовываем по реальной ширине
  });
});

// ----- плитки с главными цифрами -----
function deltaText(current, previous, upIsGood, unit) {
  if (!previous) return { text: "нет данных за прошлый период", cls: "" };
  const diff = unit === "pp" ? Math.round(current - previous) : Math.round(((current - previous) / previous) * 100);
  if (diff === 0) return { text: "без изменений", cls: "" };
  const sign = diff > 0 ? "+" : "−";
  const text = `${sign}${Math.abs(diff)}${unit === "pp" ? " п.п." : "%"} к прошлому периоду`;
  return { text, cls: (diff > 0) === upIsGood ? "good" : "bad" };
}

function renderTiles(data) {
  const t = data.totals, p = data.previous;
  const tiles = [
    ["Выручка", moneyText(t.revenue), deltaText(t.revenue, p.revenue, true, "pct")],
    ["Записей", String(t.bookings), deltaText(t.bookings, p.bookings, true, "pct")],
    ["Средний чек", moneyText(t.avg_check), deltaText(t.avg_check, p.avg_check, true, "pct")],
    ["Отмены", `${String(t.cancel_rate).replace(".", ",")}%`,
      p.bookings + p.cancelled ? deltaText(t.cancel_rate, p.cancel_rate, false, "pp") : { text: "нет данных за прошлый период", cls: "" }],
  ];
  const host = el("stats-tiles");
  host.innerHTML = "";
  tiles.forEach(([label, value, delta]) => {
    const tile = document.createElement("div");
    tile.className = "stat-tile";
    const l = document.createElement("div"); l.className = "stat-tile-label"; l.textContent = label;
    const v = document.createElement("div"); v.className = "stat-tile-value"; v.textContent = value;
    const d = document.createElement("div"); d.className = `stat-tile-delta ${delta.cls}`; d.textContent = delta.text;
    tile.append(l, v, d);
    host.appendChild(tile);
  });
}

function recordsWord(n) {
  if (n % 10 === 1 && n % 100 !== 11) return "запись";
  if (n % 10 >= 2 && n % 10 <= 4 && !(n % 100 >= 12 && n % 100 <= 14)) return "записи";
  return "записей";
}

const PERIOD_NAMES = { 7: "за 7 дней", 30: "за 30 дней", 90: "за 3 месяца", 365: "за год" };

function renderStats(data) {
  stats.data = data;
  const t = data.totals;
  const empty = t.bookings === 0 && t.cancelled === 0 && data.upcoming.count === 0;
  el("stats-basis").textContent = "Выручка считается по ценам услуг и только по состоявшимся записям: отмеченным «Выполнено» "
    + "и подтверждённым, время которых уже прошло. Реальные оплаты, скидки и чаевые здесь не видны.";
  el("stats-empty").classList.toggle("hidden", !empty);
  el("stats-cards").classList.toggle("hidden", empty && !data.upcoming.count);
  renderTiles(data);

  const up = el("stats-upcoming");
  up.classList.toggle("hidden", data.upcoming.count === 0);
  up.textContent = data.upcoming.count
    ? `Ожидается: ${data.upcoming.count} ${recordsWord(data.upcoming.count)} на ${moneyText(data.upcoming.revenue)} (будущие, не отменённые).`
    : "";

  const bucket = data.bucket;
  const periodName = PERIOD_NAMES[data.period];
  const money = (v) => moneyText(v);

  // выручка / записи
  const isRevenue = stats.metric === "revenue";
  el("stats-revenue-title").textContent = isRevenue ? `Выручка ${periodName}` : `Записи ${periodName}`;
  const revBars = data.series.map((b) => ({ tick: bucketTick(b.date, bucket), title: bucketTitle(b.date, bucket),
    values: [isRevenue ? b.revenue : b.bookings] }));
  const revCard = el("stats-card-revenue");
  if (!revCard.querySelector(".chart-view").classList.contains("hidden")) {
    columnChart(el("stats-revenue-chart"), {
      bars: revBars, series: [{ name: isRevenue ? "Выручка" : "Записей", color: CHART_COLORS.main }],
      integer: !isRevenue, yFormat: compactNumber, valueFormat: isRevenue ? money : String,
      label: `${isRevenue ? "Выручка" : "Записи"} ${periodName}: ${isRevenue ? money(t.revenue) : t.bookings}`,
    });
  }
  fillTable(revCard, [bucket === "day" ? "День" : bucket === "week" ? "Неделя" : "Месяц", "Выручка", "Записей"],
    data.series.map((b) => [bucketTitle(b.date, bucket), money(b.revenue), String(b.bookings)]));

  // клиенты: новые и вернувшиеся (стопка из двух рядов, легенда обязательна)
  const c = data.clients;
  const total = c.new + c.returning;
  el("stats-clients-note").textContent = total
    ? `Клиентов за период: ${total}. Пришли впервые: ${c.new}, уже бывали раньше: ${c.returning} (${Math.round((c.returning / total) * 100)}%).`
    : "За период клиентов не было.";
  const clientSeries = [{ name: "Впервые", color: CHART_COLORS.main, cls: "bar-main" },
    { name: "Уже бывали", color: CHART_COLORS.gray, cls: "bar-gray" }];
  const clientCard = el("stats-card-clients");
  if (!clientCard.querySelector(".chart-view").classList.contains("hidden")) {
    columnChart(el("stats-clients-chart"), {
      bars: data.series.map((b) => ({ tick: bucketTick(b.date, bucket), title: bucketTitle(b.date, bucket),
        values: [b.new_clients, b.returning_clients] })),
      series: clientSeries, integer: true, yFormat: compactNumber, valueFormat: String,
      label: `Клиенты ${periodName}: впервые ${c.new}, уже бывали ${c.returning}`,
    });
  }
  const legend = el("stats-clients-legend");
  legend.innerHTML = "";
  clientSeries.forEach((s) => {
    const item = document.createElement("span");
    const swatch = document.createElement("i");
    swatch.style.background = s.color;
    item.append(swatch, document.createTextNode(s.name));
    legend.appendChild(item);
  });
  fillTable(clientCard, ["Период", "Впервые", "Уже бывали"],
    data.series.map((b) => [bucketTitle(b.date, bucket), String(b.new_clients), String(b.returning_clients)]));

  // когда загружено
  const loadCard = el("stats-card-load");
  const hoursUsed = data.hours.map((v, h) => (v ? h : -1)).filter((h) => h >= 0);
  const from = hoursUsed.length ? Math.max(0, Math.min(...hoursUsed) - 0) : 9;
  const to = hoursUsed.length ? Math.min(23, Math.max(...hoursUsed)) : 18;
  const hourRange = Array.from({ length: to - from + 1 }, (_, i) => from + i);
  if (!loadCard.querySelector(".chart-view").classList.contains("hidden")) {
    columnChart(el("stats-weekday-chart"), {
      bars: data.weekdays.map((v, i) => ({ tick: WEEKDAYS_SHORT[i], title: ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"][i], values: [v] })),
      series: [{ name: "Записей", color: CHART_COLORS.main }], integer: true, yFormat: compactNumber, valueFormat: String,
      emphasizeMax: true, maxTicks: 7, label: "Записи по дням недели",
    });
    columnChart(el("stats-hours-chart"), {
      bars: hourRange.map((h) => ({ tick: `${h}`, title: `${String(h).padStart(2, "0")}:00`, values: [data.hours[h]] })),
      series: [{ name: "Записей", color: CHART_COLORS.main }], integer: true, yFormat: compactNumber, valueFormat: String,
      emphasizeMax: true, maxTicks: 8, label: "Записи по времени начала",
    });
  }
  fillTable(loadCard, ["День / час", "Записей"], [
    ...data.weekdays.map((v, i) => [["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"][i], String(v)]),
    ...hourRange.map((h) => [`${String(h).padStart(2, "0")}:00`, String(data.hours[h])]),
  ]);

  // услуги и мастера
  hbarChart(el("stats-services-chart"), data.services, "Состоявшихся записей за период нет.");
  fillTable(el("stats-card-services"), ["Услуга", "Выручка", "Записей"], data.services.map((s) => [s.name, money(s.revenue), String(s.count)]));
  const showMasters = data.use_masters && data.masters.length > 0;
  el("stats-card-masters").classList.toggle("hidden", !showMasters);
  if (showMasters) {
    hbarChart(el("stats-masters-chart"), data.masters, "Нет данных.");
    fillTable(el("stats-card-masters"), ["Мастер", "Выручка", "Записей"], data.masters.map((m) => [m.name, money(m.revenue), String(m.count)]));
  }
}

async function loadStats() {
  const mine = ++stats.request;
  const body = el("stats-body");
  body.classList.add("loading"); // старый график остаётся на месте, пока грузится новый: без скелетона и скачков
  try {
    const data = await api(`/api/admin/stats?${qs({ ...adminAuth(), period: stats.period })}`);
    if (mine !== stats.request) return;
    renderStats(data);
  } catch (e) {
    toast(e.message);
  } finally {
    if (mine === stats.request) body.classList.remove("loading");
  }
}

el("admin-tab-stats").addEventListener("click", () => {
  applyTheme(state.config.theme);
  setActiveAdminTab("admin-tab-stats");
  showAdminScreen("stats");
  loadStats();
});

document.querySelectorAll(".period-pill").forEach((pill) => {
  pill.addEventListener("click", () => {
    document.querySelectorAll(".period-pill").forEach((p) => p.classList.remove("active"));
    pill.classList.add("active");
    stats.period = Number(pill.dataset.period);
    loadStats();
  });
});

[["stats-metric-revenue", "revenue"], ["stats-metric-bookings", "bookings"]].forEach(([id, metric]) => {
  el(id).addEventListener("click", () => {
    stats.metric = metric;
    el("stats-metric-revenue").classList.toggle("active", metric === "revenue");
    el("stats-metric-bookings").classList.toggle("active", metric === "bookings");
    el("stats-metric-revenue").setAttribute("aria-pressed", String(metric === "revenue"));
    el("stats-metric-bookings").setAttribute("aria-pressed", String(metric === "bookings"));
    if (stats.data) renderStats(stats.data);
  });
});

let statsResizeTimer = null;
window.addEventListener("resize", () => {
  clearTimeout(statsResizeTimer);
  statsResizeTimer = setTimeout(() => {
    if (stats.data && !el("admin-screen-stats").classList.contains("hidden")) renderStats(stats.data);
  }, 200);
});

// ======================================================================
// АДМИН-РЕЖИМ: перенос записи
// ======================================================================

let adminRescheduleBooking = null;

async function openAdminReschedule(order) {
  if (!order) return;
  adminRescheduleBooking = order;
  el("admin-reschedule-info").textContent =
    `${order.service_name}, ${order.client_name || "без имени"}${order.master_name ? `, ${order.master_name}` : ""}. Сейчас: ${formatDateLabel(order.date).top} в ${order.time}.`;
  let dates;
  try {
    dates = await api(`/api/dates?${qs({ business_id: state.businessId })}`);
  } catch (e) {
    toast(e.message);
    return;
  }
  el("admin-reschedule-date").innerHTML = dates.map((d) => {
    const { top, weekday } = formatDateLabel(d);
    return `<option value="${d}"${d === order.date ? " selected" : ""}>${weekday}, ${top}</option>`;
  }).join("");
  await refreshAdminRescheduleSlots();
  showAdminScreen("reschedule");
}

async function refreshAdminRescheduleSlots() {
  const select = el("admin-reschedule-time");
  const date = el("admin-reschedule-date").value;
  select.innerHTML = "";
  if (!date || !adminRescheduleBooking) return;
  let slots = [];
  try {
    slots = await api(`/api/admin/bookings/${adminRescheduleBooking.id}/slots?${qs({ ...adminAuth(), date })}`);
  } catch (e) {
    toast(e.message);
  }
  select.innerHTML = slots.map((t) => `<option value="${t}">${t}</option>`).join("");
  if (slots.includes(adminRescheduleBooking.time) && date === adminRescheduleBooking.date) select.value = adminRescheduleBooking.time;
  el("admin-reschedule-no-slots").classList.toggle("hidden", slots.length > 0);
}

el("admin-reschedule-date").addEventListener("change", refreshAdminRescheduleSlots);

el("admin-reschedule-save").addEventListener("click", async () => {
  const time = el("admin-reschedule-time").value;
  if (!adminRescheduleBooking || !time) {
    toast("Выберите время");
    return;
  }
  const btn = el("admin-reschedule-save");
  btn.disabled = true;
  try {
    await api(`/api/admin/bookings/${adminRescheduleBooking.id}/reschedule`, {
      method: "POST",
      body: JSON.stringify({ ...adminAuth(), date: el("admin-reschedule-date").value, time }),
    });
    toast("Запись перенесена");
    showAdminScreen("orders");
    loadAdminOrders();
  } catch (e) {
    toast(e.message);
    refreshAdminRescheduleSlots();
  } finally {
    btn.disabled = false;
  }
});

// ======================================================================
// АДМИН-РЕЖИМ: клиенты
// ======================================================================

function moneyText(value) {
  return `${Number(value || 0).toLocaleString("ru-RU")} ₽`;
}

function shortDate(isoDate) {
  const { top } = formatDateLabel(isoDate);
  return top;
}

function visitsWord(n) {
  if (n % 10 === 1 && n % 100 !== 11) return "визит";
  if (n % 10 >= 2 && n % 10 <= 4 && !(n % 100 >= 12 && n % 100 <= 14)) return "визита";
  return "визитов";
}

let clientsRequest = 0;

async function loadAdminClients() {
  const query = el("clients-search").value.trim();
  const mine = ++clientsRequest;
  let clients;
  try {
    clients = await api(`/api/admin/clients?${qs({ ...adminAuth(), q: query })}`);
  } catch (e) {
    toast(e.message);
    return;
  }
  if (mine !== clientsRequest) return; // пришёл ответ на уже устаревший поиск
  state.admin.clients = clients;
  const list = el("clients-list");
  list.innerHTML = "";
  const empty = el("no-clients-msg");
  empty.textContent = query ? "Никого не нашли." : "Клиентов пока нет: они появятся после первой записи.";
  empty.classList.toggle("hidden", clients.length > 0);

  clients.forEach((client) => {
    const parts = [`${client.visits} ${visitsWord(client.visits)}`];
    if (client.spent) parts.push(moneyText(client.spent));
    if (client.last_visit) parts.push(`был ${shortDate(client.last_visit)}`);
    if (client.next_booking) parts.push(`ждём ${shortDate(client.next_booking.slice(0, 10))}, ${client.next_booking.slice(11)}`);
    const card = document.createElement("div");
    card.className = "service-admin-card";
    card.innerHTML = `
      <div>
        <div class="title">${escapeHtml(client.name)}</div>
        <div class="badge-type">${escapeHtml(parts.join(" · "))}</div>
      </div>
      <span class="edit-mark">${icon("chevron-right")}</span>
    `;
    card.addEventListener("click", () => openClient(client.id));
    list.appendChild(card);
  });
}

el("admin-tab-clients").addEventListener("click", () => {
  applyTheme(state.config.theme);
  setActiveAdminTab("admin-tab-clients");
  showAdminScreen("clients");
  loadAdminClients();
});

let clientsSearchTimer = null;
el("clients-search").addEventListener("input", () => {
  clearTimeout(clientsSearchTimer);
  clientsSearchTimer = setTimeout(loadAdminClients, 250);
});

async function openClient(id) {
  let client;
  try {
    client = await api(`/api/admin/clients/${id}?${qs(adminAuth())}`);
  } catch (e) {
    toast(e.message);
    return;
  }
  state.admin.clientId = id;
  state.admin.openedClient = client;
  el("client-title").textContent = client.name;
  el("client-name").value = client.name;
  el("client-phone").value = client.phone || "";
  el("client-notes").value = client.notes || "";

  const stats = [
    [client.bookings_count, "записей"],
    [client.visits, "выполнено"],
    [moneyText(client.spent), "на сумму"],
    [client.last_visit ? shortDate(client.last_visit) : "—", "последний визит"],
  ];
  el("client-stats").innerHTML = stats
    .map(([value, label]) => `<div class="client-stat"><b>${escapeHtml(String(value))}</b><span>${label}</span></div>`)
    .join("");

  const tg = el("client-tg");
  tg.classList.toggle("hidden", !client.tg_id);
  tg.innerHTML = client.tg_id ? `<a href="tg://user?id=${Number(client.tg_id)}">Открыть чат в Telegram</a>` : "";

  const history = el("client-history");
  history.innerHTML = "";
  client.history.forEach((b) => {
    const card = document.createElement("div");
    card.className = "order-card";
    const when = b.date ? `${shortDate(b.date)} в ${b.time}` : "разовый заказ";
    card.innerHTML = `
      <div class="order-top">
        <div>
          <div class="order-title">${escapeHtml(b.service_name)}</div>
          <div class="order-meta">${escapeHtml(when)}${b.master_name ? ` · ${escapeHtml(b.master_name)}` : ""} · ${moneyText(b.price * b.quantity)}</div>
        </div>
        <span class="status-badge status-${escapeHtml(b.status)}">${statusLabels[b.status] || escapeHtml(b.status)}</span>
      </div>
      ${b.comment ? `<div class="order-comment">${icon("message")}${escapeHtml(b.comment)}</div>` : ""}
    `;
    history.appendChild(card);
  });
  showAdminScreen("client");
}

el("save-client-btn").addEventListener("click", async () => {
  const name = el("client-name").value.trim();
  if (!name) {
    toast("Укажите имя клиента");
    return;
  }
  const btn = el("save-client-btn");
  btn.disabled = true;
  try {
    await api(`/api/admin/clients/${state.admin.clientId}`, {
      method: "PUT",
      body: JSON.stringify({
        ...adminAuth(),
        name,
        phone: el("client-phone").value.trim() || null,
        notes: el("client-notes").value.trim() || null,
      }),
    });
    toast("Сохранено");
    await openClient(state.admin.clientId); // перечитываем: телефон приводится к единому виду
  } catch (e) {
    toast(e.message);
  } finally {
    btn.disabled = false;
  }
});

el("client-book-btn").addEventListener("click", () => {
  const client = state.admin.openedClient;
  if (client) openManualForm({ id: client.id, name: el("client-name").value.trim() || client.name, phone: client.phone });
});

// ======================================================================
// АДМИН-РЕЖИМ: ручная запись владельцем
// ======================================================================

const manualMaster = () => (el("manual-master-block").classList.contains("hidden") ? null : Number(el("manual-master").value) || null);

el("add-manual-btn").addEventListener("click", () => openManualForm());

async function openManualForm(client) {
  let services;
  try {
    services = (await api(`/api/admin/services?${qs(adminAuth())}`)).filter((s) => s.type === "slot");
  } catch (e) {
    toast(e.message);
    return;
  }
  if (services.length === 0) {
    toast("Сначала добавьте услугу по расписанию");
    return;
  }
  const select = el("manual-service");
  select.innerHTML = services.map((s) => `<option value="${s.id}">${escapeHtml(s.name)}${s.is_active ? "" : " (скрыта)"}</option>`).join("");
  ["manual-name", "manual-phone", "manual-comment"].forEach((id) => { el(id).value = ""; });
  state.admin.manualClientId = null;
  await loadManualClientHints();
  if (client && client.id) {
    state.admin.manualClientId = client.id;
    el("manual-name").value = client.name;
    el("manual-phone").value = client.phone || "";
  }

  const dates = await api(`/api/dates?${qs({ business_id: state.businessId })}`);
  el("manual-date").innerHTML = dates.map((d) => {
    const { top, weekday } = formatDateLabel(d);
    return `<option value="${d}">${weekday}, ${top}</option>`;
  }).join("");

  await refreshManualMasters();
  showAdminScreen("manual");
}

async function refreshManualMasters() {
  const serviceId = Number(el("manual-service").value);
  const block = el("manual-master-block");
  const select = el("manual-master");
  let masters = [];
  try {
    const data = await api(`/api/admin/masters?${qs(adminAuth())}`);
    if (data.use_masters) {
      masters = data.masters.filter((m) => m.is_active && (m.service_ids.length === 0 || m.service_ids.includes(serviceId)));
    }
  } catch (e) {
    toast(e.message);
  }
  select.innerHTML = masters.map((m) => `<option value="${m.id}">${escapeHtml(m.name)}</option>`).join("");
  block.classList.toggle("hidden", masters.length === 0);
  await refreshManualSlots();
}

async function refreshManualSlots() {
  const timeSelect = el("manual-time");
  const date = el("manual-date").value;
  timeSelect.innerHTML = "";
  if (!date) return;
  let slots = [];
  try {
    slots = await api(`/api/slots?${qs({
      service_id: el("manual-service").value,
      date,
      business_id: state.businessId,
      master_id: manualMaster(),
    })}`);
  } catch (e) {
    toast(e.message);
  }
  timeSelect.innerHTML = slots.map((t) => `<option value="${t}">${t}</option>`).join("");
  el("manual-no-slots").classList.toggle("hidden", slots.length > 0);
}

// Подсказки имён из базы клиентов: выбранный клиент подставляет телефон и привязывает к нему запись.
const clientLabel = (c) => (c.phone ? `${c.name} · ${c.phone}` : c.name);

async function loadManualClientHints() {
  let clients = [];
  try {
    clients = await api(`/api/admin/clients?${qs(adminAuth())}`);
  } catch (e) { /* подсказки не обязательны */ }
  state.admin.clients = clients;
  const list = el("manual-clients");
  list.innerHTML = "";
  clients.forEach((c) => {
    const option = document.createElement("option");
    option.value = clientLabel(c);
    list.appendChild(option);
  });
}

el("manual-name").addEventListener("input", (e) => {
  const picked = state.admin.clients.find((c) => clientLabel(c) === e.target.value);
  if (picked) {
    state.admin.manualClientId = picked.id;
    e.target.value = picked.name;
    el("manual-phone").value = picked.phone || "";
    return;
  }
  const chosen = state.admin.clients.find((c) => c.id === state.admin.manualClientId);
  if (chosen && chosen.name !== e.target.value) state.admin.manualClientId = null; // имя поменяли: это уже другой человек
});

el("manual-service").addEventListener("change", refreshManualMasters);
el("manual-master").addEventListener("change", refreshManualSlots);
el("manual-date").addEventListener("change", refreshManualSlots);

el("save-manual-btn").addEventListener("click", async () => {
  const name = el("manual-name").value.trim();
  const time = el("manual-time").value;
  if (!name) {
    toast("Укажите имя клиента");
    return;
  }
  if (!time) {
    toast("Выберите время");
    return;
  }
  const btn = el("save-manual-btn");
  btn.disabled = true;
  try {
    await api("/api/admin/bookings", {
      method: "POST",
      body: JSON.stringify({
        ...adminAuth(),
        service_id: Number(el("manual-service").value),
        master_id: manualMaster(),
        date: el("manual-date").value,
        time,
        client_name: name,
        client_id: state.admin.manualClientId,
        client_phone: el("manual-phone").value.trim() || null,
        comment: el("manual-comment").value.trim() || null,
      }),
    });
    toast("Запись добавлена");
    showAdminScreen("orders");
    loadAdminOrders();
  } catch (e) {
    toast(e.message);
    refreshManualSlots(); // время могли занять, пока форма была открыта
  } finally {
    btn.disabled = false;
  }
});

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
      <span class="edit-mark">${icon("pencil")}</span>
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
  el("service-active").checked = service ? !!service.is_active : true;
  state.admin.serviceImages = ((service && service.images) || [])
    .filter((i) => safeMediaUrl(i.url))
    .map((i) => ({ id: i.id, url: i.url }));
  renderServiceGallery();
  el("service-share-btn").classList.toggle("hidden", !service);
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

// ----- галерея фото услуги (до 5): первое фото = обложка в списке -----

const MAX_SERVICE_PHOTOS = 5;

function renderServiceGallery() {
  const box = el("service-gallery");
  box.innerHTML = "";
  state.admin.serviceImages.forEach((image, index) => {
    const url = safeMediaUrl(image.url);
    if (!url) return;
    const thumb = document.createElement("div");
    thumb.className = "gallery-thumb";
    thumb.innerHTML = `
      <img src="${url}" alt="" />
      ${index === 0 ? `<span class="thumb-main">Главное</span>` : ""}
      <div class="thumb-actions">
        ${index === 0 ? "" : `<button type="button" class="thumb-first">Главное</button>`}
        <button type="button" class="thumb-remove">Убрать</button>
      </div>
    `;
    const first = thumb.querySelector(".thumb-first");
    if (first) first.addEventListener("click", () => {
      state.admin.serviceImages.unshift(...state.admin.serviceImages.splice(index, 1));
      renderServiceGallery();
    });
    thumb.querySelector(".thumb-remove").addEventListener("click", () => {
      state.admin.serviceImages.splice(index, 1);
      renderServiceGallery();
    });
    box.appendChild(thumb);
  });
  el("service-photo-btn").classList.toggle("hidden", state.admin.serviceImages.length >= MAX_SERVICE_PHOTOS);
}

el("service-photo-btn").addEventListener("click", () => el("service-photo-file").click());

el("service-photo-file").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  e.target.value = "";
  if (!file || state.admin.serviceImages.length >= MAX_SERVICE_PHOTOS) return;
  const btn = el("service-photo-btn");
  const label = btn.textContent;
  btn.textContent = "Загружаю…";
  btn.disabled = true;
  try {
    const uploaded = await uploadImage(file, { maxSide: 900, quality: 0.82 });
    state.admin.serviceImages.push({ id: uploaded.id, url: uploaded.url });
    renderServiceGallery();
  } catch (err) {
    toast(err.message);
  } finally {
    btn.textContent = label;
    btn.disabled = false;
  }
});

// Ссылка на конкретную услугу: клиент попадает в бота, и ему приходит кнопка, открывающая страницу этой услуги.
el("service-share-btn").addEventListener("click", async () => {
  const id = state.admin.editingServiceId;
  if (!id) return;
  try {
    if (!state.admin.botUsername) {
      const data = await api(`/api/admin/share?${qs(adminAuth())}`);
      state.admin.botUsername = data.bot_username;
    }
    if (!state.admin.botUsername) {
      toast("Не удалось определить бота, попробуйте позже");
      return;
    }
    const link = `https://t.me/${state.admin.botUsername}?start=svc_${id}`;
    toast((await copyText(link)) ? "Ссылка скопирована" : link);
  } catch (e) {
    toast(e.message);
  }
});

el("type-slot").addEventListener("click", () => setServiceType("slot"));
el("type-order").addEventListener("click", () => setServiceType("order"));

el("save-service-btn").addEventListener("click", async () => {
  const name = el("service-name").value.trim();
  const price = parseInt(el("service-price").value, 10);
  const type = el("service-form-title").dataset.type || "slot";
  const duration = type === "slot" ? parseInt(el("service-duration").value || "0", 10) : 0;

  if (!name || isNaN(price) || price < 0) {
    toast("Заполни название и укажи цену не меньше нуля");
    return;
  }
  if (type === "slot" && (isNaN(duration) || duration <= 0)) {
    toast("Укажи длительность в минутах для позиции по расписанию");
    return;
  }

  const payload = {
    name, price, duration_min: duration, type,
    description: el("service-desc").value.trim() || null,
    image_id: state.admin.serviceImages.length ? state.admin.serviceImages[0].id : null,
    image_ids: state.admin.serviceImages.map((i) => i.id),
    is_active: el("service-active").checked, init_data: state.initData, owner_tg_id: state.myTgId, business_id: state.businessId,
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
    toast(e.message);
  }
});

el("delete-service-btn").addEventListener("click", async () => {
  if (!state.admin.editingServiceId) return;
  if (!(await askConfirm("Удалить эту позицию? Это действие необратимо.", "Удалить"))) return;
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
      <span class="edit-mark">${icon("pencil")}</span>
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
    toast(err.message);
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
    toast("Укажите имя мастера");
    return;
  }
  const boxes = [...el("master-services-box").querySelectorAll("input[type=checkbox]")];
  const serviceIds = boxes.filter((b) => b.checked).map((b) => Number(b.value));
  if (boxes.length > 0 && serviceIds.length === 0) {
    toast("Отметьте хотя бы одну услугу");
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
    toast(e.message);
  }
});

el("delete-master-btn").addEventListener("click", async () => {
  if (!state.admin.editingMasterId) return;
  if (!(await askConfirm("Удалить мастера? Уже созданные записи к нему останутся в истории.", "Удалить"))) return;
  try {
    await api(`/api/admin/masters/${state.admin.editingMasterId}?${qs(adminAuth())}`, { method: "DELETE" });
    showAdminScreen("masters");
    loadAdminMasters();
  } catch (e) {
    toast(e.message);
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
      toast("Загрузите картинку для фона или выберите другой тип фона");
      return;
    }
    const result = await api("/api/admin/theme", { method: "PUT", body: JSON.stringify(payload) });
    state.config.theme = result.theme;
    applyTheme(result.theme);
    flashSaved(el("save-theme-btn"), "Сохранить оформление");
  } catch (e) {
    toast(e.message);
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
      <button type="button" class="link-btn" title="Удалить" aria-label="Удалить">${icon("x")}</button>`;
    row.addEventListener("click", () => {
      fillThemeForm(saved.theme);
      previewTheme();
    });
    row.querySelector(".link-btn").addEventListener("click", async (e) => {
      e.stopPropagation();
      if (!(await askConfirm(`Удалить вариант «${saved.name}»?`, "Удалить"))) return;
      try {
        await api(`/api/admin/themes/saved/${saved.id}?${qs(adminAuth())}`, { method: "DELETE" });
      } catch (err) {
        toast(err.message);
      }
      loadSavedThemes();
    });
    box.appendChild(row);
  });
}

el("save-variant-btn").addEventListener("click", async () => {
  const name = el("saved-theme-name").value.trim();
  if (!name) {
    toast("Введите название варианта");
    return;
  }
  const payload = { ...readThemeForm(), name };
  if (payload.bg_mode === "image" && !payload.bg_image_id) {
    toast("Загрузите картинку для фона или выберите другой тип фона");
    return;
  }
  try {
    await api("/api/admin/themes/saved", { method: "POST", body: JSON.stringify(payload) });
    el("saved-theme-name").value = "";
    loadSavedThemes();
  } catch (e) {
    toast(e.message);
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
  if (step === 3) loadShare("wizard-share");
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
      <span class="tile">${icon(niche.icon)}</span>
      <div class="card-body">
        <div class="title">${escapeHtml(niche.title)}</div>
        <div class="meta">${niche.services.length} ${pluralRu(niche.services.length, ["услуга", "услуги", "услуг"])} в примере</div>
      </div>
      <span class="chev">${icon("chevron-right")}</span>
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
    toast(e.message);
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
    toast("Заполните часы работы и количество дней");
    return;
  }
  if (payload.work_end_hour <= payload.work_start_hour) {
    toast("Время закрытия должно быть позже времени открытия");
    return;
  }
  try {
    await api("/api/admin/schedule", { method: "PUT", body: JSON.stringify(payload) });
    showWizardStep(3);
  } catch (e) {
    toast(e.message);
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

// ---- Ссылка и QR для клиентов (вкладка «Настройки» и последний шаг мастера) ----

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (e) { /* в некоторых WebView буфер недоступен — пробуем запасной путь */ }
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.style.position = "fixed";
  area.style.opacity = "0";
  document.body.appendChild(area);
  area.select();
  let ok = false;
  try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
  area.remove();
  return ok;
}

async function loadShare(prefix) {
  const linkInput = el(`${prefix}-link`);
  const copyBtn = el(`${prefix}-copy-btn`);
  const sendBtn = el(`${prefix}-send-btn`);
  const qr = document.getElementById(`${prefix}-qr`);
  const fail = () => { linkInput.value = ""; linkInput.placeholder = "Не удалось получить ссылку"; };
  try {
    const data = await api(`/api/admin/share?${qs(adminAuth())}`);
    if (!data.link) { fail(); return; }
    linkInput.value = data.link;
    copyBtn.disabled = false;
    sendBtn.disabled = false;
    // Адрес картинки строит сервер; перед подстановкой в src проверяем формат (как safeMediaUrl).
    if (qr && /^\/api\/qr\?business_id=\d+$/.test(data.qr_url || "")) {
      qr.src = API_BASE + data.qr_url;
      qr.classList.remove("hidden");
    }
  } catch (e) {
    fail();
  }
}

function bindShare(prefix) {
  el(`${prefix}-copy-btn`).addEventListener("click", async () => {
    const input = el(`${prefix}-link`);
    const ok = await copyText(input.value);
    if (!ok) {
      // Буфер недоступен — выделяем ссылку целиком, чтобы её можно было скопировать долгим нажатием.
      input.focus();
      input.select();
      input.setSelectionRange(0, input.value.length);
    }
    toast(ok ? "Ссылка скопирована" : "Не получилось скопировать сразу: ссылка выделена, скопируйте её вручную");
  });
  el(`${prefix}-send-btn`).addEventListener("click", async () => {
    const btn = el(`${prefix}-send-btn`);
    btn.disabled = true;
    try {
      await api("/api/admin/share/send", { method: "POST", body: JSON.stringify(adminAuth()) });
      toast("Отправили QR-код и ссылку вам в чат с ботом");
    } catch (e) {
      toast(e.message);
    } finally {
      btn.disabled = false;
    }
  });
}

bindShare("share");
bindShare("wizard-share");

el("bot-refresh-btn").addEventListener("click", async () => {
  const btn = el("bot-refresh-btn");
  btn.disabled = true;
  try {
    const res = await api("/api/admin/bot/refresh", { method: "POST", body: JSON.stringify(adminAuth()) });
    toast(res.ok ? "Настройки бота обновлены" : "Часть настроек бота обновить не удалось, попробуйте позже");
  } catch (e) {
    toast(e.message);
  } finally {
    btn.disabled = false;
  }
});

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
    flashSaved(el("save-settings-btn"), "Сохранить настройки");
  } catch (e) {
    toast(e.message);
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
  el("schedule-step").value = schedule.slot_step_minutes;
  el("schedule-days").value = schedule.days_ahead;
  // <input type="time"> не умеет «24:00»: работу «до полуночи» показываем как 23:59.
  state.admin.weekly = schedule.weekly.map((d) => ({
    ...d,
    end: d.end === "24:00" ? "23:59" : d.end,
    break_end: d.break_end === "24:00" ? "23:59" : d.break_end,
  }));
  renderWeekly();
}

// ---- График по дням недели: у каждого дня свой выходной / часы / перерыв ----

const WEEKDAY_NAMES = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"];
const DEFAULT_BREAK = { start: "13:00", end: "14:00" };

function timeInput(value, onChange, label) {
  const input = document.createElement("input");
  input.type = "time";
  input.value = value || "";
  input.setAttribute("aria-label", label);
  input.addEventListener("change", () => onChange(input.value));
  return input;
}

function renderWeekly() {
  const box = el("weekly-editor");
  box.innerHTML = "";
  state.admin.weekly.forEach((day, index) => {
    const card = document.createElement("div");
    card.className = "day-card" + (day.open ? "" : " closed");

    const head = document.createElement("label");
    head.className = "day-head";
    const toggle = document.createElement("input");
    toggle.type = "checkbox";
    toggle.checked = day.open;
    toggle.addEventListener("change", () => { day.open = toggle.checked; renderWeekly(); });
    const name = document.createElement("span");
    name.className = "day-name";
    name.textContent = WEEKDAY_NAMES[index];
    const status = document.createElement("span");
    status.className = "day-status";
    status.textContent = day.open ? "работаю" : "выходной";
    head.append(toggle, name, status);
    card.append(head);

    if (day.open) {
      const hours = document.createElement("div");
      hours.className = "time-row";
      const from = document.createElement("span");
      from.textContent = "с";
      const to = document.createElement("span");
      to.textContent = "до";
      hours.append(
        from, timeInput(day.start, (v) => { day.start = v; }, `${WEEKDAY_NAMES[index]}: начало`),
        to, timeInput(day.end, (v) => { day.end = v; }, `${WEEKDAY_NAMES[index]}: конец`),
      );
      card.append(hours);

      const hasBreak = !!(day.break_start && day.break_end);
      const breakLabel = document.createElement("label");
      breakLabel.className = "break-toggle";
      const breakBox = document.createElement("input");
      breakBox.type = "checkbox";
      breakBox.checked = hasBreak;
      breakBox.addEventListener("change", () => {
        if (breakBox.checked) {
          // Предлагаем обед 13:00–14:00, но сдвигаем, если он не помещается в рабочие часы.
          const inside = (t) => day.start && day.end && t >= day.start && t <= day.end;
          day.break_start = inside(DEFAULT_BREAK.start) ? DEFAULT_BREAK.start : day.start;
          day.break_end = inside(DEFAULT_BREAK.end) ? DEFAULT_BREAK.end : day.end;
        } else {
          day.break_start = null;
          day.break_end = null;
        }
        renderWeekly();
      });
      const breakText = document.createElement("span");
      breakText.textContent = "Перерыв";
      breakLabel.append(breakBox, breakText);
      card.append(breakLabel);

      if (hasBreak) {
        const breakRow = document.createElement("div");
        breakRow.className = "time-row break-row";
        const dash = document.createElement("span");
        dash.textContent = "—";
        breakRow.append(
          timeInput(day.break_start, (v) => { day.break_start = v; }, `${WEEKDAY_NAMES[index]}: начало перерыва`),
          dash,
          timeInput(day.break_end, (v) => { day.break_end = v; }, `${WEEKDAY_NAMES[index]}: конец перерыва`),
        );
        card.append(breakRow);
      }
    }
    box.append(card);
  });
}

el("weekly-copy-btn").addEventListener("click", () => {
  const monday = state.admin.weekly[0];
  state.admin.weekly = state.admin.weekly.map(() => ({ ...monday }));
  renderWeekly();
  toast("Понедельник скопирован на все дни");
});

el("save-schedule-btn").addEventListener("click", async () => {
  const payload = {
    business_id: state.businessId,
    init_data: state.initData,
    owner_tg_id: state.myTgId,
    timezone: el("schedule-timezone").value,
    weekly: state.admin.weekly.map((d) => ({
      open: d.open, start: d.start, end: d.end,
      break_start: d.break_start || null, break_end: d.break_end || null,
    })),
    slot_step_minutes: parseInt(el("schedule-step").value, 10),
    days_ahead: parseInt(el("schedule-days").value, 10),
  };

  if (isNaN(payload.days_ahead) || payload.days_ahead < 1) {
    toast("Укажите, на сколько дней вперёд открыта запись");
    return;
  }

  try {
    await api("/api/admin/schedule", { method: "PUT", body: JSON.stringify(payload) });
    flashSaved(el("save-schedule-btn"), "Сохранить расписание");
  } catch (e) {
    toast(e.message);
  }
});

// ======================================================================
// СТАРТ
// ======================================================================

function showLoadError() {
  el("load-error").classList.remove("hidden");
  el("client-mode").classList.add("hidden");
  el("brand-name").textContent = "TeleSlot";
}

el("load-retry-btn").addEventListener("click", () => location.reload());

async function init() {
  try {
    state.config = await api(`/api/config?${qs({ business_id: state.businessId })}`);
  } catch (e) {
    showLoadError();
    return;
  }
  state.businessId = state.config.business_id; // синхронизируем с тем, что реально отдал бэкенд (если в URL параметра не было)
  applyTheme(state.config.theme);
  applyBrand(state.config.business_name, state.config.logo_url);

  const user = tg.initDataUnsafe && tg.initDataUnsafe.user;
  state.myTgId = user ? user.id : null;
  state.isOwner = !!(state.myTgId && state.config.owner_tg_id && state.myTgId === state.config.owner_tg_id);

  setupModeSwitch();
  setupSuspendedBanner();
  try {
    await loadServices();
  } catch (e) {
    showLoadError();
    return;
  }
  showScreen("services");
  await maybeStartWizard();

  // Ссылка на конкретную услугу (?service=ID приходит из бота по ссылке t.me/<бот>?start=svc_ID): открываем её страницу.
  const wantedService = Number(urlParams.get("service"));
  if (wantedService && !state.config.suspended && !state.admin.wizardActive) {
    const found = state.services.find((s) => s.id === wantedService);
    if (found) openService(found);
  }

  // Запуск из кабинета TeleSlot: ссылка вида ?business_id=N&admin=1&from=cabinet.
  if (urlParams.get("from") === "cabinet" && state.isOwner) setupCabinetBack();
  if (urlParams.get("admin") === "1" && state.isOwner && !state.admin.wizardActive) switchMode("admin");
}

// Баннер владельцу, если запись приостановлена за неоплату. Кабинет открывается в чате с платформенным ботом
// (другой бот — другая подпись), поэтому ведём в этот чат, а не по ссылке /cabinet.
function setupSuspendedBanner() {
  if (!state.config.suspended || !state.isOwner) return;
  el("suspended-banner").classList.remove("hidden");
  el("suspended-open-btn").addEventListener("click", () => {
    const link = state.config.platform_link;
    if (link && tg.openTelegramLink) tg.openTelegramLink(link);
    else toast("Откройте бота TeleSlot в Telegram и зайдите в «Кабинет»");
  });
}

// Кнопка «назад в кабинет»: и системная кнопка Telegram, и видимая ссылка (на старых клиентах BackButton нет).
function setupCabinetBack() {
  el("back-to-cabinet").classList.remove("hidden");
  try {
    tg.BackButton.show();
    tg.BackButton.onClick(() => { location.href = "/cabinet"; });
  } catch (e) { /* не критично: ссылка в интерфейсе остаётся */ }
}

init();
