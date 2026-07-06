/* ============================================================================
   Умный календарь — фронтенд-логика (vanilla JS, без сборки).
   Тема, сайдбар, тосты, API-хелпер, модалки событий и пользователей, чат.
   ============================================================================ */
(function () {
  "use strict";

  /* ----------------------------- Тема ----------------------------- */
  const THEME_KEY = "smartcal-theme";
  function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    const btn = document.getElementById("theme-toggle");
    if (btn) btn.textContent = theme === "dark" ? "☀️" : "🌙";
  }
  window.toggleTheme = function () {
    const cur = document.documentElement.getAttribute("data-theme") || "light";
    const next = cur === "dark" ? "light" : "dark";
    localStorage.setItem(THEME_KEY, next);
    applyTheme(next);
  };
  applyTheme(localStorage.getItem(THEME_KEY) || "light");

  /* --------------------------- Сайдбар (моб.) --------------------------- */
  window.toggleSidebar = function () {
    const sb = document.querySelector(".sidebar");
    if (sb) sb.classList.toggle("open");
  };

  /* ----------------------------- Тосты ----------------------------- */
  function toast(msg, kind) {
    let wrap = document.querySelector(".toast-wrap");
    if (!wrap) {
      wrap = document.createElement("div");
      wrap.className = "toast-wrap";
      document.body.appendChild(wrap);
    }
    const el = document.createElement("div");
    el.className = "toast " + (kind === "err" ? "err" : "ok");
    el.textContent = msg;
    wrap.appendChild(el);
    setTimeout(() => el.remove(), 3200);
  }
  window.toast = toast;

  /* --------------------------- API-хелпер --------------------------- */
  async function api(method, url, body) {
    const opts = { method, headers: {} };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    const res = await fetch(url, opts);
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

    function openEvent(data) {
      f.reset();
      data = data || {};
      const editing = !!data.id;
      titleEl.textContent = editing ? "Редактирование встречи" : "Новая встреча";
      f.elements["id"].value = data.id || "";
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
      f.elements["priority"].value = data.priority != null ? data.priority : 5;
      f.elements["importance"].value = data.importance || "normal";
      f.elements["status"].value = data.status || "planned";
      deleteBtn.style.display = editing ? "inline-flex" : "none";
      eventModal.classList.add("open");
    }
    function defaultStart() {
      const d = new Date();
      d.setMinutes(0, 0, 0);
      d.setHours(d.getHours() + 1);
      return d;
    }
    window.openEventModal = openEvent;
    window.closeEventModal = () => eventModal.classList.remove("open");
    eventModal.addEventListener("click", (e) => { if (e.target === eventModal) eventModal.classList.remove("open"); });

    f.addEventListener("submit", async (e) => {
      e.preventDefault();
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
      try {
        if (id) {
          payload.status = f.elements["status"].value;
          await api("PATCH", `/api/events/${id}`, payload);
          toast("Встреча обновлена");
        } else {
          await api("POST", "/api/events", payload);
          toast("Встреча создана");
        }
        setTimeout(() => location.reload(), 400);
      } catch (err) {
        toast(err.message, "err");
      }
    });

    deleteBtn.addEventListener("click", async () => {
      const id = f.elements["id"].value;
      if (!id || !confirm("Удалить встречу?")) return;
      try {
        await api("DELETE", `/api/events/${id}`);
        toast("Встреча удалена");
        setTimeout(() => location.reload(), 400);
      } catch (err) { toast(err.message, "err"); }
    });

    // Клики по событиям в календаре/дашборде
    document.querySelectorAll("[data-event]").forEach((node) => {
      node.addEventListener("click", () => {
        try { openEvent(JSON.parse(node.getAttribute("data-event"))); }
        catch (e) { /* ignore */ }
      });
    });
    // Кнопки «добавить» с предустановленной датой
    document.querySelectorAll("[data-new-event]").forEach((node) => {
      node.addEventListener("click", () => {
        const preset = {};
        const day = node.getAttribute("data-day");
        if (day) {
          const s = new Date(day + "T10:00");
          preset.start_at = s.toISOString();
          preset.end_at = new Date(s.getTime() + 3600000).toISOString();
        }
        openEvent(preset);
      });
    });
  }

  /* =====================================================================
     Чат-ассистент (с карточками и подтверждением действий)
     ===================================================================== */
  const chatLog = document.getElementById("chat-log");
  if (chatLog) {
    const form = document.getElementById("chat-form");
    const input = document.getElementById("chat-input");
    const uploadInput = document.getElementById("chat-file");

    const esc = (s) =>
      String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
        ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
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

    function slotsHtml(slots) {
      return slots.map((s, i) => {
        const w = (s.warnings || []).length ? `<div class="a-warn">⚠️ ${esc(s.warnings.join("; "))}</div>` : "";
        return (
          `<div class="a-slot">` +
          `<div><b>${fmtDT(s.start_at)}–${fmtT(s.end_at)}</b> · ${s.duration_minutes} мин` +
          (s.reason ? `<div class="a-muted">${esc(s.reason)}</div>` : "") + w + `</div>` +
          `<button class="btn small ghost" data-slot='${esc(JSON.stringify({start_at: s.start_at, end_at: s.end_at, source: "assistant"}))}'>Взять</button>` +
          `</div>`
        );
      }).join("");
    }

    function ticketsHtml(opts) {
      const icon = { plane: "✈️", train: "🚆", bus: "🚌" };
      return opts.map((o) => {
        const h = Math.floor(o.duration_minutes / 60), m = o.duration_minutes % 60;
        const tr = o.transfers > 0 ? `, пересадок: ${o.transfers}` : ", без пересадок";
        return (
          `<div class="a-slot">` +
          `<div>${icon[o.mode] || ""} <b>${esc(o.mode)}</b> · ${fmtDT(o.depart_at)}→${fmtT(o.arrive_at)} (${h}ч ${String(m).padStart(2, "0")}м${tr})` +
          `<div class="a-muted">${o.price.toFixed(0)} ${esc(o.currency)}</div></div>` +
          `<a class="btn small ghost" href="${esc(o.url)}" target="_blank" rel="noopener">Открыть</a>` +
          `</div>`
        );
      }).join("");
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

    function renderCard(card) {
      const el = document.createElement("div");
      el.className = "a-card";
      const d = card.data || {};
      if (card.kind === "created_event") el.innerHTML = eventCardHtml(d);
      else if (card.kind === "alternative_slots") el.innerHTML = `<div class="a-card-title">🟢 ${esc(card.title)}</div>` + slotsHtml(d.slots || []);
      else if (card.kind === "travel_options") el.innerHTML = `<div class="a-card-title">🎫 ${esc(card.title)}</div>` + ticketsHtml(d.options || []);
      else if (card.kind === "protocol") el.innerHTML = protocolHtml(d);
      else if (card.kind === "tasks") el.innerHTML = `<div class="a-card-title">✅ Задачи</div><ul>` + (d.items || []).map((i) => `<li>${esc(i)}</li>`).join("") + "</ul>";
      else if (card.kind === "conflict") el.innerHTML = conflictHtml(d);
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

    function renderActions(actions, container) {
      const wrap = document.createElement("div");
      wrap.className = "msg-actions";
      actions.forEach((a) => {
        const b = document.createElement("button");
        const style = a.style === "danger" ? "danger" : a.style === "primary" ? "primary" : "ghost";
        b.className = "btn small " + style;
        b.textContent = a.label;
        b.addEventListener("click", () => runAction(a, b, container));
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
            const bot = renderResult({ reply: res.message || "Готово ✅", cards: res.created_event ? [{ kind: "created_event", title: "Встреча", data: res.created_event }] : [] });
            toast(res.message || "Действие выполнено");
          } else {
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

    function renderResult(data) {
      const el = document.createElement("div");
      el.className = "msg bot";
      const reply = document.createElement("div");
      reply.textContent = data.reply || "";
      el.appendChild(reply);
      if (data.intent) {
        const meta = document.createElement("span");
        meta.className = "meta";
        meta.textContent = `интент: ${data.intent}` + (data.mode ? ` · режим: ${data.mode}` : "") + (data.status ? ` · ${data.status}` : "");
        el.appendChild(meta);
      }
      (data.warnings || []).forEach((w) => {
        const wr = document.createElement("div");
        wr.className = "a-warn";
        wr.textContent = "⚠️ " + w;
        el.appendChild(wr);
      });
      (data.cards || []).forEach((c) => el.appendChild(renderCard(c)));
      if (data.suggested_actions && data.suggested_actions.length) renderActions(data.suggested_actions, el);
      chatLog.appendChild(el);
      chatLog.scrollTop = chatLog.scrollHeight;
      return el;
    }
    window.chatAddMsg = (text) => renderResult({ reply: text });

    async function send(message) {
      addUserMsg(message);
      const typing = typingIndicator();
      try {
        const data = await api("POST", "/api/chat", { message });
        typing.remove();
        renderResult(data);
        if (window.refreshNotifications) window.refreshNotifications();
      } catch (err) {
        typing.remove();
        renderResult({ reply: "Ошибка: " + err.message });
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
        try {
          const res = await fetch("/chat/upload", { method: "POST", body: fd });
          const data = await res.json();
          typing.remove();
          if ((data.warnings || []).length) data.warnings = data.warnings;
          renderResult(data);
        } catch (err) {
          typing.remove();
          renderResult({ reply: "Не удалось обработать файл: " + err.message });
        }
        uploadInput.value = "";
      });
    }
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
      userModal.classList.add("open");
    }
    window.openUserModal = openUser;
    window.closeUserModal = () => userModal.classList.remove("open");
    userModal.addEventListener("click", (e) => { if (e.target === userModal) userModal.classList.remove("open"); });

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
        if (!data.items.length) { list.innerHTML = '<div class="a-muted">Пока нет уведомлений</div>'; return; }
        list.innerHTML = data.items.map((n) => {
          const d = new Date(n.created_at).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
          const unread = n.status !== "read" ? ' style="border-left:3px solid var(--accent)"' : "";
          return `<div class="notif-item"${unread}><div class="t">${(n.title || "").replace(/[<>&]/g, "")}</div><div class="x">${(n.text || "").replace(/[<>&]/g, "")}</div><div class="x">${d}</div></div>`;
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
      const fd = new FormData();
      fd.append("file", file);
      try {
        const res = await fetch("/chat/upload", { method: "POST", body: fd });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "Ошибка загрузки");
        renderProtocol(data);
        if (data.document_id) prependDoc(data.document_id, data.filename || file.name);
        window.toast("Документ загружен, протокол готов");
      } catch (e) {
        protoBody.innerHTML = `<div class="alert error">Не удалось обработать файл: ${escHtml(e.message)}</div>`;
        window.toast(e.message, "err");
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

    document.querySelectorAll("[data-route]").forEach((b) => {
      b.addEventListener("click", () => {
        const [from, to] = b.getAttribute("data-route").split("|");
        travelForm.elements["origin"].value = from;
        travelForm.elements["destination"].value = to;
      });
    });

    function card(o) {
      const h = Math.floor(o.duration_minutes / 60), m = o.duration_minutes % 60;
      const tr = o.transfers > 0 ? `пересадок: ${o.transfers}` : "без пересадок";
      return (
        `<div class="ticket-card">` +
        `<div class="tc-mode">${modeIcon[o.mode] || "🎫"}<span>${escHtml(modeRu[o.mode] || o.mode)}</span></div>` +
        `<div class="tc-main">` +
        `<div class="tc-time"><b>${fmtDateTime(o.depart_at)}</b> → <b>${fmtTime(o.arrive_at)}</b></div>` +
        `<div class="tc-sub muted">${o.origin} → ${o.destination} · ${h}ч ${String(m).padStart(2, "0")}м · ${tr} · ${escHtml(o.provider)}</div>` +
        `</div>` +
        `<div class="tc-price"><div class="price">${Math.round(o.price)} ${escHtml(o.currency)}</div>` +
        `<a class="btn small primary" href="${escHtml(o.url)}" target="_blank" rel="noopener">Купить</a></div>` +
        `</div>`
      );
    }

    travelForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const f = travelForm.elements;
      const origin = f["origin"].value.trim(), destination = f["destination"].value.trim();
      if (!origin || !destination) return;
      const params = new URLSearchParams({ origin, destination, transport: f["transport"].value });
      if (f["date"].value) params.set("date", f["date"].value);
      const budget = parseFloat(f["budget"].value);
      const prefs = Array.from(travelForm.querySelectorAll("input[name=pref]:checked")).map((c) => c.value);

      results.innerHTML = spinner("Ищу варианты…");
      countEl.textContent = "";
      try {
        let data = await window.api("GET", "/api/tickets/search?" + params.toString());
        if (!isNaN(budget) && budget > 0) data = data.filter((o) => o.price <= budget);
        if (prefs.includes("direct")) data = data.filter((o) => o.transfers === 0);
        if (prefs.includes("fastest")) data = data.slice().sort((a, b) => a.duration_minutes - b.duration_minutes);
        else data = data.slice().sort((a, b) => a.price - b.price);

        if (!data.length) {
          results.innerHTML = `<div class="empty-state"><div class="big">🤷</div>Под условия ничего не нашлось. Смягчите бюджет или предпочтения.</div>`;
          return;
        }
        countEl.textContent = `найдено: ${data.length}`;
        results.innerHTML = data.map(card).join("");
      } catch (err) {
        results.innerHTML = `<div class="alert error">${escHtml(err.message)}</div>`;
      }
    });
  }
})();
