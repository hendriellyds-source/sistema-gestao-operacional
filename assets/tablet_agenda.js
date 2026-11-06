/* Agenda tablet: ink is written to IndexedDB before any network request. */
const W = 1000,
  H = 1400,
  TOP = 130,
  STEP = 90,
  LINES = 14;
const canvas = document.getElementById("paper"),
  ctx = canvas.getContext("2d");
const dayInput = document.getElementById("day"),
  statusEl = document.getElementById("syncStatus");
const reviewOverlay = document.getElementById("reviewOverlay"),
  reviewCards = document.getElementById("reviewCards");
let db,
  day = "",
  ink = [],
  reviews = [],
  scheduleEntries = [],
  pendingRequests = [],
  catalog = { teams: [], companies: [], works: [], aliases: [] };
let active = null,
  penActive = false,
  syncBusy = false,
  syncRequested = false,
  installPrompt = null,
  ocrReady = null,
  authExpired = false,
  operationCounter = 0;
let syncTimer = null;
const reviewTimers = new Map();
const textNotebook = document.getElementById("textNotebook");
let mode = localStorage.getItem("agenda-mode") === "drawing" ? "drawing" : "writing";

function setMode(next) {
  mode = next;
  localStorage.setItem("agenda-mode", mode);
  textNotebook.hidden = mode !== "writing";
  canvas.hidden = mode !== "drawing";
  document.getElementById("inkColorLabel").hidden = mode !== "drawing";
  document.getElementById("undo").hidden = mode !== "drawing";
  document.getElementById("modeHint").textContent = mode === "drawing"
    ? "Desenhe livremente com a S Pen. A escrita original é salva no tablet."
    : "Digite em cada linha. Use / ou Shift para separar itens; 1/2 ou 1/4 indica a fração da diária. No final, /2000 define o valor desta obra e /+400 soma um adicional.";
  for (const [id, current] of [["textMode", "writing"], ["drawMode", "drawing"]]) {
    const button = document.getElementById(id);
    button.className = mode === current ? "primary" : "subtle";
    button.setAttribute("aria-pressed", String(mode === current));
  }
  if (db && mode === "writing") renderTextRows();
}
document.getElementById("textMode").onclick = () => setMode("writing");
document.getElementById("drawMode").onclick = () => setMode("drawing");

