/* ============================================================================
   Умный календарь — travel.js: поиск билетов
   Без сборки: подключается из base.html <script defer> после core.js.
   ============================================================================ */
(function () {
  "use strict";

  // Общие хелперы из core.js (ARCH-05: единые esc/fmt без дублей).
  const api = window.api;
  const toast = window.toast;
  const uiConfirm = window.uiConfirm;
  const showFormError = window.showFormError;
  const openModalEl = window.openModalEl;
  const closeModalEl = window.closeModalEl;
  const { esc, pad, toLocalInput, fmtDateTime, fmtTime, fmtChatDate, spinner, clearFormError, emitEventChanged } = window.smartcal;

  const escHtml = esc; // ARCH-05: единый esc

  /* =====================================================================
     Поиск билетов (travel)
     ===================================================================== */
  const travelForm = document.getElementById("travel-form");
  if (travelForm) {
    const results = document.getElementById("travel-results");
    const countEl = document.getElementById("travel-count");
    const modeIcon = { plane: "✈️", train: "🚆", bus: "🚌" };
    const modeRu = { plane: "Авиа", train: "ЖД", bus: "Автобус" };
    const todayIso = () => {
      const d = new Date();
      return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
    };

    // UX-16: быстрые маршруты сразу запускают поиск.
    document.querySelectorAll("[data-route]").forEach((b) => {
      b.addEventListener("click", () => {
        const [from, to] = b.getAttribute("data-route").split("|");
        travelForm.elements["origin"].value = from;
        travelForm.elements["destination"].value = to;
        travelForm.requestSubmit();
      });
    });

    // UX-16: «⇄» меняет города местами.
    const swapBtn = document.getElementById("travel-swap");
    if (swapBtn) {
      swapBtn.addEventListener("click", () => {
        const o = travelForm.elements["origin"], d = travelForm.elements["destination"];
        const tmp = o.value; o.value = d.value; d.value = tmp;
      });
    }

    // UX-16: min-атрибуты на датах (не в прошлое; обратно не раньше «туда»).
    const dateInput = travelForm.elements["date"];
    const returnInput = travelForm.elements["return_date"];
    if (dateInput) {
      dateInput.min = todayIso();
      const syncReturnMin = () => { if (returnInput) returnInput.min = dateInput.value || todayIso(); };
      dateInput.addEventListener("change", syncReturnMin);
      syncReturnMin();
    }

    // UX-16: запоминаем последний маршрут.
    const ROUTE_KEY = "smartcal-travel-route";
    try {
      const saved = JSON.parse(localStorage.getItem(ROUTE_KEY) || "null");
      if (saved && saved.origin) {
        travelForm.elements["origin"].value = saved.origin;
        travelForm.elements["destination"].value = saved.destination || "";
        if (saved.transport) travelForm.elements["transport"].value = saved.transport;
      }
    } catch (e) { /* повреждённый localStorage — игнорируем */ }

    function showTravelError(message) {
      countEl.textContent = "";
      results.innerHTML = `<div class="alert error">${escHtml(message)}</div>`;
    }

    function sourceCard(s) {
      const mode = modeRu[s.mode] || s.mode || "Билеты";
      const ret = s.return_date ? ` · обратно ${escHtml(s.return_date)}` : "";
      return (
        `<div class="ticket-card source-card">` +
        `<div class="tc-mode">${modeIcon[s.mode] || "🔎"}<span>${escHtml(mode)}</span></div>` +
        `<div class="tc-main">` +
        `<div class="tc-time"><b>${escHtml(s.title || s.provider)}</b></div>` +
        `<div class="tc-sub muted">${escHtml(s.origin)} → ${escHtml(s.destination)} · ${escHtml(s.depart_date)}${ret}</div>` +
        `<div class="tc-sub muted">${escHtml(s.note || "Актуальные цены и места откроются на сайте-источнике.")}</div>` +
        `</div>` +
        `<div class="tc-price"><div class="price">${escHtml(s.provider)}</div>` +
        `<a class="btn small primary" href="${escHtml(s.url)}" target="_blank" rel="noopener">Открыть</a></div>` +
        `</div>`
      );
    }

    function card(o) {
      const h = Math.floor(o.duration_minutes / 60), m = o.duration_minutes % 60;
      const tr = o.transfers > 0 ? `пересадок: ${o.transfers}` : "без пересадок";
      const duration = o.duration_minutes > 0 ? `${h}ч ${String(m).padStart(2, "0")}м` : "длительность уточняется";
      const time = o.time_precision === "date"
        ? `<b>${fmtDateTime(o.depart_at)}</b> · время уточняется`
        : `<b>${fmtDateTime(o.depart_at)}</b> → <b>${fmtTime(o.arrive_at)}</b>`;
      const seats = o.available_seats != null ? ` · мест: ${escHtml(o.available_seats)}` : "";
      const carrier = o.carrier || o.provider || modeRu[o.mode] || o.mode;
      const action = o.url
        ? `<a class="btn small primary" href="${escHtml(o.url)}" target="_blank" rel="noopener">Купить</a>`
        : `<span class="btn small ghost disabled" aria-disabled="true">Ссылка недоступна</span>`;
      return (
        `<div class="ticket-card">` +
        `<div class="tc-mode">${modeIcon[o.mode] || "🎫"}<span>${escHtml(modeRu[o.mode] || o.mode)}</span></div>` +
        `<div class="tc-main">` +
        `<div class="tc-time">${time}</div>` +
        `<div class="tc-carrier">${escHtml(carrier)}</div>` +
        `<div class="tc-sub muted">${escHtml(o.origin)} → ${escHtml(o.destination)} · ${duration} · ${tr}${seats}</div>` +
        `<div class="tc-sub muted">provider: ${escHtml(o.provider || "—")}</div>` +
        `</div>` +
        `<div class="tc-price"><div class="price">${Math.round(o.price)} ${escHtml(o.currency)}</div>` +
        action + `</div>` +
        `</div>`
      );
    }

    travelForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const f = travelForm.elements;
      const origin = f["origin"].value.trim(), destination = f["destination"].value.trim();
      const date = f["date"].value;
      const returnDate = f["return_date"].value;
      const passengers = parseInt(f["passengers"].value, 10);
      if (!origin) return showTravelError("Укажите город отправления.");
      if (!destination) return showTravelError("Укажите город прибытия.");
      if (origin.toLocaleLowerCase("ru-RU") === destination.toLocaleLowerCase("ru-RU")) {
        return showTravelError("Город отправления и прибытия не должны совпадать.");
      }
      if (!date) return showTravelError("Укажите дату отправления.");
      if (date < todayIso()) return showTravelError("Дата отправления не может быть в прошлом.");
      if (returnDate && returnDate < date) return showTravelError("Дата возвращения не может быть раньше даты отправления.");
      if (!passengers || passengers < 1 || passengers > 9) return showTravelError("Количество пассажиров должно быть от 1 до 9.");

      try {
        localStorage.setItem("smartcal-travel-route", JSON.stringify({
          origin, destination, transport: f["transport"].value,
        }));
      } catch (e) { /* quota — не критично */ }

      const budget = parseFloat(f["budget"].value);
      const prefs = Array.from(travelForm.querySelectorAll("input[name=pref]:checked")).map((c) => c.value);
      let sort = f["sort"].value;
      if (prefs.includes("fastest")) sort = "duration";
      if (prefs.includes("cheapest")) sort = "price";
      const params = new URLSearchParams({
        origin,
        destination,
        date,
        transport: f["transport"].value,
        passengers: String(passengers),
        sort,
      });
      if (returnDate) params.set("return_date", returnDate);
      // BUG-26: бюджет и «без пересадок» фильтруются на сервере.
      if (!isNaN(budget) && budget > 0) params.set("budget", String(budget));
      if (prefs.includes("direct")) params.set("direct", "true");

      results.innerHTML = spinner("Ищу варианты…");
      countEl.textContent = "";
      try {
        const payload = await window.api("GET", "/api/tickets/search?" + params.toString());
        let data = Array.isArray(payload) ? payload : (payload.items || []);
        const sources = Array.isArray(payload) ? [] : (payload.external_searches || []);
        if (sort === "duration") data = data.slice().sort((a, b) => a.duration_minutes - b.duration_minutes);
        else if (sort === "departure") data = data.slice().sort((a, b) => new Date(a.depart_at) - new Date(b.depart_at));
        else data = data.slice().sort((a, b) => a.price - b.price);

        if (!data.length && sources.length) {
          countEl.textContent = `источников: ${sources.length}`;
          const msg = payload.message
            ? `<div class="alert info">${escHtml(payload.message)}</div>`
            : "";
          results.innerHTML = msg + sources.map(sourceCard).join("");
          return;
        }
        if (!data.length) {
          results.innerHTML = `<div class="empty-state"><div class="big">🎫</div>По заданным параметрам билетов не найдено.</div>`;
          return;
        }
        countEl.textContent = `найдено: ${data.length}`;
        results.innerHTML = data.map(card).join("") + (sources.length ? sources.map(sourceCard).join("") : "");
      } catch (err) {
        showTravelError(err.message || "API поиска билетов недоступен.");
      }
    });
  }
})();
