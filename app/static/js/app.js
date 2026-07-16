/* ============================================================================
   Умный календарь — фронтенд-логика (vanilla JS, без сборки).
   Тема, сайдбар, тосты, API-хелпер, модалки событий и пользователей, чат.
   ============================================================================ */
(function () {
  "use strict";

  /* -------- Базовый префикс пути (reverse-proxy под под-путём) -------- */
  // Все fetch-запросы к бэкенду идут как BASE + "/api/...".
  const BASE = (window.APP_BASE || "").replace(/\/+$/, "");
  window.APP_BASE = BASE;

  /* ----------------------------- Тема ----------------------------- */
  const THEME_KEY = "smartcal-theme";
  function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    const btn = document.getElementById("theme-toggle");
    if (btn) {
      // UI-02: иконка из SVG-спрайта; fallback на эмодзи для страниц без спрайта.
      const use = btn.querySelector("use");
      if (use) use.setAttribute("href", theme === "dark" ? "#i-sun" : "#i-moon");
      else btn.textContent = theme === "dark" ? "☀️" : "🌙";
    }
  }
  window.toggleTheme = function () {
    const cur = document.documentElement.getAttribute("data-theme") || "light";
    const next = cur === "dark" ? "light" : "dark";
    localStorage.setItem(THEME_KEY, next);
    applyTheme(next);
  };
  // UI-08: тема уже выставлена инлайн-скриптом (localStorage или системная).
  applyTheme(document.documentElement.getAttribute("data-theme") || "light");

  /* --------------------------- Сайдбар --------------------------- */
  const SIDEBAR_KEY = "smartcal-sidebar-collapsed";
  const sidebarMq = window.matchMedia("(max-width: 860px)");

  function updateSidebarButton() {
    const btn = document.getElementById("sidebar-toggle");
    if (!btn) return;
    const collapsed = document.documentElement.classList.contains("sidebar-collapsed");
    // Иконка «меню» из спрайта постоянна — обновляем только aria-атрибуты.
    btn.setAttribute("aria-label", sidebarMq.matches ? "Меню" : (collapsed ? "Развернуть меню" : "Свернуть меню"));
    btn.setAttribute("aria-expanded", sidebarMq.matches ? "true" : String(!collapsed));
  }

  function applySidebarCollapsed(collapsed) {
    document.documentElement.classList.toggle("sidebar-collapsed", collapsed);
    localStorage.setItem(SIDEBAR_KEY, collapsed ? "1" : "0");
    updateSidebarButton();
  }

  window.toggleSidebar = function () {
    const sb = document.querySelector(".sidebar");
    if (sidebarMq.matches) {
      if (sb) sb.classList.toggle("open");
      return;
    }
    applySidebarCollapsed(!document.documentElement.classList.contains("sidebar-collapsed"));
  };
  applySidebarCollapsed(localStorage.getItem(SIDEBAR_KEY) === "1");
  sidebarMq.addEventListener("change", updateSidebarButton);

  /* ------------------- Тосты (UX-15: a11y и управление) ------------------- */
  function toast(msg, kind) {
    let wrap = document.querySelector(".toast-wrap");
    if (!wrap) {
      wrap = document.createElement("div");
      wrap.className = "toast-wrap";
      wrap.setAttribute("role", "status");
      wrap.setAttribute("aria-live", "polite");
      document.body.appendChild(wrap);
    }
    const el = document.createElement("div");
    el.className = "toast " + (kind === "err" ? "err" : "ok");
    const text = document.createElement("span");
    text.textContent = msg;
    el.appendChild(text);
    const close = document.createElement("button");
    close.className = "toast-close";
    close.setAttribute("aria-label", "Закрыть уведомление");
    close.textContent = "×";
    el.appendChild(close);
    wrap.appendChild(el);
    // Ошибки живут дольше; hover ставит таймер на паузу.
    let timer = setTimeout(() => el.remove(), kind === "err" ? 6000 : 3200);
    close.addEventListener("click", () => { clearTimeout(timer); el.remove(); });
    el.addEventListener("mouseenter", () => clearTimeout(timer));
    el.addEventListener("mouseleave", () => { timer = setTimeout(() => el.remove(), 1600); });
  }
  window.toast = toast;

  /* ------- Модальный хелпер (BUG-11/UX-10): Esc, focus trap, возврат фокуса ------- */
  const FOCUSABLE =
    'a[href], button:not([disabled]), input:not([disabled]):not([type=hidden]), ' +
    'select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

  function openModalEl(backdrop) {
    if (!backdrop) return;
    backdrop.classList.add("open");
    const modal = backdrop.querySelector(".modal") || backdrop;
    modal.setAttribute("role", "dialog");
    modal.setAttribute("aria-modal", "true");
    const heading = modal.querySelector("h1, h2, h3");
    if (heading) {
      if (!heading.id) heading.id = "modal-title-" + Math.random().toString(36).slice(2, 8);
      modal.setAttribute("aria-labelledby", heading.id);
    }
    const trigger = document.activeElement;
    const first = modal.querySelector(FOCUSABLE);
    if (first) first.focus();
    const onKey = (e) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        closeModalEl(backdrop);
      } else if (e.key === "Tab") {
        const items = Array.from(modal.querySelectorAll(FOCUSABLE)).filter((n) => n.offsetParent !== null);
        if (!items.length) return;
        const firstEl = items[0], lastEl = items[items.length - 1];
        if (e.shiftKey && document.activeElement === firstEl) { e.preventDefault(); lastEl.focus(); }
        else if (!e.shiftKey && document.activeElement === lastEl) { e.preventDefault(); firstEl.focus(); }
      }
    };
    document.addEventListener("keydown", onKey);
    backdrop.__modalState = { trigger, onKey };
  }

  function closeModalEl(backdrop) {
    if (!backdrop) return;
    backdrop.classList.remove("open");
    const state = backdrop.__modalState;
    if (state) {
      document.removeEventListener("keydown", state.onKey);
      if (state.trigger && typeof state.trigger.focus === "function") state.trigger.focus();
      backdrop.__modalState = null;
    }
  }
  window.openModalEl = openModalEl;
  window.closeModalEl = closeModalEl;

  /* ----------------- Единый confirm-диалог (UX-13) ----------------- */
  let confirmModal = null;
  function uiConfirm(text, opts) {
    opts = opts || {};
    if (!confirmModal) {
      confirmModal = document.createElement("div");
      confirmModal.className = "modal-backdrop confirm-backdrop";
      confirmModal.innerHTML =
        '<div class="modal glass confirm-modal" role="dialog" aria-modal="true">' +
        '<div class="confirm-icon">⚠️</div>' +
        '<div class="confirm-text"></div>' +
        '<div class="modal-foot">' +
        '<button type="button" class="btn ghost" data-role="cancel">Отмена</button>' +
        '<button type="button" class="btn primary" data-role="ok">Подтвердить</button>' +
        "</div></div>";
      document.body.appendChild(confirmModal);
    }
    const okBtn = confirmModal.querySelector("[data-role=ok]");
    const cancelBtn = confirmModal.querySelector("[data-role=cancel]");
    confirmModal.querySelector(".confirm-text").textContent = text || "Вы уверены?";
    okBtn.className = "btn " + (opts.danger ? "danger" : "primary");
    okBtn.textContent = opts.okLabel || (opts.danger ? "Удалить" : "Подтвердить");
    confirmModal.classList.add("open");
    okBtn.focus();
    return new Promise((resolve) => {
      const done = (result) => {
        confirmModal.classList.remove("open");
        okBtn.onclick = cancelBtn.onclick = confirmModal.onclick = null;
        document.removeEventListener("keydown", onKey);
        resolve(result);
      };
      const onKey = (e) => { if (e.key === "Escape") done(false); };
      okBtn.onclick = () => done(true);
      cancelBtn.onclick = () => done(false);
      confirmModal.onclick = (e) => { if (e.target === confirmModal) done(false); };
      document.addEventListener("keydown", onKey);
    });
  }
  window.uiConfirm = uiConfirm;

  /* -------------- Инлайн-ошибки форм в модалках (UX-12) -------------- */
  function showFormError(form, message) {
    if (!form) { toast(message, "err"); return; }
    let box = form.querySelector(".form-error");
    if (!box) {
      box = document.createElement("div");
      box.className = "alert error form-error";
      const foot = form.querySelector(".modal-foot");
      if (foot) form.insertBefore(box, foot);
      else form.appendChild(box);
    }
    box.textContent = message;
    box.style.display = "block";
    box.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }
  function clearFormError(form) {
    const box = form && form.querySelector(".form-error");
    if (box) box.style.display = "none";
  }
  window.showFormError = showFormError;

  /* ------- Событие «данные календаря изменились» (BUG-10) ------- */
  // Страница календаря перерисовывается без reload; остальные страницы
  // (серверный рендер) откатываются на перезагрузку.
  function emitEventChanged() {
    window.dispatchEvent(new CustomEvent("smartcal:event-changed"));
    if (!window.__smartcalCalendarLive) {
      setTimeout(() => location.reload(), 400);
    }
  }

  /* --------------------------- API-хелпер --------------------------- */
  async function api(method, url, body, signal) {
    const opts = { method, headers: {} };
    if (signal) opts.signal = signal;
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    // Префиксуем относительные пути базовым префиксом (reverse-proxy).
    const target = url.charAt(0) === "/" ? BASE + url : url;
    const res = await fetch(target, opts);
    if (res.status === 204) return null;
    let data = null;
    try { data = await res.json(); } catch (e) { /* no body */ }
    if (!res.ok) {
      const detail = (data && (data.detail || JSON.stringify(data))) || res.statusText;
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return data;
  }
  window.api = api;

  function pad(n) { return String(n).padStart(2, "0"); }
  function toLocalInput(d) {
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  /* =====================================================================
     Модалка события (создание/редактирование)
     ===================================================================== */
  const eventModal = document.getElementById("event-modal");
  if (eventModal) {
    const f = eventModal.querySelector("form");
    const titleEl = document.getElementById("event-modal-title");
    const deleteBtn = eventModal.querySelector("[data-role=delete]");
    const submitBtn = f.querySelector("button[type=submit]");
    const ownerNote = document.getElementById("event-owner-note");

    function canEditEvent(data, editing) {
      // Сервер присылает явный can_edit; иначе выводим из владельца события.
      if (data.can_edit === false) return false;
      if (data.can_edit === true || !editing) return true;
      const viewer = window.APP_USER || null;
      if (!viewer || data.owner_id == null) return true;
      return viewer.is_admin || String(data.owner_id) === String(viewer.id);
    }

    function openEvent(data) {
      f.reset();
      clearFormError(f);
      data = data || {};
      const editing = !!data.id;
      const readOnly = editing && !canEditEvent(data, editing);
      const calendarContext = window.smartcalCalendarContext || {};
      const owner = data.owner || calendarContext.owner || null;
      titleEl.textContent = readOnly ? "Просмотр встречи" : editing ? "Редактирование встречи" : "Новая встреча";
      f.elements["id"].value = data.id || "";
      f.elements["owner_id"].value = data.owner_id || (owner && owner.id) || "";
      f.elements["title"].value = data.title || "";
      f.elements["description"].value = data.description || "";
      const start = data.start_at ? new Date(data.start_at) : defaultStart();
      const end = data.end_at ? new Date(data.end_at) : new Date(start.getTime() + 3600000);
      f.elements["start_at"].value = toLocalInput(start);
      f.elements["end_at"].value = toLocalInput(end);
      f.elements["location_type"].value = data.location_type || "offline";
      f.elements["city"].value = data.city || "";
      f.elements["address"].value = data.address || "";
      f.elements["meeting_url"].value = data.meeting_url || "";
      f.elements["participants"].value = (data.participants || []).map((p) => p.email || p).join(", ");
      f.elements["priority"].value = data.priority != null ? data.priority : 5;
      f.elements["importance"].value = data.importance || "normal";
      f.elements["status"].value = data.status || "planned";
      // Приглашённый участник видит встречу без возможности правки (FN-01).
      Array.from(f.elements).forEach((el) => {
        if (el.name) el.disabled = readOnly;
      });
      if (submitBtn) submitBtn.style.display = readOnly ? "none" : "";
      if (ownerNote) {
        const eventOwnerName = data.owner_name || (owner && (owner.full_name || owner.email)) || "";
        if (readOnly) {
          ownerNote.style.display = "block";
          ownerNote.textContent = eventOwnerName
            ? "Вы приглашены на встречу. Организатор: " + eventOwnerName + " — только просмотр."
            : "Вы приглашены на встречу — только просмотр.";
        } else {
          const ownerName = (owner && (owner.full_name || owner.email)) || data.owner_name || "";
          const foreign = calendarContext.adminView || (owner && String(owner.id) !== String(calendarContext.viewerId || ""));
          ownerNote.style.display = foreign && ownerName ? "block" : "none";
          ownerNote.textContent = ownerName ? "Вы редактируете календарь: " + ownerName : "";
        }
      }
      deleteBtn.style.display = editing && !readOnly ? "inline-flex" : "none";
      openModalEl(eventModal);
    }
    function defaultStart() {
      const d = new Date();
      d.setMinutes(0, 0, 0);
      d.setHours(d.getHours() + 1);
      return d;
    }
    window.openEventModal = openEvent;
    window.closeEventModal = () => closeModalEl(eventModal);
    eventModal.addEventListener("click", (e) => { if (e.target === eventModal) closeModalEl(eventModal); });

    // UX-04: «Окончание» следует за «Началом», сохраняя длительность.
    const startInput = f.elements["start_at"];
    const endInput = f.elements["end_at"];
    let lastStartValue = "";
    startInput.addEventListener("focus", () => { lastStartValue = startInput.value; });
    startInput.addEventListener("change", () => {
      const prev = lastStartValue ? new Date(lastStartValue) : null;
      const next = startInput.value ? new Date(startInput.value) : null;
      const end = endInput.value ? new Date(endInput.value) : null;
      if (next && end) {
        const durationMs = prev && end > prev ? end - prev : 3600000;
        endInput.value = toLocalInput(new Date(next.getTime() + durationMs));
      }
      lastStartValue = startInput.value;
    });
    // Пресеты длительности «30м · 45м · 1ч …».
    eventModal.querySelectorAll("[data-duration]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const minutes = parseInt(btn.getAttribute("data-duration"), 10);
        const start = startInput.value ? new Date(startInput.value) : defaultStart();
        if (!startInput.value) startInput.value = toLocalInput(start);
        endInput.value = toLocalInput(new Date(start.getTime() + minutes * 60000));
      });
    });

    // UX-05: автокомплит участников по справочнику сотрудников.
    const participantsInput = f.elements["participants"];
    const suggestBox = document.getElementById("participants-suggest");
    let suggestTimer = null;
    function hideSuggest() { if (suggestBox) { suggestBox.style.display = "none"; suggestBox.innerHTML = ""; } }
    if (participantsInput && suggestBox) {
      participantsInput.addEventListener("input", () => {
        clearTimeout(suggestTimer);
        const parts = participantsInput.value.split(",");
        const term = parts[parts.length - 1].trim();
        if (term.length < 2) { hideSuggest(); return; }
        suggestTimer = setTimeout(async () => {
          try {
            const data = await api("GET", "/api/assistant/employees/search?q=" + encodeURIComponent(term));
            const items = (data.items || []).filter((emp) => participantsInput.value.indexOf(emp.email) === -1);
            if (!items.length) { hideSuggest(); return; }
            suggestBox.innerHTML = items.slice(0, 6).map((emp) =>
              `<button type="button" class="suggest-item" data-email="${emp.email}">` +
              `<b>${emp.fullName || emp.email}</b> <span class="muted">${emp.email}</span></button>`
            ).join("");
            suggestBox.style.display = "block";
            suggestBox.querySelectorAll(".suggest-item").forEach((item) => {
              item.addEventListener("click", () => {
                const before = participantsInput.value.split(",").slice(0, -1).map((x) => x.trim()).filter(Boolean);
                before.push(item.getAttribute("data-email"));
                participantsInput.value = before.join(", ") + ", ";
                hideSuggest();
                participantsInput.focus();
              });
            });
          } catch (e) { hideSuggest(); }
        }, 200);
      });
      participantsInput.addEventListener("blur", () => setTimeout(hideSuggest, 200));
    }

    f.addEventListener("submit", async (e) => {
      e.preventDefault();
      clearFormError(f);
      const id = f.elements["id"].value;
      const payload = {
        title: f.elements["title"].value.trim(),
        description: f.elements["description"].value.trim(),
        start_at: f.elements["start_at"].value,
        end_at: f.elements["end_at"].value,
        location_type: f.elements["location_type"].value,
        city: f.elements["city"].value.trim(),
        address: f.elements["address"].value.trim(),
        meeting_url: f.elements["meeting_url"].value.trim(),
        priority: parseInt(f.elements["priority"].value, 10),
        importance: f.elements["importance"].value,
      };
      const ownerId = f.elements["owner_id"].value;
      if (ownerId) payload.owner_id = parseInt(ownerId, 10);
      const participants = f.elements["participants"].value
        .split(",")
        .map((x) => x.trim())
        .filter(Boolean);
      payload.participants = participants;
      try {
        if (id) {
          payload.status = f.elements["status"].value;
          await api("PATCH", `/api/events/${id}`, payload);
          toast("Встреча обновлена");
        } else {
          await api("POST", "/api/events", payload);
          toast("Встреча создана");
        }
        closeModalEl(eventModal);
        emitEventChanged();
      } catch (err) {
        showFormError(f, err.message);
      }
    });

    deleteBtn.addEventListener("click", async () => {
      const id = f.elements["id"].value;
      if (!id) return;
      const ok = await uiConfirm("Удалить встречу без возможности восстановления?", { danger: true });
      if (!ok) return;
      try {
        await api("DELETE", `/api/events/${id}`);
        toast("Встреча удалена");
        closeModalEl(eventModal);
        emitEventChanged();
      } catch (err) { showFormError(f, err.message); }
    });

    // BUG-11: Enter/Space работают как клик на «кнопочных» элементах.
    function activateOnKeydown(node) {
      node.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          node.click();
        }
      });
    }

    function bindEventTriggers(root) {
      root = root || document;
      root.querySelectorAll("[data-event]").forEach((node) => {
        if (node.dataset.eventBound) return;
        node.dataset.eventBound = "1";
        node.addEventListener("click", () => {
          try { openEvent(JSON.parse(node.getAttribute("data-event"))); }
          catch (e) { /* ignore */ }
        });
        if (node.getAttribute("role") === "button") activateOnKeydown(node);
      });
      root.querySelectorAll("[data-new-event]").forEach((node) => {
        if (node.dataset.newEventBound) return;
        node.dataset.newEventBound = "1";
        node.addEventListener("click", () => {
          const preset = {};
          const day = node.getAttribute("data-day");
          const time = node.getAttribute("data-time") || "10:00";
          const calendarContext = window.smartcalCalendarContext || {};
          if (calendarContext.owner) {
            preset.owner_id = calendarContext.owner.id;
            preset.owner = calendarContext.owner;
          }
          if (day) {
            const s = new Date(day + "T" + time);
            preset.start_at = s.toISOString();
            preset.end_at = new Date(s.getTime() + 3600000).toISOString();
          }
          openEvent(preset);
        });
      });
    }
    window.bindEventModalTriggers = bindEventTriggers;
    bindEventTriggers(document);
  }

  /* =====================================================================
     Календарь: Day / Week / Month без перезагрузки страницы
     ===================================================================== */
  const calendarBody = document.getElementById("calendar-body");
  if (calendarBody) {
    const initialNode = document.getElementById("calendar-initial");
    const rangeEl = document.getElementById("calendar-range");
    const subtitleEl = document.getElementById("calendar-subtitle");
    const viewButtons = document.querySelectorAll("[data-cal-view]");
    const navButtons = document.querySelectorAll("[data-cal-nav]");
    const VIEW_KEY = "smartcal-calendar-view";
    const HOUR_HEIGHT = 48;
    const DOW = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
    let calState = JSON.parse(initialNode.textContent || "{}");

    const escCal = (s) =>
      String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
        ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
    const loadingCal = (label) => `<div class="loading"><span class="spinner"></span> ${escCal(label || "Загрузка…")}</div>`;
    const dateAtNoon = (iso) => new Date(iso + "T12:00:00");
    const ymd = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
    const addDays = (iso, days) => {
      const d = dateAtNoon(iso);
      d.setDate(d.getDate() + days);
      return ymd(d);
    };
    const dayLabel = (iso) => dateAtNoon(iso).toLocaleDateString("ru-RU", { day: "2-digit", month: "short" });
    const fullDateLabel = (iso) => dateAtNoon(iso).toLocaleDateString("ru-RU", { weekday: "long", day: "2-digit", month: "long" });
    const eventDate = (iso) => new Date(iso);
    const eventTime = (iso) => {
      const d = eventDate(iso);
      return isNaN(d) ? "" : d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
    };
    const priorityClass = (p) => (p >= 9 ? "critical" : p >= 7 ? "high" : p >= 4 ? "mid" : "low");
    const locLabel = (e) => e.location_type === "online" ? "Онлайн" : e.location_type === "hybrid" ? "Гибрид" : "Офлайн";

    function eventPayload(e) {
      return escCal(JSON.stringify({
        id: e.id,
        title: e.title,
        description: e.description || "",
        start_at: e.start_at,
        end_at: e.end_at,
        location_type: e.location_type,
        city: e.city || "",
        address: e.address || "",
        meeting_url: e.meeting_url || "",
        priority: e.priority,
        importance: e.importance,
        owner_id: e.owner_id,
        owner_name: e.owner_name,
        participants: e.participants || [],
        status: e.status,
        is_participant: !!e.is_participant,
        can_edit: e.can_edit !== false,
      }));
    }

    function eventsForDay(dateIso) {
      const start = new Date(dateIso + "T00:00:00");
      const end = new Date(start.getTime() + 86400000);
      return (calState.events || [])
        .filter((e) => eventDate(e.start_at) < end && eventDate(e.end_at) > start)
        .sort((a, b) => eventDate(a.start_at) - eventDate(b.start_at));
    }

    function segmentsForDay(dateIso) {
      const start = new Date(dateIso + "T00:00:00");
      const end = new Date(start.getTime() + 86400000);
      const segments = eventsForDay(dateIso).map((e) => {
        const s = eventDate(e.start_at);
        const f = eventDate(e.end_at);
        const top = Math.max(0, Math.floor((Math.max(s, start) - start) / 60000));
        const bottom = Math.min(1440, Math.ceil((Math.min(f, end) - start) / 60000));
        return { event: e, startMin: top, endMin: Math.max(top + 15, bottom), lane: 0, laneCount: 1 };
      });
      const laneEnds = [];
      segments.forEach((seg) => {
        let lane = laneEnds.findIndex((endMin) => endMin <= seg.startMin);
        if (lane === -1) {
          lane = laneEnds.length;
          laneEnds.push(0);
        }
        seg.lane = lane;
        laneEnds[lane] = seg.endMin;
      });
      const laneCount = Math.max(1, laneEnds.length);
      segments.forEach((seg) => { seg.laneCount = laneCount; });
      return segments;
    }

    function hourRows() {
      return (calState.hours || []).map((h) =>
        `<div class="timegrid-hour" style="height:${HOUR_HEIGHT}px;">${String(h).padStart(2, "0")}:00</div>`
      ).join("");
    }

    // UX-01: границы рабочих часов из настроек (для затенения и автоскролла).
    function workingHoursRange() {
      const wh = calState.working_hours || {};
      const parse = (s, dflt) => {
        const m = /^(\d{1,2}):(\d{2})$/.exec(s || "");
        return m ? parseInt(m[1], 10) + parseInt(m[2], 10) / 60 : dflt;
      };
      return [parse(wh.start, 9), parse(wh.end, 19)];
    }

    function hourLines() {
      const range = workingHoursRange();
      return (calState.hours || []).map((h) =>
        `<div class="timegrid-line${h + 1 <= range[0] || h >= range[1] ? " off" : ""}" style="height:${HOUR_HEIGHT}px;"></div>`
      ).join("");
    }

    // UX-01: красная линия текущего времени в колонке «сегодня».
    function nowLineHtml(day) {
      if (!day.is_today) return "";
      const now = new Date();
      const top = Math.round((now.getHours() * 60 + now.getMinutes()) * HOUR_HEIGHT / 60);
      return `<div class="now-line" style="top:${top}px;"></div>`;
    }
    setInterval(() => {
      const now = new Date();
      const top = Math.round((now.getHours() * 60 + now.getMinutes()) * HOUR_HEIGHT / 60);
      document.querySelectorAll(".now-line").forEach((el) => { el.style.top = top + "px"; });
    }, 60000);

    function timedEvent(seg) {
      const e = seg.event;
      const top = Math.round(seg.startMin * HOUR_HEIGHT / 60);
      const height = Math.max(28, Math.round((seg.endMin - seg.startMin) * HOUR_HEIGHT / 60) - 2);
      const left = `calc(${(seg.lane * 100 / seg.laneCount).toFixed(4)}% + 3px)`;
      const width = `calc(${(100 / seg.laneCount).toFixed(4)}% - 6px)`;
      const cls = `cal-event timed-event p-${priorityClass(e.priority)} ${e.status === "cancelled" ? "cancelled" : ""} ${e.is_conflict ? "conflict" : ""} ${e.is_participant ? "invited" : ""}`;
      const invited = e.is_participant ? `<span class="ce-invited" title="Вы приглашены на эту встречу">приглашён</span>` : "";
      return (
        `<div class="${cls}" style="top:${top}px; height:${height}px; left:${left}; width:${width};" ` +
        `data-event='${eventPayload(e)}' role="button" tabindex="0" title="${escCal(e.title)}">` +
        `<div class="ce-time">${eventTime(e.start_at)}–${eventTime(e.end_at)}${invited}</div>` +
        `<div class="ce-title">${escCal(e.title)}</div>` +
        `<div class="ce-loc"><span class="dot ${escCal(e.location_type)}"></span>${escCal(locLabel(e))}${e.city ? " · " + escCal(e.city) : ""}</div>` +
        `</div>`
      );
    }

    function compactEvent(e) {
      const cls = `cal-event compact-event p-${priorityClass(e.priority)} ${e.status === "cancelled" ? "cancelled" : ""} ${e.is_conflict ? "conflict" : ""} ${e.is_participant ? "invited" : ""}`;
      return (
        `<div class="${cls}" data-event='${eventPayload(e)}' role="button" tabindex="0" title="${escCal(e.title)}${e.is_participant ? " (вы приглашены)" : ""}">` +
        `<div class="ce-time">${eventTime(e.start_at)}–${eventTime(e.end_at)}</div>` +
        `<div class="ce-title">${escCal(e.title)}</div>` +
        `</div>`
      );
    }

    function timegridColumn(day) {
      const segments = segmentsForDay(day.date);
      const empty = segments.length ? "" : `<div class="cal-empty timegrid-empty">нет встреч</div>`;
      return (
        `<div class="timegrid-col ${day.is_today ? "today" : ""}" data-day="${day.date}">` +
        `<div class="timegrid-bg">${hourLines()}</div>${empty}` +
        segments.map(timedEvent).join("") +
        nowLineHtml(day) +
        `</div>`
      );
    }

    // UX-03: клик по пустому месту колонки создаёт встречу на это время.
    function bindGridCreate(root) {
      root.querySelectorAll(".timegrid-col").forEach((col) => {
        col.addEventListener("click", (e) => {
          if (e.target.closest(".cal-event") || e.target.closest("button")) return;
          const rect = col.getBoundingClientRect();
          const minutes = Math.max(0, Math.min(1439, (e.clientY - rect.top + col.scrollTop) * 60 / HOUR_HEIGHT));
          const rounded = Math.round(minutes / 30) * 30;
          const hh = pad(Math.floor(rounded / 60) % 24);
          const mm = pad(rounded % 60);
          const start = new Date(`${col.getAttribute("data-day")}T${hh}:${mm}:00`);
          const preset = {
            start_at: toLocalInput(start),
            end_at: toLocalInput(new Date(start.getTime() + 3600000)),
          };
          if (calState.owner) { preset.owner_id = calState.owner.id; preset.owner = calState.owner; }
          if (window.openEventModal) window.openEventModal(preset);
        });
      });
    }

    // UX-01: автоскролл сетки к началу рабочего дня (минус час).
    function scrollToWorkHours() {
      const grid = calendarBody.querySelector(".calendar-timegrid");
      if (!grid) return;
      const start = Math.max(0, workingHoursRange()[0] - 1);
      grid.scrollTop = Math.round(start * HOUR_HEIGHT);
    }

    function renderDay() {
      const day = calState.days[0] || { date: calState.date, is_today: calState.date === calState.today };
      return (
        `<div class="calendar-timegrid day-timegrid glass">` +
        `<div class="timegrid-corner"></div>` +
        `<div class="timegrid-head ${day.is_today ? "today" : ""}">` +
        `<span>${escCal(fullDateLabel(day.date))}</span>` +
        `<button class="btn small ghost" data-new-event data-day="${day.date}">＋ добавить</button>` +
        `</div>` +
        `<div class="timegrid-hours">${hourRows()}</div>` +
        timegridColumn(day) +
        `</div>`
      );
    }

    function renderWeek() {
      const heads = (calState.days || []).map((day, idx) =>
        `<div class="timegrid-head ${day.is_today ? "today" : ""}">` +
        `<span><b>${DOW[idx] || ""}</b> ${escCal(dayLabel(day.date))}</span>` +
        `<button class="btn small ghost" data-new-event data-day="${day.date}">＋</button>` +
        `</div>`
      ).join("");
      return (
        `<div class="calendar-timegrid week-timegrid glass">` +
        `<div class="timegrid-corner"></div>${heads}` +
        `<div class="timegrid-hours">${hourRows()}</div>` +
        (calState.days || []).map(timegridColumn).join("") +
        `</div>`
      );
    }

    function renderMonth() {
      const dow = DOW.map((d) => `<div class="month-dow">${d}</div>`).join("");
      const cells = (calState.days || []).map((day) => {
        const evs = eventsForDay(day.date);
        const shown = evs.slice(0, 4).map(compactEvent).join("");
        // UX-02: «+N ещё» открывает день, а не остаётся мёртвым текстом.
        const more = evs.length > 4
          ? `<button class="month-more" data-goto-day="${day.date}">+${evs.length - 4} ещё</button>`
          : "";
        return (
          `<div class="month-cell ${day.is_today ? "today" : ""} ${day.is_current_month ? "" : "outside"}">` +
          `<div class="month-cell-head">` +
          `<button class="month-day-num" data-goto-day="${day.date}" title="Открыть день">${dateAtNoon(day.date).getDate()}</button>` +
          `<button class="day-add" data-new-event data-day="${day.date}">＋</button></div>` +
          (evs.length ? shown + more : `<div class="cal-empty">нет встреч</div>`) +
          `</div>`
        );
      }).join("");
      return `<div class="month-grid glass">${dow}${cells}</div>`;
    }

    function updateChrome() {
      window.smartcalCalendarContext = {
        owner: calState.owner || null,
        adminView: !!calState.admin_view,
        viewerId: calState.viewer ? calState.viewer.id : null,
      };
      if (rangeEl) rangeEl.textContent = calState.label;
      if (subtitleEl) subtitleEl.textContent = calState.label;
      viewButtons.forEach((btn) => {
        const active = btn.getAttribute("data-cal-view") === calState.view;
        btn.classList.toggle("primary", active);
        btn.classList.toggle("ghost", !active);
      });
    }

    function renderCalendar() {
      updateChrome();
      if (calState.view === "day") calendarBody.innerHTML = renderDay();
      else if (calState.view === "month") calendarBody.innerHTML = renderMonth();
      else calendarBody.innerHTML = renderWeek();
      if (window.bindEventModalTriggers) window.bindEventModalTriggers(calendarBody);
      // UX-02: переход к дню по «+N ещё» / числу дня в месяце.
      calendarBody.querySelectorAll("[data-goto-day]").forEach((btn) => {
        btn.addEventListener("click", (e) => {
          e.stopPropagation();
          loadCalendar("day", btn.getAttribute("data-goto-day"), true);
        });
      });
      if (calState.view !== "month") {
        bindGridCreate(calendarBody);
        scrollToWorkHours();
      }
    }

    // BUG-20: быстрая навигация не должна рисовать устаревший ответ.
    let calAbort = null;

    async function loadCalendar(view, date, push, userId) {
      if (calAbort) calAbort.abort();
      calAbort = new AbortController();
      const signal = calAbort.signal;
      calendarBody.innerHTML = `<div class="calendar-skeleton glass">${'<div class="sk-col"></div>'.repeat(7)}</div>`;
      const params = new URLSearchParams({ view, date });
      const selectedUserId = userId !== undefined
        ? userId
        : (calState.owner && !calState.owner.is_current_user ? calState.owner.id : "");
      if (selectedUserId) {
        params.set("user_id", selectedUserId);
      }
      try {
        calState = await window.api("GET", "/api/calendar/range?" + params.toString(), undefined, signal);
        localStorage.setItem(VIEW_KEY, calState.view);
        renderCalendar();
        if (push !== false) {
          history.pushState({ calendar: true, view: calState.view, date: calState.date }, "", `${BASE}/calendar?${params.toString()}`);
        }
      } catch (e) {
        if (e.name === "AbortError") return; // пришёл более свежий запрос
        calendarBody.innerHTML = `<div class="alert error">${escCal(e.message)}</div>`;
      }
    }

    // BUG-10: календарь перерисовывается без перезагрузки страницы.
    window.__smartcalCalendarLive = true;
    window.addEventListener("smartcal:event-changed", () => {
      loadCalendar(calState.view, calState.date, false);
    });

    // UX-09: переход к произвольной дате.
    const gotoInput = document.getElementById("calendar-goto");
    if (gotoInput) {
      gotoInput.addEventListener("change", () => {
        if (gotoInput.value) loadCalendar(calState.view, gotoInput.value, true);
      });
    }

    viewButtons.forEach((btn) => {
      btn.addEventListener("click", () => {
        const view = btn.getAttribute("data-cal-view");
        if (view && view !== calState.view) loadCalendar(view, calState.date, true);
      });
    });

    navButtons.forEach((btn) => {
      btn.addEventListener("click", () => {
        const dir = btn.getAttribute("data-cal-nav");
        const target = dir === "prev" ? calState.prev_date : dir === "next" ? calState.next_date : calState.today;
        loadCalendar(calState.view, target, true);
      });
    });

    window.addEventListener("popstate", () => {
      const params = new URLSearchParams(location.search);
      loadCalendar(
        params.get("view") || localStorage.getItem(VIEW_KEY) || "week",
        params.get("date") || calState.today,
        false,
        params.get("user_id") || ""
      );
    });

    renderCalendar();
    const urlView = new URLSearchParams(location.search).get("view");
    const savedView = localStorage.getItem(VIEW_KEY);
    if (!urlView && savedView && savedView !== calState.view) {
      loadCalendar(savedView, calState.date, false);
    } else {
      localStorage.setItem(VIEW_KEY, calState.view);
    }
  }

  /* =====================================================================
     Чат-ассистент (с карточками и подтверждением действий)
     ===================================================================== */
  const chatLog = document.getElementById("chat-log");
  if (chatLog) {
    const shell = document.getElementById("assistant-shell");
    const form = document.getElementById("chat-form");
    const input = document.getElementById("chat-input");
    const uploadInput = document.getElementById("chat-file");
    const historyList = document.getElementById("chat-history-list");
    const historyState = document.getElementById("chat-history-state");
    const historyCount = document.getElementById("chat-history-count");
    const historyToggle = document.getElementById("chat-history-toggle");
    const historyMobile = document.getElementById("chat-history-mobile");
    const sideToggle = document.getElementById("chat-side-toggle");
    const saveState = document.getElementById("chat-save-state");
    const chatUserSelect = document.getElementById("chat-user-select");
    const chatUserSearch = document.getElementById("chat-user-search");
    const readonlyNote = document.getElementById("chat-readonly-note");
    const newChatButtons = [document.getElementById("chat-new"), document.getElementById("chat-new-inline")].filter(Boolean);
    const CHAT_HISTORY_COLLAPSED_KEY = "smartcal-chat-history-collapsed";
    const CHAT_SIDE_COLLAPSED_KEY = "smartcal-chat-side-collapsed";
    const chatMq = window.matchMedia("(max-width: 1080px)");
    const currentUserId = shell ? String(shell.getAttribute("data-current-user-id") || "") : "";
    let activeChatId = null;
    let activeChatOwnerId = currentUserId;
    let selectedChatUserId = chatUserSelect ? String(chatUserSelect.value || currentUserId) : currentUserId;
    let chats = [];
    let saveStateTimer = null;

    const esc = (s) =>
      String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
        ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
    const fmtChatDate = (iso) => {
      if (!iso) return "";
      const d = new Date(iso);
      if (isNaN(d)) return "";
      return d.toLocaleString("ru-RU", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
    };
    const fmtDT = (iso) => {
      if (!iso) return "";
      const d = new Date(iso);
      if (isNaN(d)) return iso;
      return d.toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
    };
    const fmtT = (iso) => {
      const d = new Date(iso);
      return isNaN(d) ? "" : d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
    };
    const LOC_RU = { online: "Онлайн", offline: "Очно", hybrid: "Гибрид" };

    function setSaveState(text, kind) {
      if (!saveState) return;
      clearTimeout(saveStateTimer);
      saveState.textContent = text || "";
      saveState.classList.toggle("error", kind === "err");
      if (text && kind !== "err") {
        saveStateTimer = setTimeout(() => { saveState.textContent = ""; }, 1800);
      }
    }

    function setHistoryState(text, kind) {
      if (!historyState) return;
      historyState.textContent = text || "";
      historyState.classList.toggle("error", kind === "err");
      historyState.style.display = text ? "block" : "none";
    }

    function setFormBusy(busy) {
      if (!form) return;
      form.querySelectorAll("input, button").forEach((el) => { el.disabled = busy; });
    }

    function isViewingForeignChat() {
      return selectedChatUserId && currentUserId && String(selectedChatUserId) !== String(currentUserId);
    }

    function applyChatReadonly() {
      const readonly = isViewingForeignChat();
      if (readonlyNote) readonlyNote.style.display = readonly ? "block" : "none";
      if (form) {
        form.querySelectorAll("input, button").forEach((el) => { el.disabled = readonly; });
      }
      // UX-18: в чужом чате плейсхолдер объясняет режим «только чтение».
      if (input) input.placeholder = readonly ? "Просмотр чата сотрудника (только чтение)" : "Напишите сообщение…";
      newChatButtons.forEach((btn) => { btn.disabled = readonly; });
    }

    function applyHistoryCollapsed(collapsed) {
      if (!shell) return;
      shell.classList.toggle("chat-history-collapsed", collapsed);
      localStorage.setItem(CHAT_HISTORY_COLLAPSED_KEY, collapsed ? "1" : "0");
      if (historyToggle) {
        historyToggle.textContent = collapsed ? "›" : "‹";
        historyToggle.setAttribute("aria-label", collapsed ? "Развернуть историю" : "Свернуть историю");
        historyToggle.setAttribute("aria-expanded", String(!collapsed));
      }
    }

    function applySideCollapsed(collapsed) {
      if (!shell) return;
      shell.classList.toggle("chat-side-collapsed", collapsed);
      localStorage.setItem(CHAT_SIDE_COLLAPSED_KEY, collapsed ? "1" : "0");
      if (sideToggle) {
        sideToggle.textContent = collapsed ? "‹" : "›";
        sideToggle.setAttribute("aria-label", collapsed ? "Развернуть подсказки" : "Свернуть подсказки");
        sideToggle.setAttribute("aria-expanded", String(!collapsed));
      }
    }

    function closeHistoryMobile() {
      if (shell) shell.classList.remove("chat-history-mobile-open");
    }

    function renderWelcome() {
      chatLog.innerHTML = "";
      renderResult({ reply: chatLog.getAttribute("data-greeting") || "Здравствуйте! Чем помочь?" });
    }

    function addUserMsg(text) {
      const el = document.createElement("div");
      el.className = "msg user";
      el.textContent = text;
      chatLog.appendChild(el);
      chatLog.scrollTop = chatLog.scrollHeight;
      return el;
    }

    function typingIndicator() {
      const el = document.createElement("div");
      el.className = "msg bot";
      el.innerHTML = '<span class="typing"><span></span><span></span><span></span></span>';
      chatLog.appendChild(el);
      chatLog.scrollTop = chatLog.scrollHeight;
      return el;
    }

    // ---- Карточки ---------------------------------------------------------
    function eventCardHtml(e) {
      const loc = LOC_RU[e.location_type] || e.location_type || "";
      const place = e.city ? " · " + esc(e.city) : (e.meeting_url ? " · ссылка" : "");
      return (
        `<div class="a-card-title">📅 ${esc(e.title || "Встреча")}</div>` +
        `<div class="a-card-row">${fmtDT(e.start_at)}–${fmtT(e.end_at)} · ${esc(loc)}${place}` +
        (e.priority != null ? ` · приоритет ${e.priority}` : "") + `</div>`
      );
    }

    function slotsHtml(slots, prefill) {
      // UX-06: контекст диалога (тема/участники/формат) едет в модалку вместе со слотом.
      const base = prefill || {};
      return slots.map((s, i) => {
        const w = (s.warnings || []).length ? `<div class="a-warn">⚠️ ${esc(s.warnings.join("; "))}</div>` : "";
        const payload = Object.assign({}, base, { start_at: s.start_at, end_at: s.end_at, source: "assistant" });
        return (
          `<div class="a-slot">` +
          `<div><b>${fmtDT(s.start_at)}–${fmtT(s.end_at)}</b> · ${s.duration_minutes} мин` +
          (s.reason ? `<div class="a-muted">${esc(s.reason)}</div>` : "") + w + `</div>` +
          `<button class="btn small ghost" data-slot='${esc(JSON.stringify(payload))}'>Взять</button>` +
          `</div>`
        );
      }).join("");
    }

    function ticketsHtml(opts) {
      const icon = { plane: "✈️", train: "🚆", bus: "🚌" };
      const modeRu = { plane: "Авиа", train: "ЖД", bus: "Автобус" };
      return opts.map((o) => {
        const h = Math.floor(o.duration_minutes / 60), m = o.duration_minutes % 60;
        const tr = o.transfers > 0 ? `, пересадок: ${o.transfers}` : ", без пересадок";
        return (
          `<div class="a-slot">` +
          `<div>${icon[o.mode] || ""} <b>${esc(modeRu[o.mode] || o.mode)}</b> · ${fmtDT(o.depart_at)}→${fmtT(o.arrive_at)} (${h}ч ${String(m).padStart(2, "0")}м${tr})` +
          `<div class="a-muted">${o.price.toFixed(0)} ${esc(o.currency)}</div></div>` +
          `<a class="btn small ghost" href="${esc(o.url)}" target="_blank" rel="noopener">Открыть</a>` +
          `</div>`
        );
      }).join("");
    }

    function travelSourcesHtml(sources) {
      const icon = { plane: "✈️", train: "🚆", bus: "🚌" };
      const modeRu = { plane: "Авиа", train: "ЖД", bus: "Автобус" };
      return sources.map((s) => (
        `<div class="a-slot">` +
        `<div>${icon[s.mode] || "🔎"} <b>${esc(s.title || s.provider)}</b>` +
        `<div class="a-muted">${esc(s.origin)} → ${esc(s.destination)} · ${esc(s.depart_date)} · ${esc(modeRu[s.mode] || s.mode || "")}</div>` +
        (s.note ? `<div class="a-muted">${esc(s.note)}</div>` : "") +
        `</div>` +
        `<a class="btn small ghost" href="${esc(s.url)}" target="_blank" rel="noopener">Открыть</a>` +
        `</div>`
      )).join("");
    }

    function protocolHtml(p) {
      const list = (arr) => (arr && arr.length ? "<ul>" + arr.map((x) => `<li>${esc(typeof x === "string" ? x : x.title)}</li>`).join("") + "</ul>" : "<div class='a-muted'>—</div>");
      return (
        `<div class="a-card-title">📝 Протокол</div>` +
        (p.summary ? `<div class="a-card-row">${esc(p.summary)}</div>` : "") +
        `<div class="a-sec"><b>Решения</b>${list(p.decisions)}</div>` +
        `<div class="a-sec"><b>Задачи</b>${list(p.action_items)}</div>` +
        (p.risks && p.risks.length ? `<div class="a-sec"><b>Риски</b>${list(p.risks)}</div>` : "") +
        (p.follow_up_meetings && p.follow_up_meetings.length ? `<div class="a-sec"><b>Follow-up встречи</b>${list(p.follow_up_meetings)}</div>` : "")
      );
    }

    function conflictHtml(d) {
      const rows = (d.conflicts || []).map((c) =>
        `<div class="a-slot"><div>⛔ <b>${esc(c.title)}</b> · ${fmtDT(c.start_at)}–${fmtT(c.end_at)} · приоритет ${c.priority}` +
        (c.is_high_priority ? " · высокий" : "") + `</div></div>`).join("");
      return `<div class="a-card-title">⚠️ Конфликт расписания</div>` +
        (d.explanation ? `<div class="a-card-row">${esc(d.explanation)}</div>` : "") + rows;
    }

    function employeeAvailabilityHtml(items) {
      return (items || []).map((item) => {
        const busy = (item.busyIntervals || []).length;
        const slots = item.availableSlots || [];
        const slotRows = slots.slice(0, 5).map((s) => {
          const payload = {
            owner_id: item.employeeId,
            start_at: s.start_at,
            end_at: s.end_at,
            source: "assistant",
          };
          return `<div class="a-slot"><div><b>${fmtDT(s.start_at)}–${fmtT(s.end_at)}</b>` +
            `<div class="a-muted">${s.duration_minutes} мин · ${esc(s.reason || "")}</div></div>` +
            `<button class="btn small ghost" data-slot='${esc(JSON.stringify(payload))}'>Занять</button></div>`;
        }).join("");
        const empty = slots.length ? "" : "<div class='a-muted'>Свободных слотов не найдено.</div>";
        return `<div class="a-sec"><b>${esc(item.name)}</b>` +
          `<div class="a-muted">${esc((item.requestedRange || {}).label || "")} · занято интервалов: ${busy} · свободных окон: ${slots.length}</div>` +
          slotRows + empty + `</div>`;
      }).join("");
    }

    function renderCard(card) {
      const el = document.createElement("div");
      el.className = "a-card";
      const d = card.data || {};
      if (card.kind === "created_event") el.innerHTML = eventCardHtml(d);
      else if (card.kind === "alternative_slots") el.innerHTML = `<div class="a-card-title">🟢 ${esc(card.title)}</div>` + slotsHtml(d.slots || [], d.prefill);
      else if (card.kind === "travel_options") el.innerHTML = `<div class="a-card-title">🎫 ${esc(card.title)}</div>` + ticketsHtml(d.options || []);
      else if (card.kind === "travel_sources") el.innerHTML = `<div class="a-card-title">🔎 ${esc(card.title)}</div>` + travelSourcesHtml(d.sources || []);
      else if (card.kind === "protocol") el.innerHTML = protocolHtml(d);
      else if (card.kind === "tasks") el.innerHTML = `<div class="a-card-title">✅ Задачи</div><ul>` + (d.items || []).map((i) => `<li>${esc(i)}</li>`).join("") + "</ul>";
      else if (card.kind === "conflict") el.innerHTML = conflictHtml(d);
      else if (card.kind === "employee_availability") el.innerHTML = `<div class="a-card-title">🟢 ${esc(card.title)}</div>` + employeeAvailabilityHtml(d.items || []);
      else if (card.kind === "reschedule_plan") el.innerHTML = `<div class="a-card-title">🔀 План переноса</div><div class="a-card-row">«${esc((d.conflict||{}).title||"встреча")}»: ${fmtDT(d.old_start_at)} → <b>${fmtDT(d.start_at)}</b></div>`;
      else if (card.kind === "reminder") el.innerHTML = `<div class="a-card-title">⏰ Напоминание</div><div class="a-card-row">${esc((d.event||{}).title||"")} · за ${d.minutes_before} мин (${fmtDT(d.remind_at)})</div>`;
      else if (card.kind === "summary" || card.kind === "calendar") {
        const evs = d.events || d.upcoming || [];
        el.innerHTML = `<div class="a-card-title">🗓️ ${esc(card.title)}</div>` +
          (evs.length ? evs.map((e) => `<div class="a-slot"><div><b>${fmtDT(e.start_at)}</b> ${esc(e.title)}</div></div>`).join("") : "<div class='a-muted'>Пусто</div>");
      } else el.innerHTML = `<div class="a-card-title">${esc(card.title)}</div>`;

      el.querySelectorAll("[data-slot]").forEach((b) => {
        b.addEventListener("click", () => {
          try { if (window.openEventModal) window.openEventModal(JSON.parse(b.getAttribute("data-slot"))); }
          catch (e) { /* ignore */ }
        });
      });
      return el;
    }

    // BUG-09: кнопки восстановленной истории знают актуальный статус действия.
    const ACTION_DONE_LABEL = {
      confirmed: "✓ выполнено",
      in_progress: "выполняется…",
      rejected: "отклонено",
      expired: "истекло",
    };

    function renderActions(actions, container, actionsState) {
      const wrap = document.createElement("div");
      wrap.className = "msg-actions";
      const states = actionsState || {};
      actions.forEach((a) => {
        const b = document.createElement("button");
        const style = a.style === "danger" ? "danger" : a.style === "primary" ? "primary" : "ghost";
        b.className = "btn small " + style;
        b.textContent = a.label;
        const state = a.action_id ? states[a.action_id] : null;
        if (state && state !== "pending") {
          b.disabled = true;
          b.classList.add("ghost");
          b.classList.remove("primary", "danger");
          if (a.type === "confirm") b.textContent = ACTION_DONE_LABEL[state] || state;
          else b.textContent = a.label;
        } else {
          b.disabled = isViewingForeignChat();
          b.addEventListener("click", () => runAction(a, b, container));
        }
        wrap.appendChild(b);
      });
      if (actions.length) container.appendChild(wrap);
    }

    async function runAction(a, btn, container) {
      if (a.action_id && (a.type === "confirm" || a.type === "reject")) {
        btn.disabled = true;
        try {
          const url = `/api/assistant/actions/${a.action_id}/${a.type}`;
          const res = await api("POST", url, {});
          container.querySelectorAll(".msg-actions button").forEach((x) => (x.disabled = true));
          if (a.type === "confirm") {
            const payload = { reply: res.message || "Готово ✅", cards: res.created_event ? [{ kind: "created_event", title: "Встреча", data: res.created_event }] : [] };
            renderResult(payload);
            saveAssistantSnapshot(payload);
            toast(res.message || "Действие выполнено");
          } else {
            const payload = { reply: "Действие отменено." };
            renderResult(payload);
            saveAssistantSnapshot(payload);
            toast("Отменено");
          }
        } catch (err) {
          btn.disabled = false;
          toast(err.message, "err");
        }
      } else if ((a.type === "create_event" || a.type === "open_event_form") && window.openEventModal) {
        window.openEventModal(a.payload || {});
      } else if (a.type === "upload_document" && uploadInput) {
        uploadInput.click();
      } else if (a.type === "search_tickets" && a.payload && a.payload.url) {
        window.open(a.payload.url, "_blank");
      } else {
        toast("Действие: " + a.label);
      }
    }

    // UX-07: маленький бейдж статуса/режима в сообщении бота.
    const STATUS_BADGES = {
      needs_confirmation: ["wait", "ждёт подтверждения"],
      needs_clarification: ["wait", "нужны уточнения"],
      conflict: ["conflict", "конфликт"],
      done: ["done", "выполнено"],
      error: ["conflict", "ошибка"],
    };

    function statusBadgeHtml(data) {
      const parts = [];
      const st = STATUS_BADGES[data.status];
      if (st) parts.push(`<span class="msg-badge ${st[0]}"><span class="dot-i"></span>${st[1]}</span>`);
      if (data.mode === "dify-fallback") parts.push('<span class="msg-badge mode" title="Внешний ассистент недоступен — работает локальный разбор">локальный режим</span>');
      return parts.length ? `<div class="msg-badges">${parts.join("")}</div>` : "";
    }

    function renderResult(data) {
      const el = document.createElement("div");
      el.className = "msg bot";
      const reply = document.createElement("div");
      reply.textContent = data.reply || "";
      el.appendChild(reply);
      (data.warnings || []).forEach((w) => {
        const wr = document.createElement("div");
        wr.className = "a-warn";
        wr.textContent = "⚠️ " + w;
        el.appendChild(wr);
      });
      (data.cards || []).forEach((c) => el.appendChild(renderCard(c)));
      if (data.suggested_actions && data.suggested_actions.length) {
        renderActions(data.suggested_actions, el, data.actions_state);
      }
      const badges = statusBadgeHtml(data);
      if (badges) el.insertAdjacentHTML("beforeend", badges);
      chatLog.appendChild(el);
      chatLog.scrollTop = chatLog.scrollHeight;
      return el;
    }
    window.chatAddMsg = (text) => renderResult({ reply: text });

    function renderHistory() {
      if (!historyList) return;
      historyList.innerHTML = "";
      if (historyCount) historyCount.textContent = chats.length ? `${chats.length}` : "";
      if (!chats.length) {
        historyList.innerHTML =
          '<div class="chat-history-empty"><span class="ic">💬</span>' +
          "Здесь появятся ваши чаты.<br>Начните новый разговор." +
          "</div>";
        return;
      }
      chats.forEach((chat) => {
        const item = document.createElement("div");
        item.className = "chat-history-item" + (chat.id === activeChatId ? " active" : "");

        const main = document.createElement("button");
        main.type = "button";
        main.className = "chat-history-main";
        main.innerHTML = `<span class="chat-history-name">${esc(chat.title || "Новый чат")}</span>` +
          `<span class="chat-history-date">${esc(fmtChatDate(chat.updatedAt || chat.createdAt))}</span>`;
        main.addEventListener("click", () => openChat(chat.id));

        const del = document.createElement("button");
        del.type = "button";
        del.className = "btn icon ghost chat-delete";
        del.textContent = "×";
        del.title = "Удалить чат";
        del.style.display = String(chat.userId) === String(currentUserId) ? "" : "none";
        del.addEventListener("click", (e) => {
          e.stopPropagation();
          deleteChat(chat.id);
        });

        item.appendChild(main);
        item.appendChild(del);
        historyList.appendChild(item);
      });
    }

    function renderStoredMessages(messages) {
      chatLog.innerHTML = "";
      if (!messages || !messages.length) {
        renderWelcome();
        return;
      }
      messages.forEach((message) => {
        if (message.role === "user") {
          addUserMsg(message.content || "");
        } else if (message.role === "assistant") {
          const payload = message.payload && Object.keys(message.payload).length ? message.payload : { reply: message.content || "" };
          if (!payload.reply) payload.reply = message.content || "";
          renderResult(payload);
        } else {
          renderResult({ reply: `[${message.role}] ${message.content || ""}` });
        }
      });
    }

    function showHistorySkeleton() {
      if (!historyList) return;
      historyList.innerHTML =
        '<div class="chat-history-skeleton">' +
        '<div class="sk"></div><div class="sk"></div><div class="sk"></div><div class="sk"></div>' +
        "</div>";
    }

    async function loadHistory(openRecent) {
      setHistoryState("");
      if (!chats.length) showHistorySkeleton();
      try {
        const query = selectedChatUserId && selectedChatUserId !== currentUserId
          ? "?user_id=" + encodeURIComponent(selectedChatUserId)
          : "";
        const data = await api("GET", "/api/assistant/chats" + query);
        chats = data.items || [];
        renderHistory();
        if (!chats.length) {
          activeChatId = null;
          activeChatOwnerId = selectedChatUserId;
          setHistoryState("");
          renderWelcome();
          applyChatReadonly();
          return;
        }
        setHistoryState("");
        if (activeChatId && chats.some((chat) => chat.id === activeChatId)) {
          renderHistory();
          applyChatReadonly();
          return;
        }
        if (openRecent !== false) {
          await openChat(chats[0].id);
        }
        applyChatReadonly();
      } catch (err) {
        setHistoryState("Ошибка загрузки: " + err.message, "err");
        if (!activeChatId) renderWelcome();
        applyChatReadonly();
      }
    }

    async function openChat(chatId) {
      if (!chatId) return;
      activeChatId = chatId;
      renderHistory();
      chatLog.innerHTML = '<div class="loading"><span class="spinner"></span> Загрузка чата…</div>';
      setSaveState("Загрузка чата…");
      try {
        const data = await api("GET", `/api/assistant/chats/${chatId}`);
        activeChatId = data.id;
        activeChatOwnerId = String(data.userId || selectedChatUserId || currentUserId);
        renderStoredMessages(data.messages || []);
        setSaveState("");
        closeHistoryMobile();
        renderHistory();
        applyChatReadonly();
      } catch (err) {
        chatLog.innerHTML = "";
        renderResult({ reply: "Ошибка загрузки чата: " + err.message });
        setSaveState("Ошибка загрузки чата", "err");
      }
    }

    async function createNewChat() {
      if (isViewingForeignChat()) {
        toast("Чужая история доступна только для просмотра", "err");
        return;
      }
      setSaveState("Создание нового чата…");
      try {
        const data = await api("POST", "/api/assistant/chats", {});
        activeChatId = data.id;
        await loadHistory(false);
        renderStoredMessages(data.messages || []);
        setSaveState("Новый чат создан");
        closeHistoryMobile();
      } catch (err) {
        setSaveState("Ошибка создания чата", "err");
        toast(err.message, "err");
      }
    }

    async function deleteChat(chatId) {
      if (isViewingForeignChat()) {
        toast("Чужая история доступна только для просмотра", "err");
        return;
      }
      if (!chatId) return;
      const ok = await uiConfirm("Удалить этот чат вместе с историей сообщений?", { danger: true });
      if (!ok) return;
      setSaveState("Удаление чата…");
      try {
        await api("DELETE", `/api/assistant/chats/${chatId}`);
        if (activeChatId === chatId) activeChatId = null;
        await loadHistory(true);
        setSaveState("Чат удалён");
      } catch (err) {
        setSaveState("Ошибка удаления чата", "err");
        toast(err.message, "err");
      }
    }

    async function saveAssistantSnapshot(payload) {
      if (!activeChatId) return;
      try {
        await api("POST", `/api/assistant/chats/${activeChatId}/messages`, {
          role: "assistant",
          content: payload.reply || "",
          payload,
        });
        await loadHistory(false);
      } catch (err) {
        setSaveState("Ошибка сохранения сообщения", "err");
      }
    }

    async function send(message) {
      if (isViewingForeignChat()) {
        toast("Чужая история доступна только для просмотра", "err");
        return;
      }
      addUserMsg(message);
      const typing = typingIndicator();
      setFormBusy(true);
      setSaveState("Сохранение сообщения…");
      try {
        const data = await api("POST", "/api/chat", { message, conversation_id: activeChatId });
        activeChatId = data.conversation_id || activeChatId;
        typing.remove();
        renderResult(data);
        await loadHistory(false);
        setSaveState("Сохранено");
        if (window.refreshNotifications) window.refreshNotifications();
      } catch (err) {
        typing.remove();
        renderResult({ reply: "Ошибка: " + err.message });
        setSaveState("Ошибка сохранения сообщения", "err");
      } finally {
        setFormBusy(false);
        input.focus();
      }
    }
    window.chatSend = send;

    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const v = input.value.trim();
      if (!v) return;
      input.value = "";
      send(v);
    });

    newChatButtons.forEach((btn) => btn.addEventListener("click", createNewChat));

    if (chatUserSelect) {
      chatUserSelect.addEventListener("change", () => {
        selectedChatUserId = String(chatUserSelect.value || currentUserId);
        activeChatId = null;
        activeChatOwnerId = selectedChatUserId;
        loadHistory(true);
        applyChatReadonly();
      });
    }
    if (chatUserSearch && chatUserSelect) {
      chatUserSearch.addEventListener("input", () => {
        const q = chatUserSearch.value.trim().toLowerCase();
        Array.from(chatUserSelect.options).forEach((option) => {
          option.hidden = !!q && option.textContent.toLowerCase().indexOf(q) === -1;
        });
      });
    }

    document.querySelectorAll("[data-suggest]").forEach((b) => {
      b.addEventListener("click", () => send(b.getAttribute("data-suggest")));
    });

    if (uploadInput) {
      uploadInput.addEventListener("change", async () => {
        const file = uploadInput.files[0];
        if (!file) return;
        addUserMsg("📎 " + file.name);
        const typing = typingIndicator();
        const fd = new FormData();
        fd.append("file", file);
        if (activeChatId) fd.append("conversation_id", activeChatId);
        setSaveState("Сохранение документа…");
        try {
          const res = await fetch(BASE + "/chat/upload", { method: "POST", body: fd });
          // BUG-27: не-JSON ответ (500 HTML) не должен ронять чат.
          let data = null;
          try { data = await res.json(); } catch (parseErr) { data = null; }
          if (!res.ok) throw new Error((data && data.detail) || res.statusText);
          if (!data) throw new Error("Некорректный ответ сервера");
          activeChatId = data.conversation_id || activeChatId;
          typing.remove();
          renderResult(data);
          await loadHistory(false);
          setSaveState("Документ сохранён");
        } catch (err) {
          typing.remove();
          renderResult({ reply: "Не удалось обработать файл: " + err.message });
          setSaveState("Ошибка сохранения документа", "err");
        }
        uploadInput.value = "";
      });
    }

    if (historyToggle) {
      historyToggle.addEventListener("click", () => {
        if (chatMq.matches) closeHistoryMobile();
        else applyHistoryCollapsed(!shell.classList.contains("chat-history-collapsed"));
      });
    }
    if (historyMobile && shell) {
      historyMobile.addEventListener("click", () => {
        shell.classList.remove("chat-history-collapsed");
        shell.classList.toggle("chat-history-mobile-open");
      });
    }
    if (sideToggle) {
      sideToggle.addEventListener("click", () => {
        applySideCollapsed(!shell.classList.contains("chat-side-collapsed"));
      });
    }
    document.addEventListener("click", (e) => {
      if (!chatMq.matches || !shell || !shell.classList.contains("chat-history-mobile-open")) return;
      const panel = document.getElementById("chat-history-panel");
      if (panel && !panel.contains(e.target) && e.target !== historyMobile) closeHistoryMobile();
    });
    chatMq.addEventListener("change", () => closeHistoryMobile());

    applyHistoryCollapsed(localStorage.getItem(CHAT_HISTORY_COLLAPSED_KEY) === "1");
    applySideCollapsed(localStorage.getItem(CHAT_SIDE_COLLAPSED_KEY) === "1");
    loadHistory(true);
    if (input && !isViewingForeignChat()) input.focus(); // UX-18
  }

  /* =====================================================================
     Админ: пользователи
     ===================================================================== */
  const userModal = document.getElementById("user-modal");
  if (userModal) {
    const f = userModal.querySelector("form");
    const titleEl = document.getElementById("user-modal-title");
    const pwHint = userModal.querySelector("[data-role=pw-hint]");

    function openUser(data) {
      f.reset();
      data = data || {};
      const editing = !!data.id;
      titleEl.textContent = editing ? "Редактирование пользователя" : "Новый пользователь";
      f.elements["id"].value = data.id || "";
      f.elements["email"].value = data.email || "";
      f.elements["email"].disabled = false;
      f.elements["full_name"].value = data.full_name || "";
      f.elements["role"].value = data.role || "user";
      f.elements["is_active"].value = data.is_active === false ? "false" : "true";
      f.elements["password"].value = "";
      f.elements["password"].required = !editing;
      if (pwHint) pwHint.style.display = editing ? "block" : "none";
      openModalEl(userModal);
    }
    window.openUserModal = openUser;
    window.closeUserModal = () => closeModalEl(userModal);
    userModal.addEventListener("click", (e) => { if (e.target === userModal) closeModalEl(userModal); });

    f.addEventListener("submit", async (e) => {
      e.preventDefault();
      const id = f.elements["id"].value;
      const payload = {
        full_name: f.elements["full_name"].value.trim(),
        role: f.elements["role"].value,
        is_active: f.elements["is_active"].value === "true",
      };
      const pw = f.elements["password"].value;
      if (pw) payload.password = pw;
      try {
        if (id) {
          payload.email = f.elements["email"].value.trim();
          await api("PATCH", `/api/admin/users/${id}`, payload);
          toast("Пользователь обновлён");
        } else {
          payload.email = f.elements["email"].value.trim();
          if (!payload.password) { toast("Укажите пароль", "err"); return; }
          await api("POST", "/api/admin/users", payload);
          toast("Пользователь создан");
        }
        setTimeout(() => location.reload(), 400);
      } catch (err) { toast(err.message, "err"); }
    });

    document.querySelectorAll("[data-user]").forEach((node) => {
      node.addEventListener("click", () => {
        try { openUser(JSON.parse(node.getAttribute("data-user"))); }
        catch (e) { /* ignore */ }
      });
    });
  }

  /* =====================================================================
     Уведомления (топбар)
     ===================================================================== */
  const notifBtn = document.getElementById("notif-btn");
  if (notifBtn) {
    const panel = document.getElementById("notif-panel");
    const badge = document.getElementById("notif-badge");
    const list = document.getElementById("notif-list");
    const readBtn = document.getElementById("notif-read");

    async function refresh() {
      try {
        const data = await api("GET", "/api/notifications");
        if (data.unread > 0) { badge.style.display = "grid"; badge.textContent = data.unread > 99 ? "99+" : data.unread; }
        else { badge.style.display = "none"; }
        notifBtn.setAttribute("aria-label", data.unread > 0 ? `Уведомления, ${data.unread} непрочитанных` : "Уведомления");
        if (!data.items.length) { list.innerHTML = '<div class="a-muted">Пока нет уведомлений</div>'; return; }
        list.innerHTML = data.items.map((n) => {
          const d = new Date(n.created_at).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
          const unread = n.status !== "read" ? ' style="border-left:3px solid var(--accent)"' : "";
          return `<div class="notif-item"${unread}><div class="t">${escHtml(n.title || "")}</div><div class="x">${escHtml(n.text || "")}</div><div class="x">${d}</div></div>`;
        }).join("");
      } catch (e) { /* not logged in / ignore */ }
    }
    window.refreshNotifications = refresh;

    notifBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      panel.classList.toggle("open");
      if (panel.classList.contains("open")) refresh();
    });
    document.addEventListener("click", (e) => {
      if (!panel.contains(e.target) && e.target !== notifBtn) panel.classList.remove("open");
    });
    if (readBtn) readBtn.addEventListener("click", async () => {
      try { await api("POST", "/api/notifications/read", {}); refresh(); } catch (e) { /* ignore */ }
    });
    refresh();
    setInterval(refresh, 60000);
  }

  /* =====================================================================
     Общие хелперы отображения (документы, билеты)
     ===================================================================== */
  const escHtml = (s) =>
    String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmtDateTime = (iso) => {
    if (!iso) return "";
    const d = new Date(iso);
    return isNaN(d) ? iso : d.toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
  };
  const fmtTime = (iso) => {
    const d = new Date(iso);
    return isNaN(d) ? "" : d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  };
  const spinner = (label) => `<div class="loading"><span class="spinner"></span> ${escHtml(label || "Загрузка…")}</div>`;

  /* =====================================================================
     Документы / протоколы
     ===================================================================== */
  const docDropzone = document.getElementById("doc-dropzone");
  if (docDropzone) {
    const fileInput = document.getElementById("doc-file");
    const protoEmpty = document.getElementById("protocol-empty");
    let protoBody = document.getElementById("protocol-body");
    const docList = document.getElementById("doc-list");

    function ul(arr, mapper) {
      if (!arr || !arr.length) return '<div class="a-muted">—</div>';
      return "<ul>" + arr.map((x) => `<li>${escHtml(mapper ? mapper(x) : x)}</li>`).join("") + "</ul>";
    }

    function showLoading() {
      protoEmpty.style.display = "none";
      protoBody.style.display = "block";
      protoBody.innerHTML = spinner("Формирую протокол…");
      // UX-17: на мобиле панель протокола ниже списка — показываем прогресс.
      protoBody.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }

    // UX-17: показать имя выбранного файла в дропзоне до окончания загрузки.
    function showPickedFile(name) {
      const title = docDropzone.querySelector(".dz-title");
      if (title) title.textContent = "Загружаю: " + name;
    }
    function resetDropzone() {
      const title = docDropzone.querySelector(".dz-title");
      if (title) title.textContent = "Перетащите файл сюда или нажмите";
    }

    function renderProtocol(data) {
      protoEmpty.style.display = "none";
      protoBody.style.display = "block";
      const p = data.protocol || {};
      const warns = (data.warnings || []).filter(Boolean);
      const fu = p.follow_up_meetings || [];
      let html = "";
      if (data.filename) html += `<div class="a-card-title" style="font-size:16px;">📄 ${escHtml(data.filename)}</div>`;
      if (warns.length) html += `<div class="a-warn">⚠️ ${escHtml(warns.join("; "))}</div>`;
      if (p.summary) html += `<div class="proto-sec"><b>Краткое содержание</b><div>${escHtml(p.summary)}</div></div>`;
      if (p.participants && p.participants.length)
        html += `<div class="proto-sec"><b>Участники</b><div>${p.participants.map(escHtml).join(", ")}</div></div>`;
      html += `<div class="proto-sec"><b>Решения</b>${ul(p.decisions)}</div>`;
      html += `<div class="proto-sec"><b>Задачи</b>${ul(p.action_items)}</div>`;
      if (p.responsibles && p.responsibles.length)
        html += `<div class="proto-sec"><b>Ответственные</b><div>${p.responsibles.map(escHtml).join(", ")}</div></div>`;
      if (p.deadlines && p.deadlines.length)
        html += `<div class="proto-sec"><b>Сроки</b>${ul(p.deadlines)}</div>`;
      if (p.risks && p.risks.length)
        html += `<div class="proto-sec"><b>Риски</b>${ul(p.risks)}</div>`;
      if (fu.length)
        html += `<div class="proto-sec"><b>Предлагаемые follow-up встречи</b>${ul(fu, (m) => m.title + (m.date_hint ? " · " + m.date_hint : ""))}</div>`;
      protoBody.innerHTML = html;

      // Кнопка «Создать встречи из протокола» (подтверждение действия).
      const confirmAction = (data.suggested_actions || []).find((a) => a.type === "confirm" && a.action_id);
      if (confirmAction) {
        const wrap = document.createElement("div");
        wrap.className = "msg-actions";
        const b = document.createElement("button");
        b.className = "btn small primary";
        b.textContent = confirmAction.label || `Создать ${fu.length} встреч(и) из протокола`;
        b.addEventListener("click", async () => {
          b.disabled = true;
          try {
            const res = await window.api("POST", `/api/assistant/actions/${confirmAction.action_id}/confirm`, {});
            window.toast(res.message || "Встречи созданы");
            b.textContent = "✓ " + (res.message || "Готово");
            if (window.refreshNotifications) window.refreshNotifications();
          } catch (e) { b.disabled = false; window.toast(e.message, "err"); }
        });
        wrap.appendChild(b);
        protoBody.appendChild(wrap);
      }
    }

    function prependDoc(docId, filename) {
      if (!docList) return;
      const empty = document.getElementById("doc-empty");
      if (empty) empty.remove();
      if (docList.querySelector(`[data-doc-id="${docId}"]`)) return;
      const row = document.createElement("div");
      row.className = "event-row";
      row.setAttribute("data-doc-id", docId);
      row.innerHTML =
        `<div class="prio" style="background:var(--chip-bg); color:var(--accent-strong);">📄</div>` +
        `<div class="body"><div class="title">${escHtml(filename)}</div>` +
        `<div class="sub muted">только что</div></div>` +
        `<button class="btn small primary" data-make-protocol="${docId}">Сформировать протокол</button>`;
      docList.insertBefore(row, docList.firstChild);
      bindProtocolButtons();
    }

    async function uploadFile(file) {
      showLoading();
      showPickedFile(file.name);
      const fd = new FormData();
      fd.append("file", file);
      try {
        const res = await fetch(BASE + "/chat/upload", { method: "POST", body: fd });
        // BUG-27: сервер мог ответить не-JSON (например, 500 HTML).
        let data = null;
        try { data = await res.json(); } catch (parseErr) { data = null; }
        if (!res.ok) throw new Error((data && data.detail) || res.statusText || "Ошибка загрузки");
        if (!data) throw new Error("Некорректный ответ сервера");
        renderProtocol(data);
        if (data.document_id) prependDoc(data.document_id, data.filename || file.name);
        window.toast("Документ загружен, протокол готов");
        protoBody.scrollIntoView({ behavior: "smooth", block: "start" });
      } catch (e) {
        protoBody.innerHTML = `<div class="alert error">Не удалось обработать файл: ${escHtml(e.message)}</div>`;
        window.toast(e.message, "err");
      } finally {
        resetDropzone();
      }
    }

    async function makeProtocol(docId) {
      showLoading();
      try {
        const data = await window.api("POST", `/api/documents/${docId}/protocol`, {});
        renderProtocol(data);
      } catch (e) {
        protoBody.innerHTML = `<div class="alert error">${escHtml(e.message)}</div>`;
        window.toast(e.message, "err");
      }
    }

    function bindProtocolButtons() {
      document.querySelectorAll("[data-make-protocol]").forEach((b) => {
        if (b.dataset.bound) return;
        b.dataset.bound = "1";
        b.addEventListener("click", () => makeProtocol(b.getAttribute("data-make-protocol")));
      });
    }

    docDropzone.addEventListener("click", () => fileInput.click());
    fileInput.addEventListener("change", () => { if (fileInput.files[0]) uploadFile(fileInput.files[0]); fileInput.value = ""; });
    ["dragenter", "dragover"].forEach((ev) =>
      docDropzone.addEventListener(ev, (e) => { e.preventDefault(); docDropzone.classList.add("drag"); }));
    ["dragleave", "drop"].forEach((ev) =>
      docDropzone.addEventListener(ev, (e) => { e.preventDefault(); docDropzone.classList.remove("drag"); }));
    docDropzone.addEventListener("drop", (e) => { if (e.dataTransfer.files[0]) uploadFile(e.dataTransfer.files[0]); });
    bindProtocolButtons();
  }

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

      results.innerHTML = spinner("Ищу варианты…");
      countEl.textContent = "";
      try {
        const payload = await window.api("GET", "/api/tickets/search?" + params.toString());
        let data = Array.isArray(payload) ? payload : (payload.items || []);
        const sources = Array.isArray(payload) ? [] : (payload.external_searches || []);
        if (!isNaN(budget) && budget > 0) data = data.filter((o) => o.price <= budget);
        if (prefs.includes("direct")) data = data.filter((o) => o.transfers === 0);
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
