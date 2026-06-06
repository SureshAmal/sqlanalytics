const form = document.querySelector("#form");
const queryInput = document.querySelector("#query");
const messages = document.querySelector("#messages");
const sendButton = document.querySelector("#send");
const statusEl = document.querySelector("#status");

const apiUrl = resolveApiUrl();
let timerId = null;

form.addEventListener("submit", async (event) => {
  event.preventDefault();

  const query = queryInput.value.trim();
  if (!query) return;

  addMessage("user", escapeHtml(query));
  queryInput.value = "";

  const report = addReport();
  const state = {
    startedAt: Date.now(),
    markdown: "",
    sqlCalls: 0,
  };

  startLoading(report, state, "Connecting");
  setBusy(true, "Connecting");

  try {
    const response = await fetch(apiUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
      },
      body: JSON.stringify({ query, session: "demo" }),
    });

    if (!response.ok || !response.body) {
      throw new Error(await response.text());
    }

    for await (const event of readSse(response.body)) {
      handleEvent(event, report, state);
    }
  } catch (error) {
    report.body.innerHTML = `<p class="error">Error: ${escapeHtml(String(error))}</p>`;
    finishLoading(report, state, "Error");
  } finally {
    setBusy(false);
  }
});

function handleEvent(event, report, state) {
  if (event.event === "markdown") {
    state.markdown += event.data;
    report.body.innerHTML = renderMarkdown(state.markdown);
    updateProgress(report, state, "Writing report");
    scrollToBottom(false);
    return;
  }

  if (event.event === "status") {
    updateProgress(report, state, statusText(event.data));
    return;
  }

  if (event.event === "tool_call" || event.event === "tool_result") {
    updateProgress(report, state, toolText(event, state));
    return;
  }

  if (event.event === "usage") {
    updateProgress(report, state, usageText(event.data));
    return;
  }

  if (event.event === "error") {
    report.body.innerHTML = `<p class="error">Error: ${escapeHtml(event.data)}</p>`;
    finishLoading(report, state, "Error");
    return;
  }

  if (event.event === "done") {
    if (!state.markdown.trim()) {
      report.body.innerHTML = `<p class="error">The agent finished without a Markdown report.</p>`;
    }
    finishLoading(report, state, "Done");
  }
}

function addReport() {
  const article = document.createElement("article");
  article.className = "message assistant";

  const bubble = document.createElement("div");
  bubble.className = "bubble";

  const progress = document.createElement("div");
  progress.className = "progress";
  progress.innerHTML = `
    <span class="spinner"></span>
    <span class="progress-text">Starting</span>
    <span class="progress-meta">0s · SQL 0</span>
  `;

  const body = document.createElement("div");
  body.className = "report";

  bubble.append(progress, body);
  article.appendChild(bubble);
  messages.appendChild(article);
  scrollToBottom(true);

  return {
    progress,
    text: progress.querySelector(".progress-text"),
    meta: progress.querySelector(".progress-meta"),
    spinner: progress.querySelector(".spinner"),
    body,
  };
}

function addMessage(role, html) {
  const article = document.createElement("article");
  article.className = `message ${role}`;
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.innerHTML = html;
  article.appendChild(bubble);
  messages.appendChild(article);
  scrollToBottom(true);
}

function startLoading(report, state, text) {
  updateProgress(report, state, text);
  clearInterval(timerId);
  timerId = setInterval(() => updateProgress(report, state), 1000);
}

function finishLoading(report, state, text) {
  clearInterval(timerId);
  timerId = null;
  updateProgress(report, state, text);
  report.progress.classList.add(text === "Done" ? "done" : "failed");
  report.spinner.style.display = "none";
}

function updateProgress(report, state, text) {
  if (text) {
    report.text.textContent = text;
    setStatus(text);
  }
  const seconds = Math.floor((Date.now() - state.startedAt) / 1000);
  report.meta.textContent = `${seconds}s · SQL ${state.sqlCalls}`;
}

function statusText(rawData) {
  const payload = parseJson(rawData);
  if (payload && typeof payload === "object") {
    return payload.message || payload.phase || "Working";
  }
  return String(rawData || "Working");
}

function toolText(event, state) {
  const payload = parseJson(event.data);
  if (!payload || typeof payload !== "object") return event.data;

  if (payload.tool === "sql") {
    const call = Number(payload.call || 0);
    state.sqlCalls = Math.max(state.sqlCalls, call);
    return event.event === "tool_call"
      ? `Running SQL #${call}`
      : `SQL #${call} returned ${payload.chars || 0} chars`;
  }

  if (payload.tool === "memory") {
    return event.event === "tool_call" ? "Checking memory" : "Memory loaded";
  }

  return "Working";
}

function usageText(rawData) {
  const payload = parseJson(rawData);
  const usage = payload?.usage || {};
  const input = usage.input_tokens || 0;
  const output = usage.output_tokens || 0;
  return `Tokens ${input} in / ${output} out`;
}

async function* readSse(stream) {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const events = buffer.split("\n\n");
    buffer = events.pop() || "";
    for (const block of events) {
      const event = parseSseEvent(block);
      if (event) yield event;
    }
  }
}

function parseSseEvent(block) {
  let event = "message";
  const data = [];

  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
  }

  return { event, data: data.join("\n") };
}

function renderMarkdown(markdown) {
  if (window.marked?.parse) return marked.parse(markdown);
  return escapeHtml(markdown);
}

function parseJson(value) {
  try {
    return JSON.parse(value);
  } catch {
    return null;
  }
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function setBusy(isBusy, label) {
  sendButton.disabled = isBusy;
  if (label) setStatus(label);
}

function setStatus(label) {
  statusEl.textContent = label;
}

function scrollToBottom(force = false) {
  const distance = messages.scrollHeight - messages.scrollTop - messages.clientHeight;
  if (force || distance < 120) messages.scrollTop = messages.scrollHeight;
}

function resolveApiUrl() {
  if (window.SQL_ANALYTICS_API_URL) return window.SQL_ANALYTICS_API_URL;
  if (location.port === "8001" || location.port === "") {
    return "/api/v1/reports/query/";
  }
  return "http://127.0.0.1:8001/api/v1/reports/query/";
}
