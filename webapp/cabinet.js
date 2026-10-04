// Кабинет TeleSlot — Mini App платформенного бота: подписка бизнесов, оплата, история, документы.
// Личность — только по подписанному Telegram initData (сервер проверяет токеном платформенного бота).
// Весь текст, пришедший с сервера (названия бизнесов вводят пользователи), вставляется только как текст
// (textContent), а не как разметка — так нельзя подсунуть чужой HTML.
(function () {
  const tg = window.Telegram && window.Telegram.WebApp;
  const initData = tg && tg.initData ? tg.initData : "";
  // Вне Telegram (локальная отладка) сервер принимает dev_tg_id только при DEV_SKIP_INITDATA_CHECK=true.
  const devId = !initData ? new URLSearchParams(location.search).get("dev_tg_id") : null;

  if (tg) {
    tg.ready();
    tg.expand();
    try { tg.setHeaderColor("#0B0F1A"); tg.setBackgroundColor("#0B0F1A"); } catch (e) { /* старые клиенты */ }
  }

  const $ = (id) => document.getElementById(id);

  function toast(message) {
    const el = $("toast");
    el.textContent = message;
    el.classList.add("show");
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => el.classList.remove("show"), 3200);
  }

  function h(tag, props, ...children) {
    const el = document.createElement(tag);
    Object.entries(props || {}).forEach(([k, v]) => {
      if (k === "class") el.className = v;
      else if (k === "text") el.textContent = v;
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else if (v !== null && v !== undefined) el.setAttribute(k, v);
    });
    children.flat().forEach((c) => { if (c) el.append(c); });
    return el;
  }

  function authQuery() {
    const q = new URLSearchParams();
    if (initData) q.set("init_data", initData);
    else if (devId) q.set("owner_tg_id", devId);
    return q.toString();
  }

  async function api(path, options) {
    const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...(options || {}) });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: "Ошибка сервера" }));
      throw new Error(err.detail || "Ошибка сервера");
    }
    return res.json();
  }

  function openTelegram(url) {
    if (tg && tg.openTelegramLink) tg.openTelegramLink(url);
    else window.open(url, "_blank", "noopener");
  }

  function openPage(url) {
    if (tg && tg.openLink) tg.openLink(location.origin + url);
    else window.open(url, "_blank", "noopener");
  }

  const rub = (n) => `${Number(n).toLocaleString("ru-RU")} ₽`;

  function statusView(b) {
    if (b.status === "pilot") return { cls: "", text: "Пилот: оплата пока не требуется" };
    if (b.status === "active") return { cls: "ok", text: `Оплачено до ${b.paid_until}` };
    if (b.status === "expiring") return { cls: "warn", text: `Заканчивается ${b.paid_until}` };
    return { cls: "bad", text: `Срок закончился ${b.paid_until}` };
  }

  function daysWord(n) {
    const m10 = n % 10, m100 = n % 100;
    if (m10 === 1 && m100 !== 11) return "день";
    if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return "дня";
    return "дней";
  }

  async function waitForUpdate(businessId, previousPaidUntil) {
    // successful_payment обрабатывается вебхуком чуть позже, чем Telegram сообщает «paid» в Mini App.
    for (let i = 0; i < 8; i++) {
      await new Promise((r) => setTimeout(r, 1500));
      try {
        const data = await api(`/api/cabinet/me?${authQuery()}`);
        const biz = data.businesses.find((x) => x.id === businessId);
        if (biz && biz.paid_until !== previousPaidUntil) { render(data); toast("Подписка продлена"); return; }
      } catch (e) { /* пробуем ещё */ }
    }
    toast("Оплата прошла, срок обновится в течение минуты");
    load();
  }

  async function pay(business, button) {
    button.disabled = true;
    try {
      const body = { business_id: business.id, init_data: initData, owner_tg_id: devId ? Number(devId) : null };
      const { link } = await api("/api/cabinet/invoice", { method: "POST", body: JSON.stringify(body) });
      if (!tg || !tg.openInvoice) { toast("Откройте кабинет в Telegram, чтобы оплатить"); button.disabled = false; return; }
      tg.openInvoice(link, (status) => {
        button.disabled = false;
        if (status === "paid") waitForUpdate(business.id, business.paid_until);
        else if (status === "failed") toast("Оплата не прошла. Попробуйте ещё раз или напишите в поддержку");
      });
    } catch (e) {
      toast(e.message);
      button.disabled = false;
    }
  }

  function businessCard(b, data) {
    const view = statusView(b);
    const card = h("div", { class: "card" },
      h("p", { class: "biz-name", text: b.name }),
      b.bot_username
        ? h("p", { class: "biz-bot" }, h("a", { href: b.link, text: `@${b.bot_username}`, onclick: (e) => { e.preventDefault(); openTelegram(b.link); } }))
        : h("p", { class: "biz-bot", text: "Бот подключается…" }),
      h("span", { class: `badge ${view.cls}`.trim(), text: view.text }),
    );
    if (b.status === "active" || b.status === "expiring") {
      card.append(h("p", { class: "meta", text: `Осталось ${b.days_left} ${daysWord(b.days_left)}` }));
    }

    // Сводка: что требует внимания прямо сейчас.
    card.append(h("div", { class: "stats" },
      h("div", { class: `stat${b.new_bookings ? " attention" : ""}` },
        h("span", { class: "stat-num", text: String(b.new_bookings) }), h("span", { class: "stat-label", text: "новых заявок" })),
      h("div", { class: "stat" },
        h("span", { class: "stat-num", text: String(b.today_bookings) }), h("span", { class: "stat-label", text: "записей сегодня" })),
    ));

    // «Управление» открывает админку этого бизнеса (заявки, услуги, расписание…) прямо здесь, в кабинете;
    // `from=cabinet` включает там кнопку «Назад в кабинет».
    card.append(h("button", { class: "btn", type: "button", text: "Управление бизнесом", onclick: () => {
      location.href = `/?business_id=${b.id}&admin=1&from=cabinet`;
    } }));

    const urgent = b.status === "expiring" || b.status === "expired";
    if (data.payments_enabled) {
      const label = b.status === "pilot"
        ? `Оплатить ${rub(data.price_rub)} за ${data.days} ${daysWord(data.days)}`
        : `Продлить на ${data.days} ${daysWord(data.days)}: ${rub(data.price_rub)}`;
      const btn = h("button", { class: urgent ? "btn urgent" : "btn ghost", type: "button", text: label });
      btn.addEventListener("click", () => pay(b, btn));
      card.append(btn);
    } else {
      card.append(h("p", { class: "meta", style: "margin-top:10px", text: `Онлайн-оплата скоро заработает. Пока оплатить можно через поддержку: ${data.support}` }));
    }
    if (b.link) {
      card.append(h("button", { class: "btn ghost", type: "button", text: "Перейти в бота", onclick: () => openTelegram(b.link) }));
    }
    return card;
  }

  function render(data) {
    $("loading").classList.add("hidden");
    $("error").classList.add("hidden");
    $("content").classList.remove("hidden");

    const list = $("businesses");
    list.replaceChildren();
    if (!data.businesses.length) {
      list.append(h("div", { class: "card empty" },
        h("p", { text: "У вас пока нет подключённых бизнесов." }),
        h("button", { class: "btn", type: "button", text: "Подключить бизнес", onclick: () => { if (tg) tg.close(); } }),
        h("p", { class: "hint", style: "margin-top:12px", text: "Нажмите «Подключить бизнес» в чате с ботом." }),
      ));
    } else {
      data.businesses.forEach((b) => list.append(businessCard(b, data)));
    }

    const history = $("history");
    history.replaceChildren();
    $("history-block").classList.toggle("hidden", !data.history.length);
    data.history.forEach((p) => history.append(h("div", { class: "row" },
      h("div", { class: "l" }, h("div", { text: p.business_name }), h("div", { class: "hint", text: `оплачено ${p.paid_at}, до ${p.period_to}` })),
      h("div", { class: "r", text: rub(p.amount_rub) }),
    )));

    const docs = $("docs");
    docs.replaceChildren();
    data.docs.forEach((d) => docs.append(h("a", { class: "doc", href: d.url, text: d.title, onclick: (e) => { e.preventDefault(); openPage(d.url); } })));
    const support = data.support || "";
    docs.append(h("a", { class: "doc", href: `https://t.me/${support.replace("@", "")}`, text: `Поддержка: ${support}`,
      onclick: (e) => { e.preventDefault(); openTelegram(`https://t.me/${support.replace("@", "")}`); } }));
  }

  function showError(message) {
    $("loading").classList.add("hidden");
    $("content").classList.add("hidden");
    $("error").classList.remove("hidden");
    $("error-text").textContent = message;
  }

  async function load() {
    if (!initData && !devId) {
      $("loading").classList.add("hidden");
      $("not-telegram").classList.remove("hidden");
      return;
    }
    try {
      render(await api(`/api/cabinet/me?${authQuery()}`));
    } catch (e) {
      showError(e.message);
    }
  }

  load();
})();