function localDay(d = new Date()) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
function weekdayLabel(value) {
  const [year, month, date] = value.split("-").map(Number);
  if (!year || !month || !date) return "";
  const weekday = new Intl.DateTimeFormat("pt-BR", { weekday: "long" })
    .format(new Date(year, month - 1, date));
  return weekday.charAt(0).toUpperCase() + weekday.slice(1);
}
function offsetDay(value, n) {
  const [y, m, d] = value.split("-").map(Number);
  const date = new Date(y, m - 1, d + n);
  return localDay(date);
}
function key(item) {
  return `${item.day}:${item.id}`;
}
function openDb() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open("operational-tablet-agenda", 1);
    request.onupgradeneeded = () => {
      const store = request.result;
      store.createObjectStore("ink", { keyPath: "key" });
      store.createObjectStore("reviews", { keyPath: "key" });
      store.createObjectStore("queue", { keyPath: "key" });
      store.createObjectStore("meta", { keyPath: "key" });
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}
function transaction(store, mode = "readonly") {
  return db.transaction(store, mode).objectStore(store);
}
function put(store, item) {
  return new Promise((resolve, reject) => {
    const req = transaction(store, "readwrite").put(item);
    req.onsuccess = () => resolve();
    req.onerror = () => reject(req.error);
  });
}
function all(store) {
  return new Promise((resolve, reject) => {
    const req = transaction(store).getAll();
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}
function remove(store, id) {
  return new Promise((resolve, reject) => {
    const req = transaction(store, "readwrite").delete(id);
    req.onsuccess = () => resolve();
    req.onerror = () => reject(req.error);
  });
}
async function putQueued(store, item, op) {
  await new Promise((resolve, reject) => {
    const tx = db.transaction([store, "queue"], "readwrite");
    tx.objectStore(store).put(item);
    tx.objectStore("queue").put({
      ...op,
      created: Date.now() * 1000 + (operationCounter++ % 1000),
    });
    tx.oncomplete = resolve;
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error);
  });
  updateStatus();
  if (navigator.onLine) {
    if (op.type === "review") {
      clearTimeout(syncTimer);
      syncTimer = setTimeout(sync, 500);
    } else sync();
  }
}
async function pending() {
  return (await all("queue")).sort((a, b) => a.created - b.created);
}
function setStatus(message, kind = "") {
  statusEl.textContent = message;
  statusEl.className = `sync-status ${kind}`;
  document.getElementById("relogin").hidden = !authExpired;
}
async function updateStatus() {
  if (!navigator.onLine) {
    setStatus("Sem conexão · salvo no tablet", "pending");
    return;
  }
  if (authExpired) {
    setStatus("Sessão expirada · entre novamente", "error");
    return;
  }
  const count = (await pending()).length;
  const unbooked = reviews.filter((review) => !review.schedule_id
    && (review.text_original?.trim() || review.unregistered_work_text?.trim())).length;
  setStatus(
    count
      ? `${count} pendente${count > 1 ? "s" : ""} · salvo no tablet`
      : unbooked ? `${unbooked} linha${unbooked > 1 ? "s" : ""} sem agendamento · conferir`
      : "Sincronizado",
    count || unbooked ? "pending" : "",
  );
}
function paintBackground() {
  ctx.fillStyle = "#fffefa";
  ctx.fillRect(0, 0, W, H);
  ctx.strokeStyle = "#d9e3e6";
  ctx.lineWidth = 1;
  for (let i = 0; i < LINES; i++) {
    const y = TOP + (i + 1) * STEP;
    ctx.beginPath();
    ctx.moveTo(68, y);
    ctx.lineTo(W - 35, y);
    ctx.stroke();
  }
  ctx.strokeStyle = "#efcaca";
  ctx.beginPath();
  ctx.moveTo(65, TOP - 20);
  ctx.lineTo(65, H - 15);
  ctx.stroke();
  ctx.fillStyle = "#31556b";
  ctx.font = "bold 29px Arial";
  ctx.fillText("Agenda · " + day.split("-").reverse().join("/") + " · " + weekdayLabel(day), 82, 65);
  ctx.font = "15px Arial";
  ctx.fillStyle = "#8aa0ab";
  ctx.fillText(
    "Uma obra por linha. Escreva equipe, obra e horário em qualquer ordem.",
    82,
    97,
  );
  const slots = scheduleLineSlots();
  ctx.textBaseline = "alphabetic";
  for (const [line, entry] of slots.lines) {
    const y = TOP + line * STEP;
    ctx.fillStyle = entry.registrationPending ? "#9b5510" : "#17394b";
    ctx.font = "bold 21px Arial";
    ctx.fillText(shortCanvasText(scheduleTitle(entry), 62), 82, y + 39);
    ctx.fillStyle = "#5c7885";
    ctx.font = "14px Arial";
    ctx.fillText(shortCanvasText(scheduleDetail(entry), 80), 82, y + 64);
  }
  const overflow = slots.overflow.length;
  if (overflow) {
    ctx.fillStyle = "#5c7885";
    ctx.font = "bold 14px Arial";
    ctx.fillText(`+ ${overflow} agendamento${overflow > 1 ? "s" : ""} adicional${overflow > 1 ? "is" : ""} no modo Escrita`, 82, H - 17);
  }
}
function paintStroke(stroke) {
  if (stroke.deleted) return;
  const points = stroke.points;
  if (!points?.length) return;
  ctx.strokeStyle = stroke.color;
  ctx.fillStyle = stroke.color;
  ctx.lineWidth = stroke.width;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.beginPath();
  ctx.moveTo(...points[0]);
  for (const point of points.slice(1)) ctx.lineTo(...point);
  if (points.length === 1) {
    ctx.arc(points[0][0], points[0][1], stroke.width / 2, 0, Math.PI * 2);
    ctx.fill();
  } else ctx.stroke();
}
function redraw() {
  paintBackground();
  ink.forEach(paintStroke);
  if (active) paintStroke(active);
}
function point(event) {
  const rect = canvas.getBoundingClientRect();
  return [
    Math.max(0, Math.min(W, ((event.clientX - rect.left) * W) / rect.width)),
    Math.max(0, Math.min(H, ((event.clientY - rect.top) * H) / rect.height)),
  ];
}
function lineOf(stroke) {
  const ys = stroke.points.map((p) => p[1]);
  const mid = (Math.min(...ys) + Math.max(...ys)) / 2;
  return Math.max(0, Math.min(LINES - 1, Math.floor((mid - TOP) / STEP)));
}
canvas.addEventListener("pointerdown", (event) => {
  if (event.pointerType === "touch" && penActive) return;
  if (event.pointerType === "pen") penActive = true;
  event.preventDefault();
  canvas.setPointerCapture(event.pointerId);
  active = {
    id: crypto.randomUUID(),
    day,
    points: [point(event)],
    color: document.getElementById("inkColor").value,
    width: event.pointerType === "pen" ? 2.7 : 3.4,
    deleted: 0,
  };
  redraw();
});
canvas.addEventListener("pointermove", (event) => {
  if (!active) return;
  event.preventDefault();
  const events = event.getCoalescedEvents?.() || [event];
  for (const item of events) active.points.push(point(item));
  redraw();
});
async function finishStroke() {
  if (!active) return;
  const stroke = active;
  active = null;
  if (stroke.points.length > 1500) {
    const stride = Math.ceil(stroke.points.length / 1500);
    stroke.points = stroke.points.filter((_, i) => i % stride === 0);
  }
  stroke.key = key(stroke);
  try {
    await putQueued("ink", stroke, {
      key: `stroke:${stroke.id}`,
      type: "stroke",
      day: stroke.day,
      id: stroke.id,
      payload: {
        id: stroke.id,
        points: stroke.points,
        color: stroke.color,
        width: stroke.width,
      },
    });
    ink.push(stroke);
    redraw();
  } catch (error) {
    redraw();
    setStatus("Falha ao salvar no tablet", "error");
    console.error(error);
  }
}
canvas.addEventListener("pointerup", finishStroke);
canvas.addEventListener("pointercancel", finishStroke);
document.getElementById("undo").onclick = async () => {
  const last = [...ink].reverse().find((x) => !x.deleted);
  if (!last) return;
  const deleted = { ...last, deleted: 1 };
  try {
    await putQueued("ink", deleted, {
      key: `delete:${last.id}`,
      type: "delete",
      day: last.day,
      id: last.id,
    });
    last.deleted = 1;
    redraw();
  } catch (error) {
    setStatus("Falha ao salvar no tablet", "error");
    console.error(error);
  }
};

async function loadDay(next) {
  day = next;
  dayInput.value = next;
  active = null;
  ink = (await all("ink")).filter((x) => x.day === day);
  reviews = (await all("reviews")).filter((x) => x.day === day);
  const cachedPage = (await all("meta")).find((x) => x.key === `page:${day}`)?.value;
  scheduleEntries = cachedPage?.schedule_entries || [];
  pendingRequests = cachedPage?.pending_requests || [];
  redraw();
  renderTextRows();
  renderDailyPanels();
  updateStatus();
  if (navigator.onLine) sync();
}
dayInput.onchange = () => loadDay(dayInput.value);
document.getElementById("previousDay").onclick = () =>
  loadDay(offsetDay(day, -1));
document.getElementById("nextDay").onclick = () => loadDay(offsetDay(day, 1));
document.getElementById("today").onclick = () => loadDay(localDay());
async function readServer() {
  if (!navigator.onLine) return;
  const requestedDay = day;
  const [pageResponse, catalogResponse] = await Promise.all([
    fetch(`/api/tablet-agenda/pages/${requestedDay}`),
    fetch("/api/tablet-agenda/catalog"),
  ]);
  if (pageResponse.status === 401 || catalogResponse.status === 401) {
    authExpired = true;
    throw Error("Sessão expirada");
  }
  if (!pageResponse.ok || !catalogResponse.ok)
    throw Error("Servidor indisponível");
  authExpired = false;
  let page = await pageResponse.json();
  if (page.reviews.some((review) => !review.schedule_id
      && (review.work_id || review.unregistered_work_text?.trim())
      && (review.text_original?.trim() || review.unregistered_work_text?.trim()))) {
    const published = await fetch(`/api/tablet-agenda/pages/${requestedDay}/publish-pending`,
      { method: "POST" });
    if (published.status === 401) {
      authExpired = true;
      throw Error("Sessão expirada");
    }
    if (!published.ok) throw Error("Não foi possível enviar os agendamentos");
    const refreshed = await fetch(`/api/tablet-agenda/pages/${requestedDay}`);
    if (!refreshed.ok) throw Error("Não foi possível atualizar a agenda");
    page = await refreshed.json();
  }
  catalog = await catalogResponse.json();
  await put("meta", { key: "catalog", value: catalog });
  await put("meta", { key: `page:${requestedDay}`, value: {
    schedule_entries: page.schedule_entries || [], pending_requests: page.pending_requests || [],
  } });
  const localInk = await all("ink");
  const byId = new Map(
    localInk.filter((x) => x.day === requestedDay).map((x) => [x.id, x]),
  );
  for (const remote of page.strokes) {
    const saved = byId.get(remote.id);
    if (saved) {
      if (remote.deleted && !saved.deleted) {
        saved.deleted = 1;
        await put("ink", saved);
      }
      continue;
    }
    const item = { ...remote, key: `${requestedDay}:${remote.id}` };
    await put("ink", item);
  }
  const localReviews = await all("reviews");
  const byReview = new Map(
    localReviews.filter((x) => x.day === requestedDay).map((x) => [x.id, x]),
  );
  for (const remote of page.reviews) {
    const saved = byReview.get(remote.id);
    if (saved && (await hasPendingReview(remote.id))) continue;
    await put("reviews", { ...remote, key: `${requestedDay}:${remote.id}` });
  }
  if (day !== requestedDay) return;
  ink = (await all("ink")).filter((x) => x.day === day);
  reviews = (await all("reviews")).filter((x) => x.day === day);
  scheduleEntries = page.schedule_entries || [];
  pendingRequests = page.pending_requests || [];
  redraw();
  if (!document.activeElement?.classList.contains("line-entry")) renderTextRows();
  if (!reviewOverlay.hidden && !document.activeElement?.matches(".card input, .card textarea, .card select"))
    renderReviews();
  renderDailyPanels();
}
async function hasPendingReview(id) {
  return (await pending()).some((op) => op.type === "review" && op.id === id);
}
async function sync() {
  if (syncBusy) { syncRequested = true; return; }
  if (!navigator.onLine || !db) return;
  syncBusy = true;
  try {
    const operations = await pending();
    const latestReview = new Map(operations.filter((op) => op.type === "review")
      .map((op) => [op.id, op.key]));
    for (const op of operations) {
      if (op.type === "review" && latestReview.get(op.id) !== op.key) {
        await remove("queue", op.key);
        continue;
      }
      let url = `/api/tablet-agenda/pages/${op.day}`,
        method,
        body;
      if (op.type === "stroke") {
        url += "/strokes";
        method = "POST";
        body = op.payload;
      } else if (op.type === "delete") {
        url += `/strokes/${op.id}`;
        method = "DELETE";
      } else if (op.type === "review") {
        url += "/reviews";
        method = "PUT";
        body = op.payload;
      } else continue;
      const response = await fetch(url, {
        method,
        headers: { "Content-Type": "application/json" },
        body: body ? JSON.stringify(body) : undefined,
      });
      if (response.status === 401) {
        authExpired = true;
        await updateStatus();
        return;
      }
      if (!response.ok) {
        const detail = (await response.json().catch(() => ({}))).detail;
        throw Error(detail || "Não foi possível sincronizar");
      }
      await remove("queue", op.key);
    }
    await readServer();
    await updateStatus();
  } catch (error) {
    if (authExpired) await updateStatus();
    else setStatus(error.message?.includes("cobrança") ? error.message
      : "Salvo no tablet · aguardando servidor", error.message?.includes("cobrança") ? "error" : "pending");
  } finally {
    syncBusy = false;
    if (syncRequested) {
      syncRequested = false;
      setTimeout(sync, 0);
    }
  }
}
window.addEventListener("online", sync);
window.addEventListener("offline", updateStatus);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) sync();
});
setInterval(() => {
  if (navigator.onLine) sync();
}, 30000);

