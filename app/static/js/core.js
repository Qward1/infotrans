/* ============================================================================
   Умный календарь — core.js (ARCH-05: общий слой).
   BASE, тема, сайдбар, тосты, api, esc/fmt-хелперы, модальный хелпер,
   confirm-диалог, инлайн-ошибки форм, уведомления в топбаре.
   Подключается ПЕРВЫМ; страничные модули (calendar/chat/…) берут хелперы
   из window.smartcal и включаются по наличию якорного DOM-элемента.
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

  /* ---------------- Общие esc/fmt-хелперы (единственная реализация) ---------------- */
  const esc = (s) =>
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
  const fmtChatDate = (iso) => {
    if (!iso) return "";
    const d = new Date(iso);
    if (isNaN(d)) return "";
    return d.toLocaleString("ru-RU", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
  };
  const spinner = (label) => `<div class="loading"><span class="spinner"></span> ${esc(label || "Загрузка…")}</div>`;

  // Публичный namespace для страничных модулей.
  window.smartcal = {
    esc, pad, toLocalInput, fmtDateTime, fmtTime, fmtChatDate, spinner,
    clearFormError, emitEventChanged,
  };

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
          return `<div class="notif-item"${unread}><div class="t">${esc(n.title || "")}</div><div class="x">${esc(n.text || "")}</div><div class="x">${d}</div></div>`;
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
})();
