/*
 * Shared UI layer for the AgriCloud-CI dashboard.
 *
 * Plain vanilla JavaScript, no build step. It provides:
 *   - apiGet(): typed JSON fetch with readable errors
 *   - mountShell(): sidebar + top bar + mobile nav (identical on every page)
 *   - formatting and badge helpers
 *   - tiny SVG sparkline / bar chart builders used by the Live page
 *
 * Each page supplies only its own content; the shell is injected once so the
 * three pages cannot drift apart visually.
 */
(function () {
  "use strict";

  const API_BASE = window.AGRI_API_BASE || "/api";

  const NAV = [
    { id: "index", href: "index.html", label: "Live telemetry", icon: "pulse" },
    { id: "advisory", href: "advisory.html", label: "Crop advisory", icon: "leaf" },
    { id: "history", href: "history.html", label: "History", icon: "clock" },
  ];

  const ICONS = {
    pulse: '<path d="M3 12h3l2.5-6 3.5 12 3-8 2 2h4"/>',
    leaf: '<path d="M5 19c0-8 6-14 14-14 0 8-6 14-14 14Z"/><path d="M5 19c3-3 6-5 9-6"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    refresh: '<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/>',
    droplet: '<path d="M12 3c3.5 4.2 6 7.1 6 10a6 6 0 1 1-12 0c0-2.9 2.5-5.8 6-10Z"/>',
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M19.1 4.9l-1.4 1.4M6.3 17.7l-1.4 1.4"/>',
    cloud: '<path d="M4 14a4 4 0 0 0 4 4h8a4 4 0 0 0 0-8 5.5 5.5 0 0 0-10.6 1.5A3.5 3.5 0 0 0 4 14Z"/>',
    chip: '<rect x="7" y="7" width="10" height="10" rx="2"/><path d="M10 3v2m4-2v2m-4 14v2m4-2v2M3 10h2m-2 4h2m14-4h2m-2 4h2"/>',
    arrow: '<path d="M5 12h14"/><path d="m13 6 6 6-6 6"/>',
    check: '<path d="m5 13 4 4L19 7"/>',
    alert: '<path d="M12 8v5"/><circle cx="12" cy="16.5" r="0.6"/>',
    chat: '<path d="M21 12a8 8 0 0 1-8 8H8l-4 3v-6.6A8 8 0 0 1 13 4a8 8 0 0 1 8 8Z"/>',
    send: '<path d="m5 12 14-8-4 16-4-6-6-2Z"/>',
  };

  function icon(name, className) {
    const path = ICONS[name] || "";
    return (
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" ' +
      'stroke-linecap="round" stroke-linejoin="round" class="' +
      (className || "h-4 w-4") +
      '" aria-hidden="true">' +
      path +
      "</svg>"
    );
  }

  function byId(id) {
    return document.getElementById(id);
  }

  function setText(id, value) {
    const element = byId(id);
    if (element) element.textContent = value;
  }

  function setHidden(element, hidden) {
    if (!element) return;
    element.classList.toggle("hidden", Boolean(hidden));
  }

  /** GET a JSON endpoint and throw a readable Error on failure. */
  async function apiGet(path) {
    const response = await fetch(API_BASE + path, {
      headers: { Accept: "application/json" },
    });

    let body = null;
    try {
      body = await response.json();
    } catch (err) {
      body = null;
    }

    if (!response.ok) {
      const detail = body && body.detail;
      let message = "Request failed (" + response.status + ")";
      if (typeof detail === "string") {
        message = detail;
      } else if (Array.isArray(detail)) {
        message = detail.map((item) => item.msg || "invalid value").join("; ");
      }
      throw new Error(message);
    }
    return body;
  }

  /** POST JSON to an endpoint and throw a readable Error on failure. */
  async function apiPost(path, body) {
    const response = await fetch(API_BASE + path, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(body),
    });

    let data = null;
    try {
      data = await response.json();
    } catch (err) {
      data = null;
    }

    if (!response.ok) {
      const detail = data && data.detail;
      let message = "Request failed (" + response.status + ")";
      if (typeof detail === "string") {
        message = detail;
      } else if (Array.isArray(detail)) {
        message = detail.map((item) => item.msg || "invalid value").join("; ");
      }
      throw new Error(message);
    }
    return data;
  }

  /** Show the shared red error banner at the top of the content area. */
  function showError(message) {
    setText("error-text", message);
    setHidden(byId("error-banner"), false);
  }

  function clearError() {
    setHidden(byId("error-banner"), true);
  }

  // -------------------------------------------------------------------------
  // Badges and formatting
  // -------------------------------------------------------------------------
  function viabilityBadge(viability) {
    const styles = {
      Optimal: "bg-acid/15 text-acid ring-1 ring-inset ring-acid/30",
      Marginal: "bg-amber-400/15 text-amber-300 ring-1 ring-inset ring-amber-400/30",
      Unsuitable: "bg-rose-500/15 text-rose-300 ring-1 ring-inset ring-rose-500/30",
    };
    return styles[viability] || "bg-white/5 text-zinc-300 ring-1 ring-inset ring-white/10";
  }

  function sourceBadge(source) {
    if (source === "satellite_fallback") {
      return {
        label: "Satellite fallback",
        classes: "bg-violet-500/15 text-violet-300 ring-1 ring-inset ring-violet-500/30",
      };
    }
    return {
      label: "ESP32 edge node",
      classes: "bg-acid/10 text-acid ring-1 ring-inset ring-acid/25",
    };
  }

  function metricStatusChip(status) {
    const styles = {
      optimal: "bg-acid/15 text-acid",
      marginal_low: "bg-amber-400/15 text-amber-300",
      marginal_high: "bg-amber-400/15 text-amber-300",
      unsuitable_low: "bg-rose-500/15 text-rose-300",
      unsuitable_high: "bg-rose-500/15 text-rose-300",
    };
    return {
      classes: styles[status] || "bg-white/5 text-zinc-300",
      label: (status || "—").replace("_", " "),
    };
  }

  function formatNumber(value, digits) {
    const number = Number(value);
    return Number.isFinite(number) ? number.toFixed(digits === undefined ? 1 : digits) : "—";
  }

  function formatTime(iso) {
    if (!iso) return "—";
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return iso;
    return date.toLocaleString(undefined, {
      day: "2-digit",
      month: "short",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  }

  function escapeHtml(value) {
    return String(value === null || value === undefined ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  // -------------------------------------------------------------------------
  // Charts
  // -------------------------------------------------------------------------
  let gradientCounter = 0;

  /** Smooth-ish area sparkline. Returns "" when there are fewer than 2 points. */
  function sparkline(values, options) {
    const opts = options || {};
    const colour = opts.colour || "#c6f24e";
    const data = (values || []).filter((value) => Number.isFinite(Number(value)));
    if (data.length < 2) return "";

    const width = 560;
    const height = 150;
    const pad = 10;
    const numbers = data.map(Number);
    const min = Math.min.apply(null, numbers);
    const max = Math.max.apply(null, numbers);
    const span = max - min || 1;
    const step = (width - pad * 2) / (numbers.length - 1);

    const points = numbers.map(function (value, index) {
      return [
        pad + index * step,
        height - pad - ((value - min) / span) * (height - pad * 2),
      ];
    });

    const line = points
      .map(function (point, index) {
        return (index === 0 ? "M" : "L") + point[0].toFixed(1) + " " + point[1].toFixed(1);
      })
      .join(" ");
    const area =
      line +
      " L " + points[points.length - 1][0].toFixed(1) + " " + height +
      " L " + points[0][0].toFixed(1) + " " + height + " Z";

    gradientCounter += 1;
    const gradientId = "agri-gradient-" + gradientCounter;
    const last = points[points.length - 1];

    return (
      '<svg viewBox="0 0 ' + width + " " + height + '" class="h-full w-full" ' +
      'preserveAspectRatio="none" role="img" aria-label="Trend of the last ' + numbers.length + ' readings">' +
      "<defs><linearGradient id=\"" + gradientId + "\" x1=\"0\" y1=\"0\" x2=\"0\" y2=\"1\">" +
      '<stop offset="0%" stop-color="' + colour + '" stop-opacity="0.30"/>' +
      '<stop offset="100%" stop-color="' + colour + '" stop-opacity="0"/>' +
      "</linearGradient></defs>" +
      '<path d="' + area + '" fill="url(#' + gradientId + ')"/>' +
      '<path d="' + line + '" fill="none" stroke="' + colour + '" stroke-width="2.4" ' +
      'stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/>' +
      '<circle cx="' + last[0].toFixed(1) + '" cy="' + last[1].toFixed(1) + '" r="3.5" fill="' + colour + '"/>' +
      "</svg>"
    );
  }

  /** Vertical bars scaled to the maximum value. Returns "" with no data. */
  function barChart(values, options) {
    const opts = options || {};
    const numbers = (values || []).map(Number).filter(Number.isFinite);
    if (!numbers.length) return "";
    const max = Math.max.apply(null, numbers.concat([1]));

    return numbers
      .map(function (value) {
        const height = Math.max(6, (value / max) * 100);
        const tone =
          opts.threshold !== undefined && value < opts.threshold
            ? "bg-amber-400/70"
            : "bg-acid/80";
        return (
          '<div class="flex h-full flex-1 flex-col justify-end" title="' + value + '">' +
          '<div class="rounded-t-sm ' + tone + '" style="height:' + height.toFixed(1) + '%"></div>' +
          "</div>"
        );
      })
      .join("");
  }

  // -------------------------------------------------------------------------
  // App shell
  // -------------------------------------------------------------------------
  function navLink(item, page, mobile) {
    const active = item.id === page;
    const base = mobile
      ? "flex flex-1 flex-col items-center gap-1 rounded-xl px-2 py-2 text-[10px] font-medium transition"
      : "flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium transition";
    const tone = active
      ? "bg-acid/10 text-acid ring-1 ring-inset ring-acid/20"
      : "text-zinc-400 hover:bg-white/[0.04] hover:text-zinc-100";
    const iconClass = mobile ? (active ? "h-5 w-5" : "h-5 w-5 opacity-80") : "h-[18px] w-[18px]";
    return (
      '<a href="' + item.href + '" data-nav="' + item.id + '" class="' + base + " " + tone +
      '"' + (active ? ' aria-current="page"' : "") + ">" +
      icon(item.icon, iconClass) +
      "<span>" + item.label + "</span>" +
      "</a>"
    );
  }

  function sidebarHtml(page) {
    const links = NAV.map(function (item) {
      return navLink(item, page, false);
    }).join("");

    return (
      '<div class="flex h-full flex-col gap-6 px-4 py-5">' +
        '<a href="index.html" class="flex items-center gap-2.5 px-1">' +
          '<span class="grid h-10 w-10 place-items-center rounded-2xl bg-acid/10 text-acid ring-1 ring-inset ring-acid/20">' +
            icon("leaf", "h-5 w-5") +
          "</span>" +
          '<span class="font-display text-[15px] font-semibold tracking-tight text-white">' +
            'AGRICLOUD<span class="text-acid">·CI</span>' +
            '<span class="mt-0.5 block text-[10px] font-normal uppercase tracking-[0.18em] text-zinc-500">Precision advisory</span>' +
          "</span>" +
        "</a>" +
        '<div class="rounded-2xl border border-white/5 bg-ink-900/80 p-4">' +
          '<p class="text-[10px] uppercase tracking-[0.18em] text-zinc-500">Field location</p>' +
          '<p id="shell-location" class="mt-1.5 font-display text-sm text-zinc-200">—</p>' +
          '<p class="mt-2.5 flex items-center gap-2 text-[11px] text-zinc-500">' +
            '<span id="api-dot" class="h-1.5 w-1.5 rounded-full bg-zinc-600"></span>' +
            '<span id="api-status">Checking API…</span>' +
          "</p>" +
        "</div>" +
        '<nav class="flex flex-col gap-1" aria-label="Main navigation">' +
          '<p class="px-3 pb-1 text-[10px] uppercase tracking-[0.18em] text-zinc-600">Workspace</p>' +
          links +
        "</nav>" +
        '<div class="mt-auto space-y-3">' +
          '<div class="rounded-2xl border border-white/5 bg-ink-900/80 p-4">' +
            '<div class="flex items-center justify-between">' +
              '<p class="text-[10px] uppercase tracking-[0.18em] text-zinc-500">Fusion mode</p>' +
              icon("chip", "h-4 w-4 text-acid/70") +
            "</div>" +
            '<p id="mode-label" class="mt-1.5 font-display text-lg text-white">—</p>' +
            '<p id="mode-hint" class="mt-1 text-[11px] leading-relaxed text-zinc-500">Reading server configuration…</p>' +
          "</div>" +
          '<div class="rounded-2xl border border-acid/20 bg-acid/[0.05] p-4">' +
            '<p class="text-[10px] uppercase tracking-[0.18em] text-acid/80">Edge node</p>' +
            '<p class="mt-1.5 text-sm text-zinc-200">ESP32 · DHT11 · probe</p>' +
            '<p class="mt-1 text-[11px] leading-relaxed text-zinc-500">5 samples every 5 minutes over HTTPS.</p>' +
          "</div>" +
        "</div>" +
      "</div>"
    );
  }

  function topbarHtml(title, subtitle) {
    return (
      '<div class="flex h-16 items-center justify-between gap-3 border-b border-white/5 bg-ink-950/80 px-4 backdrop-blur lg:px-8">' +
        '<div class="flex min-w-0 items-center gap-3">' +
          '<a href="index.html" class="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-acid/10 text-acid ring-1 ring-inset ring-acid/20 lg:hidden">' +
            icon("leaf", "h-4 w-4") +
          "</a>" +
          '<div class="min-w-0">' +
            '<h1 class="font-display truncate text-[17px] font-semibold leading-tight text-white">' +
              escapeHtml(title) + "</h1>" +
            '<p class="hidden truncate text-[11px] text-zinc-500 sm:block">' + escapeHtml(subtitle) + "</p>" +
          "</div>" +
        "</div>" +
        '<div class="flex items-center gap-2">' +
          '<span class="hidden items-center gap-2 rounded-full border border-white/5 bg-ink-900 px-3 py-1.5 text-[11px] text-zinc-400 sm:flex">' +
            '<span class="h-1.5 w-1.5 rounded-full bg-acid shadow-[0_0_8px_rgba(198,242,78,0.8)]"></span>' +
            "30 s auto-refresh" +
          "</span>" +
          '<button id="shell-refresh" type="button" class="flex items-center gap-2 rounded-xl border border-white/10 bg-white/[0.04] px-3 py-2 text-xs font-semibold text-zinc-200 transition hover:border-acid/40 hover:text-acid disabled:opacity-50">' +
            icon("refresh", "h-4 w-4") +
            '<span class="hidden sm:inline">Refresh</span>' +
          "</button>" +
        "</div>" +
      "</div>"
    );
  }

  function mobileNavHtml(page) {
    return (
      '<nav class="fixed inset-x-0 bottom-0 z-30 flex items-stretch gap-1 border-t border-white/5 bg-ink-950/95 px-2 py-1.5 backdrop-blur" aria-label="Mobile navigation">' +
      NAV.map(function (item) {
        return navLink(item, page, true);
      }).join("") +
      "</nav>"
    );
  }

  async function refreshShellStatus() {
    const dot = byId("api-dot");
    try {
      const health = await apiGet("/health");
      setText("api-status", "API online");
      if (dot) {
        dot.className = "h-1.5 w-1.5 rounded-full bg-acid shadow-[0_0_8px_rgba(198,242,78,0.8)]";
      }
      setText("mode-label", health.simulate ? "Demo mode" : "Field mode");
      setText(
        "mode-hint",
        health.simulate
          ? "SIMULATE=true · synthetic ESP32 readings"
          : "Live device uploads with satellite fallback"
      );
      const location = health.location || {};
      if (location.latitude !== undefined) {
        setText(
          "shell-location",
          formatNumber(location.latitude, 4) + "° N · " + formatNumber(location.longitude, 4) + "° E"
        );
      }
    } catch (error) {
      setText("api-status", "API offline");
      if (dot) dot.className = "h-1.5 w-1.5 rounded-full bg-rose-500";
      setText("mode-label", "Offline");
      setText("mode-hint", "The dashboard cannot reach /api/health.");
    }
  }

  // -------------------------------------------------------------------------
  // Assistant chat
  // -------------------------------------------------------------------------
  // The panel is injected on every page. It talks to POST /api/assistant, which
  // answers from the same fused reading and crop rules as the rest of the UI.
  const CHAT_STARTERS = [
    "What are the current conditions?",
    "Should I irrigate Wheat?",
    "Which crop suits today?",
  ];

  function chatMarkup() {
    return (
      '<button id="agri-chat-button" type="button" aria-label="Open AgriCloud assistant" aria-expanded="false" ' +
      'class="fixed bottom-20 right-4 z-40 grid h-14 w-14 place-items-center rounded-2xl bg-acid text-ink-950 ' +
      'shadow-[0_14px_40px_-12px_rgba(198,242,78,0.65)] transition hover:brightness-105 lg:bottom-6 lg:right-6">' +
      icon("chat", "h-6 w-6") +
      "</button>" +
      '<section id="agri-chat-panel" aria-label="AgriCloud assistant" ' +
      'class="fixed bottom-36 right-3 z-40 hidden w-[min(92vw,384px)] flex-col overflow-hidden rounded-3xl ' +
      'border border-white/10 bg-ink-900/95 shadow-2xl backdrop-blur lg:bottom-24 lg:right-6" ' +
      'style="height:min(70vh,520px)">' +
        '<div class="flex items-center justify-between gap-2 border-b border-white/5 bg-white/[0.02] px-4 py-3">' +
          '<div class="flex items-center gap-2.5">' +
            '<span class="grid h-8 w-8 place-items-center rounded-xl bg-acid/10 text-acid">' +
              icon("leaf", "h-4 w-4") +
            "</span>" +
            "<div>" +
              '<p class="text-sm font-semibold text-white">AgriCloud Assistant</p>' +
              '<p class="text-[10px] uppercase tracking-[0.12em] text-zinc-500">live data · crops · history</p>' +
            "</div>" +
          "</div>" +
          '<button id="agri-chat-close" type="button" aria-label="Close assistant" ' +
          'class="rounded-lg px-2 py-1 text-zinc-500 transition hover:bg-white/5 hover:text-zinc-200">✕</button>' +
        "</div>" +
        '<div id="agri-chat-messages" class="flex-1 space-y-3 overflow-y-auto px-3 py-3 text-sm" aria-live="polite"></div>' +
        '<div id="agri-chat-suggestions" class="flex flex-wrap gap-1.5 border-t border-white/5 px-3 py-2"></div>' +
        '<form id="agri-chat-form" class="flex items-center gap-2 border-t border-white/5 px-3 py-3">' +
          '<input id="agri-chat-input" type="text" autocomplete="off" maxlength="500" ' +
          'placeholder="Ask about soil, crops, irrigation…" ' +
          'class="min-w-0 flex-1 rounded-xl border border-white/10 bg-ink-950/80 px-3 py-2.5 text-sm ' +
          'text-zinc-100 outline-none placeholder:text-zinc-600 focus:border-acid/50 focus:ring-2 focus:ring-acid/15" />' +
          '<button type="submit" aria-label="Send message" ' +
          'class="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-acid text-ink-950 transition hover:brightness-105">' +
            icon("send", "h-4 w-4") +
          "</button>" +
        "</form>" +
      "</section>"
    );
  }

  function chatAdd(role, text) {
    const box = byId("agri-chat-messages");
    if (!box) return null;
    const bubble = document.createElement("div");
    bubble.className =
      role === "user"
        ? "ml-auto max-w-[85%] whitespace-pre-line rounded-2xl rounded-br-md bg-acid px-3 py-2 " +
          "text-[13px] leading-relaxed text-ink-950"
        : "max-w-[92%] whitespace-pre-line rounded-2xl rounded-bl-md border border-white/5 " +
          "bg-white/[0.04] px-3 py-2 text-[13px] leading-relaxed text-zinc-200";
    bubble.textContent = text;
    box.appendChild(bubble);
    box.scrollTop = box.scrollHeight;
    return bubble;
  }

  function chatSuggestions(items) {
    const box = byId("agri-chat-suggestions");
    if (!box) return;
    box.innerHTML = "";
    (items || []).slice(0, 3).forEach(function (text) {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.textContent = text;
      chip.className =
        "rounded-full border border-white/10 bg-white/[0.03] px-2.5 py-1 text-[11px] text-zinc-300 " +
        "transition hover:border-acid/40 hover:text-acid";
      chip.addEventListener("click", function () {
        chatSend(text);
      });
      box.appendChild(chip);
    });
  }

  async function chatSend(text) {
    const input = byId("agri-chat-input");
    if (!input || !text) return;
    chatAdd("user", text);
    chatSuggestions([]);
    const typing = chatAdd("assistant", "…");
    input.value = "";

    try {
      const cropSelect = byId("crop-select");
      const payload = { message: text };
      if (cropSelect && cropSelect.value) payload.crop = cropSelect.value;

      const data = await apiPost("/assistant", payload);
      if (typing) typing.remove();
      chatAdd("assistant", data.reply || "I do not have an answer for that yet.");
      chatSuggestions(data.suggestions);
    } catch (error) {
      if (typing) typing.remove();
      chatAdd("assistant", "I could not reach the API: " + error.message);
      chatSuggestions(CHAT_STARTERS);
    }
  }

  function mountChat() {
    if (byId("agri-chat-button")) return;
    const holder = document.createElement("div");
    holder.innerHTML = chatMarkup();
    while (holder.firstChild) document.body.appendChild(holder.firstChild);

    const button = byId("agri-chat-button");
    const panel = byId("agri-chat-panel");
    const input = byId("agri-chat-input");

    function setOpen(state) {
      panel.classList.toggle("hidden", !state);
      panel.classList.toggle("flex", state);
      button.setAttribute("aria-expanded", String(state));
      if (!state) return;

      if (!byId("agri-chat-messages").childElementCount) {
        chatAdd(
          "assistant",
          "Hi! Ask me about live soil moisture, whether a crop suits today, irrigation " +
            "timing, crop ranges, the satellite fallback or the stored history."
        );
        chatSuggestions(CHAT_STARTERS);
      }
      input.focus();
    }

    button.addEventListener("click", function () {
      setOpen(panel.classList.contains("hidden"));
    });
    byId("agri-chat-close").addEventListener("click", function () {
      setOpen(false);
    });
    byId("agri-chat-form").addEventListener("submit", function (event) {
      event.preventDefault();
      const text = input.value.trim();
      if (text) chatSend(text);
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && !panel.classList.contains("hidden")) setOpen(false);
    });
  }

  function mountShell(config) {
    const settings = config || {};
    const page = document.body.getAttribute("data-page");
    const sidebar = byId("sidebar");
    const topbar = byId("topbar");
    const mobile = byId("mobile-nav");

    if (sidebar) sidebar.innerHTML = sidebarHtml(page);
    if (topbar) topbar.innerHTML = topbarHtml(settings.title || "", settings.subtitle || "");
    if (mobile) mobile.innerHTML = mobileNavHtml(page);

    const refresh = byId("shell-refresh");
    if (refresh) {
      refresh.addEventListener("click", function () {
        window.dispatchEvent(new CustomEvent("agri:refresh"));
      });
    }

    refreshShellStatus();
    mountChat();
  }

  window.AgriUI = {
    apiGet: apiGet,
    mountShell: mountShell,
    byId: byId,
    setText: setText,
    setHidden: setHidden,
    showError: showError,
    clearError: clearError,
    viabilityBadge: viabilityBadge,
    sourceBadge: sourceBadge,
    metricStatusChip: metricStatusChip,
    formatNumber: formatNumber,
    formatTime: formatTime,
    escapeHtml: escapeHtml,
    sparkline: sparkline,
    barChart: barChart,
    icon: icon,
  };
})();