function escapeHtml(value) {
  return String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
}
function scheduleTitle(entry) {
  if (entry.type !== "Obra") return entry.client || entry.location || "Levantamento";
  return catalog.works.find((item) => Number(item.id) === Number(entry.workId))?.name
    || entry.tabletWorkText || entry.historicalWorkText || entry.client || "Obra não localizada";
}
function scheduleDetail(entry) {
  const team = catalog.teams.find((item) => Number(item.id) === Number(entry.teamId))?.name;
  const company = catalog.companies.find((item) => Number(item.id) === Number(entry.companyId))?.name;
  const supportTeams = !entry.appointmentExtraTeam && scheduleEntries.filter((item) => item.appointmentExtraTeam
    && Number(item.appointmentGroupId) === Number(entry.appointmentGroupId || entry.id))
    .map((item) => catalog.teams.find((candidate) => Number(candidate.id) === Number(item.teamId))?.name)
    .filter(Boolean);
  return [entry.scheduledTime, team || "Equipe a definir", company, entry.status,
    entry.appointmentExtraTeam ? `Adicional variável: ${new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" }).format(Number(entry.value || 0))}` : entry.dailyType === "Quarto" ? "1/4 de diária" : entry.dailyType === "Meia" ? "Meia diária" : "Diária inteira",
    supportTeams?.length ? `Equipes de apoio: ${supportTeams.join(", ")}` : "",
    entry.registrationPending ? "Obra não cadastrada · Sem financeiro" : ""].filter(Boolean).join(" · ");
}
function shortCanvasText(value, limit) {
  return value.length > limit ? `${value.slice(0, limit - 1)}…` : value;
}
function scheduleLineSlots() {
  const occupied = new Set(reviews.filter((review) => review.text_original?.trim()
    || review.unregistered_work_text?.trim() || review.schedule_id)
    .map((review) => review.line_number));
  for (const stroke of ink.filter((item) => !item.deleted)) occupied.add(lineOf(stroke));
  const linked = new Set(reviews.map((review) => String(review.id)));
  const unlinked = scheduleEntries.filter((entry) => !entry.tabletAgendaId
    || !linked.has(String(entry.tabletAgendaId)));
  unlinked.sort((a, b) => String(a.scheduledTime || "99:99").localeCompare(String(b.scheduledTime || "99:99"))
    || scheduleTitle(a).localeCompare(scheduleTitle(b), "pt-BR"));
  const free = Array.from({ length: LINES }, (_, line) => line).filter((line) => !occupied.has(line));
  return { lines: new Map(unlinked.slice(0, free.length).map((entry, index) => [free[index], entry])),
    overflow: unlinked.slice(free.length) };
}
function renderDailyPanels() {
  const requestsPanel = document.getElementById("requestsPanel");
  requestsPanel.hidden = !pendingRequests.length;
  requestsPanel.innerHTML = pendingRequests.length
    ? `<div class="panel-heading"><strong>${pendingRequests.length} solicitação${pendingRequests.length > 1 ? "ões" : ""} da Projetista aguardando marcação</strong>${"Notification" in window && Notification.permission === "default" ? '<button id="enableNotifications" class="subtle">Ativar avisos no tablet</button>' : ""}</div>` +
      pendingRequests.map((item) => `<div class="request-item"><strong>${escapeHtml(item.client_name)}</strong><span>${escapeHtml([item.service_type, item.location].filter(Boolean).join(" · "))}</span></div>`).join("")
    : "";
  notifyPendingRequests();
}
document.getElementById("requestsPanel").addEventListener("click", async (event) => {
  if (event.target.id !== "enableNotifications") return;
  const permission = await Notification.requestPermission();
  renderDailyPanels();
  if (permission === "granted") notifyPendingRequests();
});
const notifyingRequests = new Set();
async function notifyPendingRequests() {
  if (!("Notification" in window) || Notification.permission !== "granted" || !pendingRequests.length) return;
  const notified = new Set(JSON.parse(localStorage.getItem("agenda-request-notified") || "[]"));
  for (const item of pendingRequests) {
    const id = String(item.id);
    if (notified.has(id) || notifyingRequests.has(id)) continue;
    notifyingRequests.add(id);
    try {
      const worker = await navigator.serviceWorker.ready;
      await worker.showNotification("Nova solicitação da Projetista", {
        body: `${item.client_name}${item.location ? ` · ${item.location}` : ""}`,
        tag: `agenda-projectist-${id}`, data: { url: "/agenda-tablet" },
      });
      notified.add(id);
      localStorage.setItem("agenda-request-notified", JSON.stringify([...notified]));
    } catch (error) { console.warn("Aviso do tablet indisponível", error); }
    finally { notifyingRequests.delete(id); }
  }
}
function selectedOption(items, value) {
  return (
    '<option value="">Selecionar…</option>' +
    items
      .map(
        (x) =>
          `<option value="${x.id}" ${Number(value) === Number(x.id) ? "selected" : ""}>${escapeHtml(x.name)}</option>`,
      )
      .join("")
  );
}
function cardFor(line) {
  let review = reviews.find((x) => x.line_number === line);
  if (!review) {
    review = {
      id: crypto.randomUUID(),
      day,
      line_number: line,
      text_original: "",
      team_id: null,
      company_id: null,
      work_id: null,
      scheduled_time: null,
      daily_type: "Inteira",
      extra_teams: [],
      observation: "",
      unregistered_work_text: "",
      include_financial: true,
      price_override: null,
      schedule_id: null,
      key: "",
    };
    review.key = key(review);
    reviews.push(review);
    put("reviews", review);
  }
  return review;
}
function cardHtml(line) {
  const r = cardFor(line),
    works = catalog.works.filter(
      (x) => !r.company_id || Number(x.companyId) === Number(r.company_id),
    );
  const hasWork = Boolean(r.work_id || r.unregistered_work_text?.trim());
  const message = r.schedule_id ? "Agendamento confirmado no sistema."
    : hasWork ? "Ainda não consta no agendamento do sistema."
    : "Escolha uma obra para criar o agendamento.";
  return `<article class="card ${r.unregistered_work_text ? "unregistered" : ""}" data-line="${line}">
    <h3>Linha ${line + 1} ${r.schedule_id ? "· Agendada" : ""}</h3>
    <canvas class="line-preview" width="900" height="90"></canvas>
    ${r.unregistered_work_text ? '<div class="registration-warning">Obra não cadastrada · Sem dados financeiros. O agendamento pode ser feito.</div>' : ""}
    <textarea data-field="text_original" placeholder="Texto reconhecido ou digitado para conferir">${escapeHtml(r.text_original)}</textarea>
    <div class="fields">
      <label>Equipe<select data-field="team_id">${selectedOption(catalog.teams, r.team_id)}</select></label>
      <label>Empresa<select data-field="company_id">${selectedOption(catalog.companies, r.company_id)}</select></label>
      <label>Obra cadastrada<select data-field="work_id">${selectedOption(works, r.work_id)}</select></label>
      <label>Obra nova<input data-field="unregistered_work_text" value="${escapeHtml(r.unregistered_work_text || "")}" placeholder="Nome escrito na agenda"></label>
      <label>Horário<input data-field="scheduled_time" type="time" value="${escapeHtml(r.scheduled_time || "")}"></label>
      <label>Diária<select data-field="daily_type"><option ${r.daily_type === "Inteira" ? "selected" : ""}>Inteira</option><option ${r.daily_type === "Meia" ? "selected" : ""}>Meia</option><option ${r.daily_type === "Quarto" ? "selected" : ""}>Quarto</option></select></label>
      <label>Valor para este serviço (R$)<input data-field="price_override" type="number" min="0" step="0.01" value="${r.price_override == null ? "" : escapeHtml(r.price_override)}" placeholder="Usar valor da obra"></label>
      <label>Faturamento<select data-field="include_financial"><option value="true" ${r.include_financial !== false && r.include_financial !== 0 ? "selected" : ""}>Incluir no faturamento</option><option value="false" ${r.include_financial === false || r.include_financial === 0 ? "selected" : ""}>Sem faturamento</option></select></label>
      <label>Observação<input data-field="observation" value="${escapeHtml(r.observation)}"></label>
    </div>
    <details class="extra-team-fields" ${r.extra_teams?.length ? "open" : ""}>
      <summary>Equipes adicionais e valor variável</summary>
      <small>Marque as equipes de apoio e informe o adicional em reais ou como fração da diária inteira (ex.: 1/4).</small>
      ${catalog.teams.filter((team) => Number(team.id) !== Number(r.team_id)).map((team) => {
        const extra = (r.extra_teams || []).find((item) => Number(item.team_id) === Number(team.id));
        return `<label class="extra-team-row"><input type="checkbox" data-extra-team-id="${team.id}" ${extra ? "checked" : ""}>${escapeHtml(team.name)}<input type="text" inputmode="decimal" data-extra-amount="${team.id}" placeholder="R$ ou 1/4" value="${escapeHtml(extra?.amount || "")}" ${extra ? "" : "disabled"}></label>`;
      }).join("")}
    </details>
    <div class="card-actions"><button class="subtle read-ink">Ler escrita</button><button class="subtle clear-line">Apagar linha</button>${!r.schedule_id && hasWork ? '<button class="subtle send-line">Enviar ao agendamento</button>' : ""}<small class="message">${message}</small></div>
  </article>`;
}
function renderPreview(card, line) {
  const preview = card.querySelector("canvas"),
    p = preview.getContext("2d");
  p.fillStyle = "#fffefa";
  p.fillRect(0, 0, 900, 90);
  p.save();
  p.translate(-62, -(TOP + line * STEP));
  p.scale(0.9, 1);
  for (const stroke of ink.filter((x) => !x.deleted && lineOf(x) === line)) {
    p.strokeStyle = stroke.color;
    p.lineWidth = stroke.width;
    p.lineCap = "round";
    p.lineJoin = "round";
    p.beginPath();
    stroke.points.forEach(([x, y], i) => (i ? p.lineTo(x, y) : p.moveTo(x, y)));
    p.stroke();
  }
  p.restore();
}
function renderReviews() {
  const lines = [
    ...new Set([
      ...ink.filter((x) => !x.deleted).map(lineOf),
      ...reviews.filter((x) => x.text_original || x.unregistered_work_text || x.schedule_id)
        .map((x) => x.line_number),
    ]),
  ].sort((a, b) => a - b);
  reviewCards.innerHTML = lines.length
    ? lines.map(cardHtml).join("")
    : "<p>Escreva na página para começar.</p>";
  reviewCards.querySelectorAll(".card").forEach((card) => {
    const line = Number(card.dataset.line);
    renderPreview(card, line);
    card
      .querySelectorAll("[data-field]")
      .forEach((input) =>
        input.addEventListener("input", () => editReview(line, input)),
      );
    card.querySelectorAll("[data-extra-team-id]").forEach((checkbox) => {
      checkbox.addEventListener("change", () => {
        const amount = card.querySelector(`[data-extra-amount="${checkbox.dataset.extraTeamId}"]`);
        amount.disabled = !checkbox.checked;
        saveExtraTeams(line, card);
      });
      card.querySelector(`[data-extra-amount="${checkbox.dataset.extraTeamId}"]`)
        .addEventListener("input", () => saveExtraTeams(line, card));
    });
    card.querySelector(".read-ink").onclick = () => recognizeLine(line, card);
    card.querySelector(".clear-line").onclick = () => clearLine(line);
    const send = card.querySelector(".send-line");
    if (send) send.onclick = async () => {
      send.disabled = true;
      while (syncBusy) await new Promise((resolve) => setTimeout(resolve, 50));
      await sync();
      renderReviews();
      if (!reviews.find((review) => review.line_number === line)?.schedule_id)
        reviewCards.querySelector(`.card[data-line="${line}"] .message`).textContent = navigator.onLine
          ? "Ainda não consta no sistema. Confira os dados e tente novamente."
          : "Salvo no tablet; será enviado quando a conexão voltar.";
    };
  });
}
async function clearLine(line) {
  for (const stroke of ink.filter((item) => !item.deleted && lineOf(item) === line)) {
    const deleted = { ...stroke, deleted: 1 };
    await putQueued("ink", deleted, {
      key: `delete:${stroke.id}`, type: "delete", day: stroke.day, id: stroke.id,
    });
    stroke.deleted = 1;
  }
  const review = cardFor(line);
  Object.assign(review, { text_original: "", team_id: null, company_id: null,
    work_id: null, scheduled_time: null, unregistered_work_text: "", observation: "", extra_teams: [] });
  await persistReview(review);
  redraw();
  renderTextRows();
  renderReviews();
}
const aliasesOverlay = document.getElementById("aliasesOverlay");
const aliasKind = document.getElementById("aliasKind");
const aliasTarget = document.getElementById("aliasTarget");
function renderAliasTargets() {
  const list = catalog[{ team: "teams", company: "companies", work: "works" }[aliasKind.value]];
  aliasTarget.innerHTML = '<option value="">Escolha o cadastro…</option>' +
    list.map((item) => `<option value="${item.id}">${escapeHtml(item.name)}</option>`).join("");
}
function renderAliases() {
  const names = { team: "Equipe", company: "Empresa", work: "Obra" };
  const lists = { team: catalog.teams, company: catalog.companies, work: catalog.works };
  document.getElementById("aliasList").innerHTML = catalog.aliases.length
    ? catalog.aliases.map((alias, index) => {
      const target = lists[alias.kind].find((item) => Number(item.id) === Number(alias.target_id));
      return `<div class="alias-item"><span><strong>${escapeHtml(alias.alias)}</strong> → ${names[alias.kind]}: ${escapeHtml(target?.name || "Cadastro removido")}</span><button class="subtle remove-alias" data-index="${index}" type="button">Excluir</button></div>`;
    }).join("")
    : "<p>Nenhuma sigla cadastrada ainda.</p>";
}
document.getElementById("aliasesButton").onclick = () => {
  aliasesOverlay.hidden = false;
  renderAliasTargets();
  renderAliases();
};
document.getElementById("closeAliases").onclick = () => { aliasesOverlay.hidden = true; };
aliasKind.onchange = renderAliasTargets;
document.getElementById("aliasForm").onsubmit = async (event) => {
  event.preventDefault();
  const payload = {
    alias: document.getElementById("aliasName").value.trim(),
    kind: aliasKind.value,
    target_id: Number(aliasTarget.value),
  };
  const message = document.getElementById("aliasMessage");
  if (!payload.alias || !payload.target_id) { message.textContent = "Escolha a sigla e o cadastro."; return; }
  try {
    const response = await fetch("/api/tablet-agenda/aliases", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
    if (!response.ok) throw Error((await response.json()).detail || "Não foi possível guardar a sigla.");
    if (!catalog.aliases.some((a) => a.alias === payload.alias.toLowerCase() && a.kind === payload.kind && Number(a.target_id) === payload.target_id))
      catalog.aliases.push({ ...payload, alias: payload.alias.toLowerCase() });
    await put("meta", { key: "catalog", value: catalog });
    document.getElementById("aliasName").value = "";
    message.textContent = "Sigla vinculada. Já pode usar na agenda.";
    renderAliases();
  } catch (error) { message.textContent = error.message; }
};
document.getElementById("aliasList").onclick = async (event) => {
  const button = event.target.closest(".remove-alias");
  if (!button) return;
  const alias = catalog.aliases[Number(button.dataset.index)];
  const message = document.getElementById("aliasMessage");
  try {
    const response = await fetch("/api/tablet-agenda/aliases", {
      method: "DELETE", headers: { "Content-Type": "application/json" }, body: JSON.stringify(alias),
    });
    if (!response.ok) throw Error("Não foi possível excluir a sigla.");
    catalog.aliases.splice(Number(button.dataset.index), 1);
    await put("meta", { key: "catalog", value: catalog });
    message.textContent = "Sigla excluída.";
    renderAliases();
  } catch (error) { message.textContent = error.message; }
};
async function persistReview(r) {
  r.key = key(r);
  await putQueued("reviews", r, {
    key: `review:${r.id}:${crypto.randomUUID()}`,
    type: "review",
    day: r.day,
    id: r.id,
    payload: {
      id: r.id,
      line_number: r.line_number,
      text_original: r.text_original,
      team_id: r.team_id,
      company_id: r.company_id,
      work_id: r.work_id,
      scheduled_time: r.scheduled_time || null,
      daily_type: r.daily_type,
      extra_teams: r.extra_teams || [],
      observation: r.observation,
      unregistered_work_text: r.unregistered_work_text || "",
      include_financial: r.include_financial !== false && r.include_financial !== 0,
      price_override: r.price_override == null || r.price_override === "" ? null : Number(r.price_override),
    },
  });
}
function editReview(line, input) {
  const r = cardFor(line),
    field = input.dataset.field;
  r[field] = field === "include_financial" ? input.value === "true"
    : field === "price_override" ? (input.value === "" ? null : Number(input.value))
    : ["team_id", "company_id", "work_id"].includes(field)
    ? input.value
      ? Number(input.value)
      : null
    : input.value;
  if (field === "text_original") {
    resolveText(r);
  } else if (field === "company_id") {
    r.work_id = null;
    renderReviews();
  } else if (field === "work_id" && r.work_id) {
    const work = catalog.works.find((x) => Number(x.id) === Number(r.work_id));
    if (work) r.company_id = Number(work.companyId);
    r.unregistered_work_text = "";
    renderReviews();
  } else if (field === "unregistered_work_text" && r.unregistered_work_text) {
    r.work_id = null;
    input.closest(".card").classList.add("unregistered");
  } else if (field === "text_original" && !r.text_original.trim()) {
    r.unregistered_work_text = "";
    r.work_id = r.company_id = r.team_id = null;
  }
  persistReview(r).catch(console.error);
  clearTimeout(reviewTimers.get(r.id));
  reviewTimers.set(
    r.id,
    setTimeout(async () => {
      if (field === "text_original") suggest(r);
    }, 450),
  );
}
function saveExtraTeams(line, card) {
  const review = cardFor(line);
  review.extra_teams = [...card.querySelectorAll("[data-extra-team-id]:checked")].map((checkbox) => ({
    team_id: Number(checkbox.dataset.extraTeamId),
    amount: card.querySelector(`[data-extra-amount="${checkbox.dataset.extraTeamId}"]`).value.trim(),
  }));
  persistReview(review).catch(console.error);
}
function fold(value) {
  return String(value)
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase();
}
const kinds = [
  ["team", "teams", "Equipe"],
  ["company", "companies", "Empresa"],
  ["work", "works", "Obra"],
];
function namesFor(kind, item) {
  return [item.name, ...catalog.aliases
    .filter((a) => a.kind === kind && Number(a.target_id) === Number(item.id))
    .map((a) => a.alias)];
}
function exactMatches(kind, list, token) {
  const query = fold(token.trim());
  if (!query) return [];
  return list.filter((item) => namesFor(kind, item).some((name) => fold(name) === query));
}
function resolveText(review) {
  let entryText = review.text_original.replace(/\b1\s*\/\s*2\b/g, "MEIA")
    .replace(/\b1\s*\/\s*4\b/g, "QUARTO");
  const priceMatch = entryText.match(/(?:\/|\s+)(\+?\s*(?:R\$\s*)?\d[\d.,]*)\s*$/i);
  const priceToken = priceMatch ? priceMatch[1].replace(/\s/g, "").replace(/^R\$/i, "") : "";
  if (priceMatch) entryText = entryText.slice(0, priceMatch.index).trim();
  const tokens = entryText.split("/").map((x) => x.trim()).filter(Boolean);
  const priceRaw = priceToken.replace(/^\+/, "");
  const normalizedPrice = priceRaw.includes(",")
    ? priceRaw.replace(/\./g, "").replace(",", ".")
    : /^\d{1,3}(?:\.\d{3})+$/.test(priceRaw) ? priceRaw.replace(/\./g, "") : priceRaw;
  const priceNumber = Number(normalizedPrice);
  review.manual_price_mode = priceToken ? (priceToken.startsWith("+") ? "surcharge" : "total") : "";
  review.manual_price_amount = Number.isFinite(priceNumber) ? priceNumber : 0;
  const found = { team: new Set(), company: new Set(), work: new Set() };
  const unknown = [];
  const previousExtras = new Map((review.extra_teams || []).map((item) => [Number(item.team_id), item]));
  review.team_id = review.company_id = review.work_id = null;
  review.scheduled_time = null;
  review.daily_type = /(?:^|[^\w])quarto(?:$|[^\w])|(?:^|\D)1\s*\/\s*4(?:$|\D)/i.test(fold(review.text_original))
    ? "Quarto" : /(?:^|[^\w])meia(?:$|[^\w])|(?:^|\D)1\s*\/\s*2(?:$|\D)/i.test(fold(review.text_original))
      ? "Meia" : "Inteira";
  for (const token of tokens) {
    if (/^(meia|inteira|quarto)$/i.test(fold(token))) continue;
    const hour = token.match(/^(\d{1,2})[:h.](\d{2})$/i);
    if (hour && Number(hour[1]) < 24 && Number(hour[2]) < 60) {
      review.scheduled_time = `${hour[1].padStart(2, "0")}:${hour[2]}`;
      continue;
    }
    let matched = false;
    for (const [kind, listName] of kinds) {
      let matches = exactMatches(kind, catalog[listName], token);
      if (!matches.length && kind === "team" && token.length >= 2) {
        const query = fold(token);
        matches = catalog.teams.filter((item) => namesFor(kind, item)
          .some((name) => fold(name).startsWith(query)));
      }
      if (matches.length === 1) {
        found[kind].add(Number(matches[0].id));
        matched = true;
      }
    }
    if (!matched) unknown.push(token);
  }
  const teamIds = [...found.team];
  if (teamIds.length) review.team_id = teamIds[0];
  const extraTeamIds = [...new Set([...teamIds.slice(1), ...previousExtras.keys()])]
    .filter((teamId) => Number(teamId) !== Number(review.team_id));
  review.extra_teams = extraTeamIds.map((teamId) =>
    previousExtras.get(teamId) || { team_id: teamId, amount: "" },
  );
  for (const [kind] of kinds) {
    if (kind !== "team" && found[kind].size === 1) review[`${kind}_id`] = [...found[kind]][0];
  }
  if (review.work_id) {
    const work = catalog.works.find((x) => Number(x.id) === review.work_id);
    if (work) review.company_id = Number(work.companyId);
  }
  review.unregistered_work_text = review.work_id ? "" :
    [...unknown].sort((a, b) => b.length - a.length)[0]?.slice(0, 200) || "";
}
function resultText(review) {
  const name = (items, id) => items.find((x) => Number(x.id) === Number(id))?.name;
  return [
    review.team_id && `Equipe: ${name(catalog.teams, review.team_id)}`,
    review.company_id && `Empresa: ${name(catalog.companies, review.company_id)}`,
    review.work_id && `Obra: ${name(catalog.works, review.work_id)}`,
    review.unregistered_work_text && `Obra nova: ${review.unregistered_work_text}${review.manual_price_amount > 0 ? " · Com valor para o Financeiro" : " · Sem financeiro"}`,
    review.scheduled_time && `Horário: ${review.scheduled_time}`,
    `Diária: ${review.daily_type === "Quarto" ? "1/4" : review.daily_type || "Inteira"}`,
    review.manual_price_mode === "total" && review.manual_price_amount > 0 && `Valor neste agendamento: ${new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" }).format(review.manual_price_amount)}`,
    review.manual_price_mode === "surcharge" && review.manual_price_amount > 0 && `Adicional neste agendamento: ${new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" }).format(review.manual_price_amount)}`,
    review.extra_teams?.length ? `Equipes adicionais: ${review.extra_teams.map((item) => name(catalog.teams, item.team_id)).filter(Boolean).join(", ")}` : "",
  ].filter(Boolean).join(" · ") || "Digite uma equipe, empresa, obra ou horário";
}
function renderTextRows() {
  const slots = scheduleLineSlots();
  const firstFree = Array.from({ length: LINES }, (_, line) => line)
    .find((line) => !slots.lines.has(line));
  const scheduledRow = (entry, line) => `<div class="text-row scheduled-row ${entry.registrationPending ? "unregistered" : ""}" data-line="${line}">
    <span class="line-number">${line + 1}</span><div class="line-content">
      <strong class="scheduled-title">${escapeHtml(scheduleTitle(entry))}</strong>
      <div class="line-result">${escapeHtml(scheduleDetail(entry))}</div>
    </div><span class="scheduled-source">Sistema</span></div>`;
  textNotebook.innerHTML = `<div class="text-page-title">Agenda · ${day.split("-").reverse().join("/")}<span class="weekday"> · ${weekdayLabel(day)}</span><small>${scheduleEntries.length} agendamento${scheduleEntries.length === 1 ? "" : "s"} do dia</small></div>` +
    Array.from({ length: LINES }, (_, line) => {
      if (slots.lines.has(line)) return scheduledRow(slots.lines.get(line), line);
      const review = reviews.find((x) => x.line_number === line);
      return `<div class="text-row ${review?.unregistered_work_text ? "unregistered" : ""}" data-line="${line}"><span class="line-number">${line + 1}</span><div class="line-content"><input class="line-entry" aria-label="Linha ${line + 1}" autocomplete="off" value="${escapeHtml(review?.text_original || "")}" placeholder="${line === firstFree ? "Ex.: E/MD/BETT/7:30 ou Cervejaria" : ""}"><div class="line-result">${review ? escapeHtml(resultText(review)) : ""}</div><div class="line-suggestions" hidden></div></div></div>`;
    }).join("") + slots.overflow.map((entry, index) => scheduledRow(entry, LINES + index)).join("");
}
function activeToken(input) {
  return input.value.slice(0, input.selectionStart ?? input.value.length).split("/").at(-1).trim();
}
function showSuggestions(input) {
  const menu = input.closest(".line-content").querySelector(".line-suggestions");
  const token = fold(activeToken(input));
  if (!token) { menu.hidden = true; menu.innerHTML = ""; return; }
  const suggestions = kinds.flatMap(([kind, listName, label]) =>
    catalog[listName].filter((item) => namesFor(kind, item).some((name) => fold(name).includes(token)))
      .map((item) => ({ kind, label, item }))).slice(0, 7);
  menu.innerHTML = suggestions.map(({ kind, label, item }, index) =>
    `<button type="button" role="option" aria-selected="${index === 0}" class="${index === 0 ? "selected" : ""}" data-kind="${kind}" data-id="${item.id}"><strong>${label}: ${escapeHtml(item.name)}</strong>${kind === "work" ? `<small>${escapeHtml(catalog.companies.find((x) => Number(x.id) === Number(item.companyId))?.name || "")}</small>` : ""}</button>`).join("");
  menu.setAttribute("role", "listbox");
  menu.hidden = !suggestions.length;
}
function acceptLineSuggestion(input, button) {
  const listName = { team: "teams", company: "companies", work: "works" }[button.dataset.kind];
  const item = catalog[listName]?.find((candidate) => Number(candidate.id) === Number(button.dataset.id));
  if (!item) return false;
  const position = input.selectionStart ?? input.value.length;
  const start = input.value.lastIndexOf("/", position - 1) + 1;
  const nextSeparator = input.value.indexOf("/", position);
  input.setRangeText(item.name, start, nextSeparator < 0 ? input.value.length : nextSeparator, "end");
  scheduleTextSave(input);
  input.closest(".line-content").querySelector(".line-suggestions").hidden = true;
  input.focus();
  return true;
}
function scheduleTextSave(input) {
  if (/\d\/:/.test(input.value)) {
    const position = input.selectionStart ?? input.value.length;
    input.value = input.value.replace(/(\d)\/:/g, "$1:");
    input.setSelectionRange(Math.max(0, position - 1), Math.max(0, position - 1));
  }
  const line = Number(input.closest(".text-row").dataset.line);
  if (!input.value.trim() && !reviews.some((x) => x.line_number === line)) return;
  const review = cardFor(line);
  review.text_original = input.value;
  resolveText(review);
  input.closest(".text-row").classList.toggle("unregistered", Boolean(review.unregistered_work_text));
  input.closest(".line-content").querySelector(".line-result").textContent = resultText(review);
  clearTimeout(reviewTimers.get(review.id));
  persistReview(review).catch(console.error);
  showSuggestions(input);
}
textNotebook.addEventListener("input", (event) => {
  if (event.target.classList.contains("line-entry")) scheduleTextSave(event.target);
});
textNotebook.addEventListener("focusout", (event) => {
  if (!event.target.classList.contains("line-entry")) return;
  if (event.relatedTarget?.closest(".line-suggestions")) return;
  setTimeout(() => {
    if (!document.activeElement?.classList.contains("line-entry")) renderTextRows();
  }, 0);
});
let pendingShiftInput = null;
let shiftUsedWithKey = false;
textNotebook.addEventListener("keydown", (event) => {
  const input = event.target;
  if (!input.classList.contains("line-entry") || input.disabled) return;
  if (event.key === "Shift" && !event.repeat) {
    pendingShiftInput = input;
    shiftUsedWithKey = false;
    return;
  }
  if (pendingShiftInput) shiftUsedWithKey = true;
  const menu = input.closest(".line-content").querySelector(".line-suggestions");
  const options = [...menu.querySelectorAll("button")];
  if (event.key === "Tab" && !menu.hidden && options.length) {
    const selected = options.find((option) => option.classList.contains("selected")) || options[0];
    if (acceptLineSuggestion(input, selected)) event.preventDefault();
  } else if ((event.key === "ArrowDown" || event.key === "ArrowUp") && !menu.hidden && options.length) {
    event.preventDefault();
    const selectedIndex = options.findIndex((option) => option.classList.contains("selected"));
    const nextIndex = (selectedIndex + (event.key === "ArrowDown" ? 1 : -1) + options.length) % options.length;
    options.forEach((option, index) => {
      const selected = index === nextIndex;
      option.classList.toggle("selected", selected);
      option.setAttribute("aria-selected", String(selected));
    });
  } else if (event.key === "/") {
    event.preventDefault();
    const pos = input.selectionStart ?? input.value.length;
    input.setRangeText("/", pos, input.selectionEnd ?? pos, "end");
    scheduleTextSave(input);
  } else if (event.key === "Enter") {
    event.preventDefault();
    input.closest(".text-row").nextElementSibling?.querySelector(".line-entry")?.focus();
  }
});
textNotebook.addEventListener("keyup", (event) => {
  if (event.key !== "Shift" || !pendingShiftInput) return;
  const input = pendingShiftInput;
  pendingShiftInput = null;
  if (shiftUsedWithKey || input !== event.target || input.disabled) return;
  const pos = input.selectionStart ?? input.value.length;
  if (pos && input.value[pos - 1] !== "/") {
    input.setRangeText("/", pos, input.selectionEnd ?? pos, "end");
    scheduleTextSave(input);
  }
});
textNotebook.addEventListener("click", (event) => {
  const button = event.target.closest(".line-suggestions button");
  if (!button || button.closest(".line-suggestions").hidden) return;
  acceptLineSuggestion(button.closest(".text-row").querySelector(".line-entry"), button);
});
textNotebook.addEventListener("pointerdown", (event) => {
  const button = event.target.closest(".line-suggestions button");
  if (!button || button.closest(".line-suggestions").hidden) return;
  event.preventDefault();
  acceptLineSuggestion(button.closest(".text-row").querySelector(".line-entry"), button);
});
async function suggest(r) {
  let result;
  try {
    const response = await fetch(
      `/api/tablet-agenda/interpret?text=${encodeURIComponent(r.text_original)}`,
    );
    if (response.ok) result = await response.json();
  } catch (_) {}
  if (!result) {
    const text = fold(r.text_original);
    const matches = {};
    for (const [kind, list] of [
      ["team", catalog.teams],
      ["company", catalog.companies],
      ["work", catalog.works],
    ])
      matches[kind] = list.filter((x) =>
        [
          x.name,
          ...catalog.aliases
            .filter((a) => a.kind === kind && a.target_id === x.id)
            .map((a) => a.alias),
        ].some(
          (name) =>
            name &&
            new RegExp(
              `(^|\\W)${fold(name).replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}($|\\W)`,
            ).test(text),
        ),
      );
    result = { matches, scheduled_time: null,
      daily_type: /(?:^|[^\w])quarto(?:$|[^\w])|(?:^|\D)1\s*\/\s*4(?:$|\D)/i.test(text)
        ? "Quarto" : /(?:^|[^\w])meia(?:$|[^\w])|(?:^|\D)1\s*\/\s*2(?:$|\D)/i.test(text) ? "Meia" : "Inteira" };
  }
  if (result.matches.work?.length === 1) {
    const work = catalog.works.find((x) => Number(x.id) === Number(result.matches.work[0].id));
    if (work) result.matches.company = [{ id: work.companyId }];
  }
  let changed = false;
  for (const [kind, field] of [
    ["team", "team_id"],
    ["company", "company_id"],
    ["work", "work_id"],
  ]) {
    const choices = result.matches[kind] || [];
    if (!r[field] && choices.length === 1) {
      r[field] = Number(choices[0].id);
      changed = true;
    }
  }
  if (!r.scheduled_time && result.scheduled_time) {
    r.scheduled_time = result.scheduled_time;
    changed = true;
  }
  if (result.daily_type && r.daily_type !== result.daily_type) {
    r.daily_type = result.daily_type;
    changed = true;
  }
  if (r.work_id && r.unregistered_work_text) {
    r.unregistered_work_text = "";
    changed = true;
  }
  if (changed) {
    await persistReview(r);
    renderReviews();
  }
}
function loadOcr() {
  if (!ocrReady)
    ocrReady = new Promise((resolve, reject) => {
      if (window.Tesseract) {
        resolve(window.Tesseract);
        return;
      }
      const script = document.createElement("script");
      script.src =
        "https://cdn.jsdelivr.net/npm/tesseract.js@5/dist/tesseract.min.js";
      script.onload = () => resolve(window.Tesseract);
      script.onerror = () => reject(Error("Leitor de escrita indisponível"));
      document.head.appendChild(script);
    });
  return ocrReady;
}
async function recognizeLine(line, card) {
  const r = cardFor(line),
    message = card.querySelector(".message");
  if (!navigator.onLine) {
    message.textContent =
      "Sem conexão. A escrita está salva; a leitura fica para depois.";
    return;
  }
  message.textContent = "Lendo escrita…";
  try {
    const tesseract = await loadOcr();
    const result = await tesseract.recognize(
      card.querySelector("canvas"),
      "por",
    );
    const text = result.data.text.trim();
    if (!text) {
      message.textContent =
        "Não consegui ler esta linha. Você pode digitar o texto.";
      return;
    }
    r.text_original = text;
    resolveText(r);
    await persistReview(r);
    await suggest(r);
    renderReviews();
  } catch (error) {
    message.textContent = "Leitura indisponível. Digite o texto para conferir.";
  }
}
async function confirmLine(line, card) {
  const r = cardFor(line),
    message = card.querySelector(".message");
  if (!r.team_id || !r.company_id || !r.work_id) {
    message.textContent = "Escolha equipe, empresa e obra.";
    message.className = "message warning";
    return;
  }
  if (!navigator.onLine) {
    message.textContent = "Salvo no tablet. Confirme quando a conexão voltar.";
    return;
  }
  clearTimeout(reviewTimers.get(r.id));
  reviewTimers.delete(r.id);
  await persistReview(r);
  while (syncBusy) await new Promise((resolve) => setTimeout(resolve, 50));
  await sync();
  if ((await pending()).some((op) => op.type === "review" && op.id === r.id)) {
    message.textContent = "Aguarde sincronização para confirmar.";
    return;
  }
  try {
    const response = await fetch(
      `/api/tablet-agenda/pages/${day}/reviews/${r.id}/confirm`,
      { method: "POST" },
    );
    const result = await response.json();
    if (!response.ok)
      throw Error(result.detail || "Não foi possível confirmar");
    r.schedule_id = result.schedule_id;
    const current = reviews.find((item) => item.id === r.id) || r;
    current.schedule_id = result.schedule_id;
    await put("reviews", current);
    renderReviews();
  } catch (error) {
    message.textContent = error.message;
    message.className = "message warning";
  }
}
document.getElementById("reviewButton").onclick = async () => {
  reviewOverlay.hidden = false;
  renderReviews();
  if (navigator.onLine) {
    for (const card of reviewCards.querySelectorAll(".card")) {
      const line = Number(card.dataset.line),
        r = cardFor(line);
      if (!r.text_original && !r.schedule_id) await recognizeLine(line, card);
    }
  }
};
document.getElementById("closeReview").onclick = () =>
  (reviewOverlay.hidden = true);
window.addEventListener("beforeinstallprompt", (event) => {
  event.preventDefault();
  installPrompt = event;
  document.getElementById("install").hidden = false;
});
document.getElementById("install").onclick = async () => {
  if (!installPrompt) return;
  await installPrompt.prompt();
  installPrompt = null;
  document.getElementById("install").hidden = true;
};
setMode(mode);
(async () => {
  try {
    db = await openDb();
    const saved = (await all("meta")).find((x) => x.key === "catalog");
    if (saved) catalog = saved.value;
    await loadDay(localDay());
    if ("serviceWorker" in navigator)
      navigator.serviceWorker
        .register("/agenda-tablet/sw.js", { scope: "/" })
        .catch(console.warn);
  } catch (error) {
    setStatus("Armazenamento do tablet indisponível", "error");
    console.error(error);
  }
})();


const agendaSearchInput = document.getElementById("agendaSearch");
const agendaSearchResults = document.getElementById("agendaSearchResults");
const scheduleEditOverlay = document.getElementById("scheduleEditOverlay");
const scheduleEditForm = document.getElementById("scheduleEditForm");
const scheduleEditMessage = document.getElementById("scheduleEditMessage");
let agendaSearchTimer = null;
let agendaSearchItems = [];
let selectedScheduleItem = null;
const brl = (value) => new Intl.NumberFormat("pt-BR", {style: "currency", currency: "BRL"}).format(Number(value || 0));

async function searchAgendaEntries() {
  const query = agendaSearchInput.value.trim();
  if (query.length < 2) {
    agendaSearchResults.innerHTML = '<small>Digite ao menos duas letras para buscar em todos os dias.</small>';
    agendaSearchItems = [];
    return;
  }
  if (!navigator.onLine) {
    agendaSearchResults.textContent = "A busca precisa de conexão com o sistema.";
    return;
  }
  agendaSearchResults.textContent = "Pesquisando…";
  try {
    const response = await fetch(`/api/tablet-agenda/search?q=${encodeURIComponent(query)}`);
    const result = await response.json();
    if (!response.ok) throw Error(result.detail || "Não foi possível pesquisar.");
    if (agendaSearchInput.value.trim() !== query) return;
    agendaSearchItems = result.items || [];
    agendaSearchResults.innerHTML = agendaSearchItems.length
      ? `<p><strong>${agendaSearchItems.length} serviço(s) em ${result.days} dia(s)</strong></p><div class="agenda-search-list">${agendaSearchItems.map((item, index) => `
          <article class="agenda-search-item"><div><strong>${escapeHtml(item.date)} · ${escapeHtml(item.work)}</strong><small>${escapeHtml(item.company)} · ${escapeHtml(item.team)} · ${escapeHtml(item.status)} · ${brl(item.value)}</small></div><button type="button" class="subtle" data-search-edit="${index}">Editar</button></article>
        `).join("")}</div>`
      : '<p>Nenhum serviço encontrado para esta obra ou empresa.</p>';
  } catch (error) {
    agendaSearchResults.textContent = error.message;
  }
}
agendaSearchInput.addEventListener("input", () => {
  clearTimeout(agendaSearchTimer);
  agendaSearchTimer = setTimeout(searchAgendaEntries, 300);
});
agendaSearchResults.addEventListener("click", (event) => {
  const button = event.target.closest("[data-search-edit]");
  if (button) openScheduleEdit(agendaSearchItems[Number(button.dataset.searchEdit)]);
});

function openScheduleEdit(item) {
  if (!item) return;
  selectedScheduleItem = item;
  scheduleEditMessage.textContent = "";
  const companyOptions = catalog.companies.map((company) => `<option value="${company.id}" ${Number(item.company_id) === Number(company.id) ? "selected" : ""}>${escapeHtml(company.name)}</option>`).join("");
  scheduleEditForm.innerHTML = `
    <p><strong>${escapeHtml(item.date)} · ${escapeHtml(item.work)}</strong><br>${escapeHtml(item.team)}</p>
    <div class="schedule-edit-fields">
      <label>Empresa<select name="company_id" required><option value="">Selecione a empresa</option>${companyOptions}</select></label>
      <label>Obra<select name="work_id"></select></label>
      <label>Valor do serviço (R$)<input name="value" type="number" min="0" step="0.01" value="${Number(item.value || 0)}" required></label>
      <label>Situação<select name="status"><option ${item.status === "Planejado" ? "selected" : ""}>Planejado</option><option ${item.status === "Confirmado" ? "selected" : ""}>Confirmado</option><option ${item.status === "Cancelado" ? "selected" : ""}>Cancelado</option></select></label>
      <label>Faturamento<select name="include_financial"><option value="true" ${item.include_financial ? "selected" : ""}>Incluir no faturamento</option><option value="false" ${item.include_financial ? "" : "selected"}>Sem faturamento</option></select></label>
      <label>Horário<input name="scheduled_time" type="time" value="${escapeHtml(item.scheduled_time || "")}"></label>
      <label class="schedule-edit-note">Observação<textarea name="observation" maxlength="1000">${escapeHtml(item.observation || "")}</textarea></label>
    </div>
    <div class="card-actions"><button type="submit" class="primary">Salvar alteração</button><button type="button" class="subtle" id="viewScheduleDay">Ver este dia na agenda</button></div>`;
  const companySelect = scheduleEditForm.elements.company_id;
  const workSelect = scheduleEditForm.elements.work_id;
  function renderWorks() {
    const available = catalog.works.filter((work) => Number(work.companyId) === Number(companySelect.value));
    workSelect.innerHTML = `<option value="">${item.work_id ? "Selecione a obra" : "Manter obra não cadastrada"}</option>` + available.map((work) => `<option value="${work.id}" ${Number(work.id) === Number(item.work_id) ? "selected" : ""}>${escapeHtml(work.name)}</option>`).join("");
  }
  companySelect.addEventListener("change", () => { item = {...item, work_id: null}; renderWorks(); });
  renderWorks();
  document.getElementById("viewScheduleDay").onclick = async () => {
    scheduleEditOverlay.hidden = true;
    await loadDay(selectedScheduleItem.date);
  };
  scheduleEditOverlay.hidden = false;
}
document.getElementById("closeScheduleEdit").onclick = () => { scheduleEditOverlay.hidden = true; };
scheduleEditForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!selectedScheduleItem) return;
  if (!navigator.onLine) { scheduleEditMessage.textContent = "Conecte o tablet para salvar."; return; }
  if (syncBusy || (await pending()).length) { scheduleEditMessage.textContent = "Aguarde a sincronização das anotações antes de editar."; return; }
  const values = Object.fromEntries(new FormData(scheduleEditForm).entries());
  const submit = scheduleEditForm.querySelector('button[type="submit"]');
  submit.disabled = true;
  scheduleEditMessage.textContent = "Salvando…";
  try {
    const response = await fetch(`/api/tablet-agenda/schedule/${selectedScheduleItem.id}`, {
      method: "PATCH", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        company_id: Number(values.company_id) || null,
        work_id: Number(values.work_id) || null,
        value: Number(values.value), status: values.status,
        include_financial: values.include_financial === "true",
        scheduled_time: values.scheduled_time || "", observation: values.observation || "",
      }),
    });
    const result = await response.json();
    if (!response.ok) throw Error(result.detail || "Não foi possível salvar a alteração.");
    scheduleEditOverlay.hidden = true;
    await loadDay(selectedScheduleItem.date);
    await searchAgendaEntries();
  } catch (error) {
    scheduleEditMessage.textContent = error.message;
  } finally {
    submit.disabled = false;
  }
});
