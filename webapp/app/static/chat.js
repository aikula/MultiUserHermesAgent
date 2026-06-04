(() => {
  const log = document.getElementById("chat-log");
  const form = document.getElementById("chat-form");
  const input = document.getElementById("chat-input");
  const sendBtn = document.getElementById("chat-send");
  const usageEl = document.getElementById("chat-usage");

  const PREFIX = window.location.pathname.startsWith("/chat/") ? "/chat" : "";

  let busy = false;

  // Get CSRF token from meta tag
  function getCsrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute("content") : "";
  }

  // Optional prefill from ?prefill=... query string (e.g. from Files "Ask agent")
  // or ?skill=name (from the Skills tab — wraps the user's request in a marker).
  (function applyPrefill() {
    const params = new URLSearchParams(window.location.search);
    const prefill = params.get("prefill");
    if (prefill) {
      input.value = prefill;
      // Strip the query so a refresh doesn't keep reapplying
      const url = new URL(window.location.href);
      url.searchParams.delete("prefill");
      window.history.replaceState({}, "", url.pathname + (url.search || "") + url.hash);
      // Focus so the user can just press Enter
      setTimeout(() => input.focus(), 0);
      return;
    }
    const skill = params.get("skill");
    if (skill) {
      // The Skills tab stashes a structured prefill in localStorage; fall back
      // to the bare marker if it's missing (e.g. user typed ?skill= by hand).
      let stored = null;
      try {
        const raw = localStorage.getItem("hermes_prefill");
        if (raw) {
          const data = JSON.parse(raw);
          if (data && data.marker && Date.now() - (data.ts || 0) < 60_000) {
            stored = data;
          }
          localStorage.removeItem("hermes_prefill");
        }
      } catch (e) { /* ignore */ }
      const marker = stored ? stored.marker : "[Используй навык: " + skill + "]";
      input.value = marker + "\n\n";
      const url = new URL(window.location.href);
      url.searchParams.delete("skill");
      window.history.replaceState({}, "", url.pathname + (url.search || "") + url.hash);
      setTimeout(() => {
        input.focus();
        // Place caret right after the marker so the user types their request.
        input.setSelectionRange(marker.length + 1, marker.length + 1);
      }, 0);
    }
  })();

  function escapeHtml(s) {
    return s.replace(/[&<>"']/g, c => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  function renderMessage(role, content) {
    const div = document.createElement("div");
    div.className = "msg msg-" + role;
    div.innerHTML = `<div class="msg-role">${role === "user" ? "Вы" : "Hermes"}</div><div class="msg-body">${escapeHtml(content)}</div>`;
    log.appendChild(div);
    log.scrollTop = log.scrollHeight;
  }

  function renderApprovalCard(content, approval) {
    // Render the text part
    renderMessage("assistant", content);

    // Render approval card
    const card = document.createElement("div");
    card.className = "msg msg-assistant msg-approval";
    card.innerHTML = `
      <div class="msg-role">Hermes</div>
      <div class="msg-body">
        <div class="approval-card">
          <div class="approval-action">${escapeHtml(approval.action_type)}</div>
          <div class="approval-payload">${escapeHtml(JSON.stringify(approval.payload, null, 2))}</div>
          <div class="approval-buttons">
            <button class="btn-approve" data-intent="${approval.intent_id}">✅ Подтвердить</button>
            <button class="btn-reject" data-intent="${approval.intent_id}">❌ Отменить</button>
          </div>
          <div class="approval-status"></div>
        </div>
      </div>
    `;

    // Add event listeners
    const approveBtn = card.querySelector(".btn-approve");
    const rejectBtn = card.querySelector(".btn-reject");
    const statusEl = card.querySelector(".approval-status");

    approveBtn.addEventListener("click", async () => {
      approveBtn.disabled = true;
      rejectBtn.disabled = true;
      statusEl.textContent = "⏳ Выполняю...";
      try {
        const r = await fetch(PREFIX + "/api/approve", {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-CSRF-Token": getCsrfToken() },
          body: JSON.stringify({ intent_id: approval.intent_id }),
        });
        if (r.ok) {
          statusEl.textContent = "✅ Выполнено";
          card.classList.add("approval-done");
        } else {
          const err = await r.json().catch(() => ({ detail: r.statusText }));
          statusEl.textContent = `⚠️ ${err.detail || "ошибка"}`;
          approveBtn.disabled = false;
          rejectBtn.disabled = false;
        }
      } catch (e) {
        statusEl.textContent = `⚠️ ${escapeHtml(e.message)}`;
        approveBtn.disabled = false;
        rejectBtn.disabled = false;
      }
    });

    rejectBtn.addEventListener("click", async () => {
      approveBtn.disabled = true;
      rejectBtn.disabled = true;
      statusEl.textContent = "❌ Отменяю...";
      try {
        const r = await fetch(PREFIX + "/api/reject", {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-CSRF-Token": getCsrfToken() },
          body: JSON.stringify({ intent_id: approval.intent_id }),
        });
        if (r.ok) {
          statusEl.textContent = "❌ Отменено";
          card.classList.add("approval-done");
        }
      } catch (e) {
        statusEl.textContent = `⚠️ ${escapeHtml(e.message)}`;
        approveBtn.disabled = false;
        rejectBtn.disabled = false;
      }
    });

    log.appendChild(card);
    log.scrollTop = log.scrollHeight;
  }

  async function loadHistory() {
    try {
      const r = await fetch(PREFIX + "/api/history");
      if (r.status === 401) { window.location.href = PREFIX + "/login"; return; }
      const msgs = await r.json();
      log.innerHTML = "";
      if (msgs.length === 0) {
        log.innerHTML = '<div class="muted chat-empty">Начните диалог ↓</div>';
      } else {
        msgs.forEach(m => renderMessage(m.role, m.content));
      }
    } catch (e) {
      log.innerHTML = `<div class="alert">Ошибка загрузки: ${escapeHtml(e.message)}</div>`;
    }
  }

  input.addEventListener("keydown", e => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      form.requestSubmit();
    }
  });

  form.addEventListener("submit", async e => {
    e.preventDefault();
    if (busy) return;
    const content = input.value.trim();
    if (!content) return;
    busy = true;
    sendBtn.disabled = true;
    input.value = "";
    renderMessage("user", content);
    const pending = document.createElement("div");
    pending.className = "msg msg-assistant msg-pending";
    pending.innerHTML = '<div class="msg-role">Hermes</div><div class="msg-body"><span class="dot"></span><span class="dot"></span><span class="dot"></span></div>';
    log.appendChild(pending);
    log.scrollTop = log.scrollHeight;
    try {
      const r = await fetch(PREFIX + "/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": getCsrfToken() },
        body: JSON.stringify({ content }),
      });
      pending.remove();
      if (r.status === 401) { window.location.href = PREFIX + "/login"; return; }
      if (!r.ok) {
        const err = await r.json().catch(() => ({ detail: r.statusText }));
        renderMessage("assistant", `⚠️ ${err.detail || "ошибка " + r.status}`);
      } else {
        const data = await r.json();
        if (data.approval) {
          renderApprovalCard(data.content, data.approval);
        } else {
          renderMessage("assistant", data.content);
        }
        if (data.usage) {
          usageEl.textContent = `промпт ${data.usage.prompt_tokens} → ответ ${data.usage.completion_tokens} (всего ${data.usage.total_tokens})`;
        }
      }
    } catch (e) {
      pending.remove();
      renderMessage("assistant", `⚠️ ${escapeHtml(e.message)}`);
    } finally {
      busy = false;
      sendBtn.disabled = false;
      input.focus();
    }
  });

  loadHistory();
})();
