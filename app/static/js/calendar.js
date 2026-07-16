/* ============================================================================
   Умный календарь — calendar.js: модалка события + сетка календаря
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

    const escCal = esc; // ARCH-05: единый esc из core.js
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

    // UI-15: подсветка выходных (суббота=6, воскресенье=0).
    const isWeekend = (iso) => {
      const dow = dateAtNoon(iso).getDay();
      return dow === 0 || dow === 6;
    };

    function timegridColumn(day) {
      const segments = segmentsForDay(day.date);
      const empty = segments.length ? "" : `<div class="cal-empty timegrid-empty">нет встреч</div>`;
      return (
        `<div class="timegrid-col ${day.is_today ? "today" : ""}${isWeekend(day.date) ? " weekend" : ""}" data-day="${day.date}">` +
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
          `<div class="month-cell ${day.is_today ? "today" : ""} ${day.is_current_month ? "" : "outside"}${isWeekend(day.date) ? " weekend" : ""}">` +
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
          history.pushState({ calendar: true, view: calState.view, date: calState.date }, "", `${window.APP_BASE}/calendar?${params.toString()}`);
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
})();
