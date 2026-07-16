/* ============================================================================
   Умный календарь — admin.js: модалка пользователя
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
})();
