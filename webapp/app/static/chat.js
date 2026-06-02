(() => {
  const log = document.getElementById("chat-log");
  const form = document.getElementById("chat-form");
  const input = document.getElementById("chat-input");
  const sendBtn = document.getElementById("chat-send");
  const usageEl = document.getElementById("chat-usage");

  const PREFIX = window.location.pathname.startsWith("/chat/") ? "/chat" : "";

  let busy = false;

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
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content }),
      });
      pending.remove();
      if (r.status === 401) { window.location.href = PREFIX + "/login"; return; }
      if (!r.ok) {
        const err = await r.json().catch(() => ({ detail: r.statusText }));
        renderMessage("assistant", `⚠️ ${err.detail || "ошибка " + r.status}`);
      } else {
        const data = await r.json();
        renderMessage("assistant", data.content);
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
