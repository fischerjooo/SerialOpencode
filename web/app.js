(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const ui = {
    connect: $("connectBtn"),
    label: $("connLabel"),
    dot: $("connDot"),
    sessions: $("sessions"),
    title: $("sessionTitle"),
    meta: $("sessionMeta"),
    messages: $("messages"),
    empty: $("empty"),
    stage: $("stage"),
    prompt: $("prompt"),
    send: $("sendBtn"),
    sidebar: $("sidebar"),
    protocol: $("protocol"),
    notification: $("notification"),
  };
  let port = null,
    writer = null,
    reader = null,
    readLoopPromise = null,
    connected = false,
    closing = false;
  let receiveBuffer = "",
    protocolMode = "unknown",
    sessionId = null,
    sessionTitle = "",
    currentPlain = null,
    historyMode = false,
    toastTimer = null,
    initTimer = null;
  let sessions = [],
    messageNodes = new Map();
  let bridgeVersion = 0,
    writeQueue = Promise.resolve(),
    sending = false;
  const requests = new Map(),
    drafts = new Map();
  let reconnectTimer = null,
    reconnectAttempts = 0,
    manualDisconnect = false;
  const encoder = new TextEncoder(),
    decoder = new TextDecoder();
  function notice(text) {
    ui.notification.textContent = String(text).slice(0, 280);
    ui.notification.classList.add("visible");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(
      () => ui.notification.classList.remove("visible"),
      4800,
    );
  }
  function updateConnection(isOn, label) {
    connected = isOn;
    ui.label.textContent = label || (isOn ? "connected" : "offline");
    ui.dot.className = "dot" + (isOn ? " online" : "");
    ui.connect.textContent = isOn ? "Disconnect" : "Connect BT";
    ui.connect.classList.toggle("primary", !isOn);
    document.querySelectorAll(".action").forEach((b) => (b.disabled = !isOn));
    ui.prompt.disabled = !isOn;
    ui.protocol.textContent = isOn
      ? protocolMode === "json"
        ? "SPP / JSON stream"
        : "SPP / text stream"
      : "Bluetooth / offline";
  }
  function showEmpty(title, body) {
    ui.messages.replaceChildren();
    messageNodes.clear();
    currentPlain = null;
    const d = document.createElement("div");
    d.className = "empty";
    d.id = "empty";
    const h = document.createElement("h2");
    h.textContent = title;
    const p = document.createElement("p");
    p.textContent = body;
    const g = document.createElement("div");
    g.className = "terminalglyph";
    g.textContent = ">_";
    d.append(g, h, p);
    ui.messages.append(d);
  }
  function removeEmpty() {
    const empty = ui.messages.querySelector(".empty");
    if (empty) empty.remove();
  }
  function atBottom() {
    const s = ui.stage;
    return s.scrollHeight - s.scrollTop - s.clientHeight < 130;
  }
  function scrollEnd(force = false) {
    if (force || atBottom())
      requestAnimationFrame(() => (ui.stage.scrollTop = ui.stage.scrollHeight));
  }
  function addMessage(role, text, id) {
    const follow = atBottom();
    removeEmpty();
    role = role === "user" || role === "assistant" ? role : "notice";
    if (id && messageNodes.has(id)) {
      const node = messageNodes.get(id);
      node.querySelector(".msg-body").textContent = String(text);
      if (follow) scrollEnd(true);
      return node;
    }
    const row = document.createElement("div");
    row.className = "msg " + role;
    const label = document.createElement("div");
    label.className = "msg-label";
    label.textContent =
      role === "assistant" ? "code" : role === "user" ? "you" : "system";
    const body = document.createElement("div");
    body.className = "msg-body";
    body.textContent = String(text);
    const copy = document.createElement("button");
    copy.className = "tiny copy";
    copy.textContent = "Copy";
    copy.onclick = () =>
      navigator.clipboard
        .writeText(body.textContent)
        .then(() => notice("Copied."))
        .catch(handleError);
    label.append(copy);
    row.append(label, body);
    ui.messages.append(row);
    while (ui.messages.children.length > 200) {
      const first = ui.messages.firstChild;
      for (const [key, value] of messageNodes)
        if (value === first) messageNodes.delete(key);
      first.remove();
    }
    if (id) messageNodes.set(id, row);
    if (follow) scrollEnd(true);
    return row;
  }
  function appendPlain(text) {
    if (currentPlain) {
      let body = currentPlain.querySelector(".msg-body");
      body.textContent += (body.textContent ? "\n" : "") + text;
      scrollEnd();
    } else addMessage("notice", text);
  }
  function setSidebarOpen(open) {
    ui.sidebar.classList.toggle("open", open);
    $("sidebarScrim").classList.toggle("open", open);
    $("menuBtn").setAttribute("aria-expanded", String(open));
    $("menuBtn").setAttribute(
      "aria-label",
      open ? "Close sessions" : "Open sessions",
    );
  }
  function closeSidebar() {
    setSidebarOpen(false);
  }
  function renderSessions() {
    ui.sessions.replaceChildren();
    if (!sessions.length) {
      const d = document.createElement("div");
      d.className = "side-card";
      d.textContent =
        "No saved sessions yet. Use /new or start one on Windows.";
      ui.sessions.append(d);
      return;
    }
    for (const s of sessions.filter((s) =>
      (s.title || "")
        .toLowerCase()
        .includes($("sessionSearch").value.toLowerCase()),
    )) {
      const b = document.createElement("button");
      b.className =
        "session" + ((s.id && s.id === sessionId) || s.active ? " active" : "");
      const num = document.createElement("span");
      num.className = "session-idx";
      num.textContent = String(s.index || "");
      const title = document.createElement("span");
      title.className = "session-name";
      title.textContent = s.title || "Untitled";
      b.append(num, title);
      b.disabled = !connected;
      b.setAttribute("aria-current", String(s.id === sessionId));
      b.onclick = () => {
        (bridgeVersion >= 3
          ? rpc("select", { sessionId: s.id })
          : send("/use " + s.index)
        ).catch(handleError);
        closeSidebar();
      };
      ui.sessions.append(b);
    }
  }
  async function send(value) {
    if (!writer || !connected) throw new Error("Not connected to Bluetooth.");
    const target = writer,
      bytes = encoder.encode(String(value) + "\n");
    const job = writeQueue.then(async () => {
      if (writer !== target || !connected)
        throw new Error("Connection changed");
      await target.write(bytes);
    });
    writeQueue = job.catch(() => {});
    return job;
  }
  function rpc(action, fields = {}) {
    if (bridgeVersion < 3)
      return Promise.reject(
        new Error("Update and restart the Windows bridge to use this feature."),
      );
    const requestId = crypto.randomUUID();
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        requests.delete(requestId);
        reject(
          new Error(
            "No acknowledgement. Outcome unknown; refresh history before resending.",
          ),
        );
      }, 20000);
      requests.set(requestId, { resolve, reject, timer });
      send(
        "@OCBT1 " + JSON.stringify({ requestId, action, sessionId, ...fields }),
      ).catch((err) => {
        clearTimeout(timer);
        requests.delete(requestId);
        reject(err);
      });
    });
  }
  function renderPending(e) {
    const root = $("pending");
    root.replaceChildren();
    if (e.sessionId !== sessionId) return;
    const button = (card, title, action, fields) => {
      const b = document.createElement("button");
      b.className = "button";
      b.textContent = title;
      b.onclick = async () => {
        b.disabled = true;
        try {
          await rpc(action, fields);
          card.remove();
        } catch (err) {
          handleError(err);
          b.disabled = false;
        }
      };
      card.append(b);
    };
    for (const item of e.permission || []) {
      const card = document.createElement("div");
      card.className = "request-card";
      card.textContent =
        (item.permission || "Permission requested") +
        "\n" +
        (item.patterns || []).join("\n");
      for (const [title, reply] of [
        ["Allow once", "once"],
        ["Allow for session", "always"],
        ["Reject", "reject"],
      ])
        button(card, title, "permission", { id: item.id, reply });
      root.append(card);
    }
    for (const item of e.question || []) {
      const card = document.createElement("div");
      card.className = "request-card";
      const readers = [];
      for (const q of item.questions || []) {
        const title = document.createElement("p");
        title.textContent = q.question;
        card.append(title);
        const choices = [];
        for (const option of q.options || []) {
          const label = document.createElement("label"),
            input = document.createElement("input");
          input.type = q.multiple ? "checkbox" : "radio";
          input.name = item.id + "-" + readers.length;
          input.value = option.label;
          label.append(
            input,
            document.createTextNode(
              option.label + " — " + (option.description || ""),
            ),
          );
          card.append(label);
          choices.push(input);
        }
        const custom = document.createElement("input");
        custom.type = "text";
        custom.placeholder = "Custom answer";
        custom.setAttribute("aria-label", "Custom answer");
        if (q.custom !== false) card.append(custom);
        readers.push(() => {
          const selected = choices.filter((x) => x.checked).map((x) => x.value);
          return custom.value.trim()
            ? q.multiple
              ? [...selected, custom.value.trim()]
              : [custom.value.trim()]
            : selected;
        });
      }
      const submit = document.createElement("button");
      submit.className = "button";
      submit.textContent = "Reply";
      submit.onclick = async () => {
        const answers = readers.map((read) => read());
        if (answers.some((x) => !x.length)) {
          notice("Answer every question first.");
          return;
        }
        submit.disabled = true;
        try {
          await rpc("question", { id: item.id, answers });
          card.remove();
        } catch (err) {
          handleError(err);
          submit.disabled = false;
        }
      };
      card.append(submit);
      button(card, "Dismiss", "reject_question", { id: item.id });
      root.append(card);
    }
  }
  async function loadCatalog() {
    try {
      const data = await rpc("catalog");
      for (const name of data.agents) {
        $("agent").add(new Option(name, name));
      }
      for (const m of data.models) {
        $("model").add(
          new Option(
            m.name + " / " + m.providerID,
            JSON.stringify({ providerID: m.providerID, modelID: m.modelID }),
          ),
        );
      }
    } catch (err) {
      notice("Model/agent selection unavailable: " + err.message);
    }
  }
  function handleJSON(e) {
    if (
      !e ||
      typeof e !== "object" ||
      Array.isArray(e) ||
      typeof e.type !== "string"
    )
      throw new Error("Invalid bridge event");
    protocolMode = "json";
    ui.protocol.textContent = "SPP / JSON stream";
    switch (e.type) {
      case "hello":
        reconnectAttempts = 0;
        bridgeVersion = Number(e.version) || 0;
        if (bridgeVersion >= 3) loadCatalog();
        notice("Structured bridge connected.");
        send("/sessions").catch(handleError);
        break;
      case "sessions":
        sessions = Array.isArray(e.items) ? e.items : [];
        renderSessions();
        if (!sessionId && sessions.length) {
          send("/use " + sessions[0].index).catch(handleError);
        }
        break;
      case "selected":
        drafts.set(sessionId, ui.prompt.value);
        sessionId = e.id || null;
        ui.prompt.value = drafts.get(sessionId) || "";
        $("pending").replaceChildren();
        sessionTitle = e.title || "Untitled";
        ui.title.textContent = sessionTitle;
        ui.meta.textContent = "local OpenCode project / active session";
        // Show the selected session immediately, even if a delayed list update follows.
        if (sessionId && !sessions.some((s) => s.id === sessionId))
          sessions = [
            { id: sessionId, title: sessionTitle, index: null, active: true },
            ...sessions,
          ].slice(0, 10);
        renderSessions();
        closeSidebar();
        showEmpty(
          "Loading chat history",
          "Reading messages from OpenCode via Bluetooth.",
        );
        break;
      case "history_begin":
        if (e.sessionId && e.sessionId !== sessionId) break;
        historyMode = true;
        ui.messages.replaceChildren();
        messageNodes.clear();
        currentPlain = null;
        break;
      case "history_end":
        if (e.sessionId && e.sessionId !== sessionId) break;
        historyMode = false;
        if (!ui.messages.children.length)
          showEmpty(
            "Ready for prompts",
            "This OpenCode session has no text messages yet.",
          );
        else scrollEnd(true);
        break;
      case "message":
        if (e.sessionId && e.sessionId !== sessionId) break;
        if (typeof e.text === "string")
          addMessage(e.role || "assistant", e.text, e.id || undefined);
        break;
      case "status":
        if (e.id === sessionId) {
          ui.meta.textContent = "OpenCode · " + String(e.status || "unknown");
          $("stopBtn").disabled = !connected || e.status === "idle";
        }
        break;
      case "message_delta":
        if (e.sessionId === sessionId) {
          const node = messageNodes.get(e.id);
          addMessage(
            e.role,
            (node?.querySelector(".msg-body").textContent || "") + e.text,
            e.id,
          );
        }
        break;
      case "pending":
        renderPending(e);
        break;
      case "result": {
        const req = requests.get(e.requestId);
        if (req) {
          clearTimeout(req.timer);
          requests.delete(e.requestId);
          e.ok
            ? req.resolve(e.data)
            : req.reject(new Error(e.error || "Request failed"));
        }
        break;
      }
      case "error":
        notice(e.message || "Bridge error");
        break;
      case "prompt_accepted":
        notice("Prompt accepted. Waiting for OpenCode reply...");
        break;
      case "notice":
        if (typeof e.text === "string") addMessage("notice", String(e.text));
        break;
      default:
        if (e.type) notice("Bridge event: " + e.type);
        break;
    }
  }
  function handlePlain(line) {
    if (!line) return;
    if (
      line.startsWith("Unknown bridge command") &&
      protocolMode === "unknown"
    ) {
      protocolMode = "text";
      ui.protocol.textContent = "SPP / text stream";
      return;
    }
    if (line.startsWith("Sessions (most recent first):")) {
      sessions = [];
      ui.sessions.replaceChildren();
      currentPlain = null;
      addMessage(
        "notice",
        "Sessions received; choose one from the list below.",
      );
      return;
    }
    let m = line.match(/^(\d+)\. (.+?)( \*)?$/);
    if (m && protocolMode !== "json") {
      sessions.push({ index: Number(m[1]), title: m[2], active: !!m[3] });
      renderSessions();
      return;
    }
    if (line.startsWith("Selected: ")) {
      sessionTitle = line.slice(10);
      ui.title.textContent = sessionTitle;
      ui.meta.textContent = "local OpenCode project / selected session";
      currentPlain = null;
      closeSidebar();
      showEmpty("Loading history", "Waiting for messages from OpenCode.");
      return;
    }
    if (line.startsWith("--- Last ")) {
      ui.messages.replaceChildren();
      messageNodes.clear();
      currentPlain = null;
      historyMode = true;
      return;
    }
    if (line === "--- End history ---") {
      historyMode = false;
      currentPlain = null;
      if (!ui.messages.children.length)
        showEmpty(
          "Ready for prompts",
          "Type a message below to start chatting.",
        );
      return;
    }
    const role = line.match(/^\[(USER|ASSISTANT)(?: (continues|updated))?\]$/i);
    if (role) {
      const r = role[1].toLowerCase();
      currentPlain = addMessage(r, "");
      return;
    }
    if (line.startsWith("Prompt accepted")) {
      notice(line);
      return;
    }
    if (line.startsWith("Current session:")) {
      notice(line);
      return;
    }
    if (line.startsWith("OpenCode connection issue:")) {
      addMessage("notice", line);
      return;
    }
    if (line.startsWith("No sessions found")) {
      sessions = [];
      renderSessions();
      addMessage("notice", line);
      return;
    }
    appendPlain(line);
  }
  function consumeLine(line) {
    line = line.replace(/\r$/, "");
    if (line.startsWith("@OCBT1 ")) {
      try {
        handleJSON(JSON.parse(line.slice(7)));
      } catch (err) {
        addMessage("notice", "Bad JSON frame: " + err.message);
      }
      return;
    }
    handlePlain(line);
  }
  function feedBytes(bytes) {
    receiveBuffer += decoder.decode(bytes, { stream: true });
    if (receiveBuffer.length > 200000) {
      receiveBuffer = "";
      notice("Received frame too large. Buffer reset.");
      return;
    }
    while (receiveBuffer.includes("\n")) {
      const i = receiveBuffer.indexOf("\n");
      const line = receiveBuffer.slice(0, i);
      receiveBuffer = receiveBuffer.slice(i + 1);
      consumeLine(line);
    }
  }
  async function receiveLoop() {
    try {
      while (port && port.readable) {
        reader = port.readable.getReader();
        try {
          while (true) {
            const { value, done } = await reader.read();
            if (done) break;
            if (value) feedBytes(value);
          }
        } finally {
          try {
            reader.releaseLock();
          } catch (_) {}
          reader = null;
        }
        break;
      }
    } catch (err) {
      if (!closing) notice("Bluetooth disconnected: " + err.message);
    } finally {
      if (!closing) {
        const previous = port;
        await disconnect(true, true);
        scheduleReconnect(previous);
      }
    }
  }
  function scheduleReconnect(previous) {
    if (manualDisconnect || !previous || reconnectAttempts >= 6) return;
    const delay = Math.min(30000, 1000 * 2 ** reconnectAttempts++);
    notice("Connection lost. Reconnecting…");
    reconnectTimer = setTimeout(() => connect(previous, true), delay);
  }
  async function connect(approvedPort = null, automatic = false) {
    if (!("serial" in navigator)) {
      if (!automatic)
        notice(
          "Web Serial unavailable. Use Chrome for Android with Web Serial support and HTTPS.",
        );
      return;
    }
    if (!window.isSecureContext) {
      if (!automatic)
        notice(
          "This page needs HTTPS or a browser-trusted local origin to access Bluetooth.",
        );
      return;
    }
    if (connected || closing || ui.connect.disabled) return;
    ui.connect.disabled = true;
    try {
      let selected = approvedPort;
      if (!selected) {
        // First-time site permission requires a user gesture. Call requestPort()
        // directly from the Connect button's click (before any other await).
        selected = await navigator.serial.requestPort();
      }
      port = selected;
      await port.open({ baudRate: 9600 });
      writer = port.writable.getWriter();
      receiveBuffer = "";
      decoder.decode();
      protocolMode = "unknown";
      bridgeVersion = 0;
      sessionId = null;
      sessions = [];
      $("agent").length = 1;
      $("model").length = 1;
      renderSessions();
      showEmpty(
        "Connecting to OpenCode",
        "Waiting for the Windows Bluetooth bridge.",
      );
      updateConnection(true, "connected");
      readLoopPromise = receiveLoop();
      notice("Bluetooth connected. Loading sessions...");
      await send("/ui json");
      // Fall back to the text bridge if no structured hello arrives.
      initTimer = setTimeout(() => {
        if (connected && protocolMode !== "json")
          send("/sessions").catch(handleError);
      }, 900);
    } catch (err) {
      if (!automatic)
        notice(
          "Bluetooth: " +
            err.message +
            " · Confirm the phone is paired and the Windows COM3 bridge is running.",
        );
      await disconnect(true);
      if (automatic) scheduleReconnect(approvedPort);
    } finally {
      ui.connect.disabled = false;
    }
  }
  // On subsequent visits, Chrome can return a previously-authorized serial port.
  // First-time website permission still requires tapping Connect BT and choosing a device.
  async function autoConnectPreviouslyAllowed() {
    if (!("serial" in navigator) || !window.isSecureContext || connected)
      return;
    try {
      const granted = await navigator.serial.getPorts();
      if (granted.length === 1) await connect(granted[0], true);
    } catch (err) {
      console.info("Bluetooth auto-connect unavailable:", err.message);
    }
  }
  async function disconnect(quiet = false, fromReader = false) {
    if (closing) return;
    closing = true;
    closeSidebar();
    clearTimeout(initTimer);
    clearTimeout(reconnectTimer);
    for (const req of requests.values()) {
      clearTimeout(req.timer);
      req.reject(
        new Error(
          "Disconnected; outcome unknown. Check history before resending.",
        ),
      );
    }
    requests.clear();
    $("pending").replaceChildren();
    updateConnection(false, "offline");
    try {
      if (reader) await reader.cancel();
    } catch (_) {}
    if (!fromReader && readLoopPromise) {
      try {
        await readLoopPromise;
      } catch (_) {}
    }
    try {
      if (writer) {
        await writer.abort().catch(() => {});
        writer.releaseLock();
        writer = null;
      }
    } catch (_) {}
    try {
      if (port) await port.close();
    } catch (_) {}
    port = null;
    reader = null;
    receiveBuffer = "";
    closing = false;
    if (!quiet) notice("Bluetooth disconnected.");
  }
  function handleError(err) {
    notice(err && err.message ? err.message : String(err));
  }
  $("sessionSearch").oninput = renderSessions;
  $("stopBtn").onclick = () =>
    rpc("abort")
      .then(() => notice("Stop requested."))
      .catch(handleError);
  $("renameBtn").onclick = async () => {
    const title = prompt("Session title", sessionTitle);
    if (title === null) return;
    try {
      await rpc("rename", { title });
      sessionTitle = title;
      ui.title.textContent = title;
    } catch (err) {
      handleError(err);
    }
  };
  $("deleteBtn").onclick = () => {
    if (
      sessionId &&
      confirm("Permanently delete this session and its messages?")
    )
      rpc("delete").catch(handleError);
  };
  $("exportBtn").onclick = () => {
    const text = [
      sessionTitle,
      ...Array.from(ui.messages.querySelectorAll(".msg")).map(
        (n) =>
          n.querySelector(".msg-label").textContent +
          ":\n" +
          n.querySelector(".msg-body").textContent,
      ),
    ].join("\n\n");
    const url = URL.createObjectURL(
      new Blob([text], { type: "text/plain;charset=utf-8" }),
    );
    const a = document.createElement("a");
    a.href = url;
    a.download = "opencode-session.txt";
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  ui.connect.onclick = () => {
    manualDisconnect = connected;
    clearTimeout(reconnectTimer);
    reconnectAttempts = 0;
    return connected ? disconnect() : connect();
  };
  $("menuBtn").onclick = () =>
    setSidebarOpen(!ui.sidebar.classList.contains("open"));
  $("closeSidebarBtn").onclick = closeSidebar;
  $("sidebarScrim").onclick = closeSidebar;
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeSidebar();
  });
  $("refreshSessionsBtn").onclick = () => send("/sessions").catch(handleError);
  $("sessionsCmd").onclick = () => {
    send("/sessions").catch(handleError);
    setSidebarOpen(true);
  };
  $("historyBtn").onclick = $("historyCmd").onclick = () =>
    send("/history 12").catch(handleError);
  $("statusBtn").onclick = $("statusCmd").onclick = () =>
    send("/status").catch(handleError);
  async function createNewSession() {
    if (!confirm("Create a new OpenCode session?")) return;
    try {
      await send("/new");
      closeSidebar();
    } catch (err) {
      handleError(err);
    }
  }
  $("newSessionBtn").onclick = $("newCmd").onclick = createNewSession;
  ui.send.onclick = () => submitPrompt();
  async function submitPrompt() {
    const value = ui.prompt.value;
    if (!value.trim() || sending) return;
    if (!sessionId) {
      notice("Select or create a session first.");
      return;
    }
    const sentSession = sessionId;
    sending = true;
    ui.send.disabled = true;
    try {
      if (bridgeVersion >= 3) {
        await rpc("prompt", {
          text: value,
          agent: $("agent").value || undefined,
          model: $("model").value ? JSON.parse($("model").value) : undefined,
        });
      } else {
        throw new Error(
          "Update the bridge for acknowledged multiline prompts.",
        );
      }
      if (sessionId === sentSession && ui.prompt.value === value) {
        ui.prompt.value = "";
      }
      drafts.delete(sentSession);
      ui.prompt.style.height = "";
      notice("Prompt accepted by OpenCode.");
    } catch (err) {
      handleError(err);
    } finally {
      sending = false;
      ui.send.disabled = !connected;
    }
  }
  ui.prompt.onkeydown = (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      submitPrompt();
    }
  };
  ui.prompt.oninput = () => {
    ui.prompt.style.height = "27px";
    ui.prompt.style.height = Math.min(ui.prompt.scrollHeight, 140) + "px";
  };
  // Installation and display controls. First-time Bluetooth website permission requires a user tap.
  const installBtn = $("installBtn"),
    installHint = $("installHint"),
    fullBtn = $("fullscreenBtn");
  function installedDisplay() {
    return (
      window.matchMedia("(display-mode: fullscreen)").matches ||
      window.matchMedia("(display-mode: standalone)").matches
    );
  }
  function manifestFullscreen() {
    return window.matchMedia("(display-mode: fullscreen)").matches;
  }
  function setDisplayButton() {
    const active =
      !!document.fullscreenElement ||
      (manifestFullscreen() &&
        !document.body.classList.contains("windowed-mode"));
    fullBtn.textContent = active ? "▣" : "⛶";
    fullBtn.title = manifestFullscreen()
      ? "Toggle edge-to-edge / framed app view"
      : active
        ? "Exit fullscreen"
        : "Enter fullscreen";
    fullBtn.setAttribute("aria-label", fullBtn.title);
    fullBtn.setAttribute("aria-pressed", String(active));
  }
  fullBtn.addEventListener("click", async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else if (manifestFullscreen()) {
        // Manifest fullscreen cannot reliably be exited programmatically on Chrome Android.
        // Give the user a visible framed alternative without claiming to reveal system bars.
        document.body.classList.toggle("windowed-mode");
        notice(
          document.body.classList.contains("windowed-mode")
            ? "Framed app view. The installed PWA still controls system bars."
            : "Edge-to-edge app view.",
        );
      } else if (document.documentElement.requestFullscreen)
        await document.documentElement.requestFullscreen({
          navigationUI: "hide",
        });
      else
        notice(
          "Fullscreen API unavailable here. Use Chrome Fullscreen when supported.",
        );
    } catch (err) {
      notice("Fullscreen: " + err.message);
    }
    setDisplayButton();
  });
  document.addEventListener("fullscreenchange", setDisplayButton);
  installBtn.addEventListener("click", () => {
    notice(
      "For a home-screen shortcut, open this file as an HTTPS page and use Chrome menu → Add to Home screen. Install from Chrome via Add to Home screen / Install app.",
    );
  });
  installHint.textContent =
    "One file: HTML, CSS, JavaScript and icon are all embedded. Use Chrome 138+ with a trusted HTTPS origin for Bluetooth.";
  setDisplayButton();
  // A manifest and service worker enable installability; app CSS and JS stay embedded.
  updateConnection(false, "offline");
  if (!("serial" in navigator)) {
    ui.connect.title = "Web Serial unavailable";
    ui.meta.textContent =
      "Web Serial not detected. Use Chrome for Android 138+ with HTTPS.";
  } else if (!isSecureContext) {
    ui.meta.textContent = "Insecure page origin. Open via trusted HTTPS.";
  } else {
    ui.meta.textContent =
      "local OpenCode project / Web Serial available - pair Windows first";
  }
  window.addEventListener("pagehide", () => {
    manualDisconnect = true;
    clearTimeout(reconnectTimer);
    if (connected) disconnect(true);
  });
  // Reconnect if the browser has already granted access to this device for this origin.
  autoConnectPreviouslyAllowed();
})();
