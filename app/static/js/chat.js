/* ============================================================================
   Умный календарь — chat.js: чат-ассистент с карточками и действиями
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

    // ARCH-05: единые esc/fmt из core.js.
    const fmtDT = fmtDateTime;
    const fmtT = fmtTime;
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
        `<div class="a-slot"><div>⛔ ${c.owner_name ? `у ${esc(c.owner_name)}: ` : ""}<b>${esc(c.title)}</b> · ${fmtDT(c.start_at)}–${fmtT(c.end_at)} · приоритет ${c.priority}` +
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
      else if (card.kind === "followups") {
        // FN-05: предпросмотр встреч, которые будут созданы из протокола.
        el.innerHTML = `<div class="a-card-title">📅 ${esc(card.title)}</div>` +
          (d.events || []).map((e) =>
            `<div class="a-slot"><div><b>${fmtDT(e.start_at)}–${fmtT(e.end_at)}</b> ${esc(e.title)}` +
            `<div class="a-muted">${esc(LOC_RU[e.location_type] || e.location_type || "")}</div></div></div>`
          ).join("");
      }
      else if (card.kind === "tasks") el.innerHTML = `<div class="a-card-title">✅ Задачи</div><ul>` + (d.items || []).map((i) => `<li>${esc(i)}</li>`).join("") + "</ul>";
      else if (card.kind === "conflict") el.innerHTML = conflictHtml(d);
      else if (card.kind === "employee_availability") el.innerHTML = `<div class="a-card-title">🟢 ${esc(card.title)}</div>` + employeeAvailabilityHtml(d.items || []);
      else if (card.kind === "reschedule_plan") el.innerHTML = `<div class="a-card-title">🔀 План переноса</div><div class="a-card-row">«${esc((d.conflict||{}).title||"встреча")}»: ${fmtDT(d.old_start_at)} → <b>${fmtDT(d.start_at)}</b></div>`;
      else if (card.kind === "reminder") el.innerHTML = `<div class="a-card-title">⏰ Напоминание</div><div class="a-card-row">${esc((d.event||{}).title||"")} · за ${d.minutes_before} мин (${fmtDT(d.remind_at)})</div>`;
      else if (card.kind === "summary" || card.kind === "calendar") {
        const evs = d.events || d.upcoming || [];
        // BUG-25: отменённые показываем зачёркнутыми.
        el.innerHTML = `<div class="a-card-title">🗓️ ${esc(card.title)}</div>` +
          (evs.length ? evs.map((e) =>
            `<div class="a-slot${e.status === "cancelled" ? " cancelled" : ""}"><div><b>${fmtDT(e.start_at)}</b> ${esc(e.title)}${e.status === "cancelled" ? ' <span class="a-muted">(отменена)</span>' : ""}</div></div>`
          ).join("") : "<div class='a-muted'>Пусто</div>");
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
          const res = await fetch(window.APP_BASE + "/chat/upload", { method: "POST", body: fd });
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
})();
