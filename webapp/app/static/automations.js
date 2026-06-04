(() => {
  const PREFIX = window.location.pathname.startsWith("/chat/") ? "/chat" : "";

  function csrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute("content") : "";
  }

  function fmtLocalIso(s) {
    // Convert "2026-06-05T14:30" → ISO UTC.
    if (!s) return null;
    const d = new Date(s);
    if (isNaN(d.getTime())) return null;
    return d.toISOString();
  }

  async function api(path, opts = {}) {
    opts.headers = Object.assign(
      { "Content-Type": "application/json", "X-CSRF-Token": csrfToken() },
      opts.headers || {},
    );
    if (opts.body && typeof opts.body !== "string") {
      opts.body = JSON.stringify(opts.body);
    }
    const r = await fetch(PREFIX + path, opts);
    const data = await r.json().catch(() => ({}));
    if (!r.ok) {
      throw new Error(data.error || r.statusText || "request failed");
    }
    return data;
  }

  function showStatus(el, text, kind = "ok") {
    if (!el) return;
    el.textContent = text;
    el.className = "form-status form-status-" + kind;
    setTimeout(() => { el.textContent = ""; el.className = "form-status"; }, 4000);
  }

  // ---- Reminder form ----

  document.getElementById("form-reminder")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.currentTarget;
    const status = document.getElementById("reminder-status");
    const fd = new FormData(f);
    const schedule_type = fd.get("schedule_type");
    const weekdays = schedule_type === "weekly"
      ? Array.from(f.querySelectorAll('input[name="weekday"]:checked')).map(i => parseInt(i.value, 10))
      : null;
    let run_at = null;
    let time_of_day = null;
    if (schedule_type === "one_time") {
      run_at = fmtLocalIso(fd.get("run_at_local"));
      if (!run_at) { showStatus(status, "Укажи дату и время", "err"); return; }
    } else {
      time_of_day = fd.get("time_of_day") || "09:00";
    }
    try {
      await api("/api/jobs", {
        method: "POST",
        body: {
          title: fd.get("title"),
          kind: "reminder",
          schedule_type,
          run_at,
          time_of_day,
          weekdays,
          channel: fd.get("channel"),
          payload: {
            message: fd.get("message"),
            context: fd.get("context") || null,
          },
        },
      });
      showStatus(status, "Создано", "ok");
      setTimeout(() => window.location.reload(), 600);
    } catch (err) {
      showStatus(status, err.message, "err");
    }
  });

  // ---- Digest form ----

  document.getElementById("form-digest")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.currentTarget;
    const status = document.getElementById("digest-status");
    const fd = new FormData(f);
    try {
      await api("/api/jobs", {
        method: "POST",
        body: {
          title: fd.get("title") || "Утренний дайджест",
          kind: "morning_digest",
          schedule_type: "daily",
          time_of_day: fd.get("time_of_day") || "09:00",
          channel: fd.get("channel"),
          payload: {
            include_memory: !!fd.get("include_memory"),
            include_recent_history: !!fd.get("include_recent_history"),
            include_tasks: !!fd.get("include_tasks"),
            include_email: false,
            include_calendar: false,
          },
        },
      });
      showStatus(status, "Создано", "ok");
      setTimeout(() => window.location.reload(), 600);
    } catch (err) {
      showStatus(status, err.message, "err");
    }
  });

  // ---- Row actions ----

  async function postJobAction(id, action) {
    try {
      await api(`/api/jobs/${id}/${action}`, { method: "POST" });
      window.location.reload();
    } catch (err) {
      alert(err.message);
    }
  }

  document.querySelectorAll(".btn-disable").forEach((b) => {
    b.addEventListener("click", () => postJobAction(b.dataset.id, "disable"));
  });
  document.querySelectorAll(".btn-enable").forEach((b) => {
    b.addEventListener("click", () => postJobAction(b.dataset.id, "enable"));
  });
  document.querySelectorAll(".btn-delete").forEach((b) => {
    b.addEventListener("click", () => {
      if (confirm("Удалить задачу?")) {
        postJobAction(b.dataset.id, "delete");
      }
    });
  });
  document.querySelectorAll(".btn-run-now").forEach((b) => {
    b.addEventListener("click", async () => {
      try {
        const r = await api(`/api/jobs/${b.dataset.id}/run-now`, { method: "POST" });
        alert("Запущено: " + (r.result?.status || "?"));
        window.location.reload();
      } catch (err) {
        alert(err.message);
      }
    });
  });
})();
