/* ============================================================================
   Умный календарь — documents.js: загрузка документов и протоколы
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
  const { esc, pad, toLocalInput, fmtDateTime, fmtTime, fmtChatDate, spinner, icon, clearFormError, emitEventChanged } = window.smartcal;

  const escHtml = esc; // ARCH-05: единый esc

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
      if (data.filename) html += `<div class="a-card-title" style="font-size:16px;">${icon("file-text", "ic-18")} ${escHtml(data.filename)}</div>`;
      if (warns.length) html += `<div class="a-warn">${icon("warning", "ic-14")} ${escHtml(warns.join("; "))}</div>`;
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
      // FN-05: если бэкенд прислал предпросмотр с датами — показываем его.
      const fuCard = (data.cards || []).find((c) => c.kind === "followups");
      const fuEvents = fuCard && fuCard.data ? fuCard.data.events || [] : null;
      if (fuEvents && fuEvents.length)
        html += `<div class="proto-sec"><b>Будут созданы встречи</b>${ul(fuEvents, (m) => m.title + " · " + fmtDateTime(m.start_at))}</div>`;
      else if (fu.length)
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
            b.textContent = res.message || "Готово";
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
        `<div class="prio" style="background:var(--chip-bg); color:var(--accent-strong);">${icon("file-text", "ic-18")}</div>` +
        `<div class="body"><div class="title">${escHtml(filename)}</div>` +
        `<div class="sub muted">только что</div></div>` +
        `<button class="btn small primary" data-make-protocol="${docId}">Сформировать протокол</button>` +
        `<button class="btn icon ghost" data-delete-doc="${docId}" title="Удалить документ" aria-label="Удалить документ">×</button>`;
      docList.insertBefore(row, docList.firstChild);
      bindProtocolButtons();
    }

    async function uploadFile(file) {
      showLoading();
      showPickedFile(file.name);
      const fd = new FormData();
      fd.append("file", file);
      try {
        const res = await fetch(window.APP_BASE + "/chat/upload", { method: "POST", body: fd });
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
      // FN-12: удаление документа с подтверждением.
      document.querySelectorAll("[data-delete-doc]").forEach((b) => {
        if (b.dataset.bound) return;
        b.dataset.bound = "1";
        b.addEventListener("click", async () => {
          const ok = await window.uiConfirm("Удалить документ? Файл и извлечённый текст будут стёрты.", { danger: true });
          if (!ok) return;
          try {
            await window.api("DELETE", `/api/documents/${b.getAttribute("data-delete-doc")}`);
            const row = b.closest("[data-doc-id]");
            if (row) row.remove();
            window.toast("Документ удалён");
          } catch (e) { window.toast(e.message, "err"); }
        });
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
})();
