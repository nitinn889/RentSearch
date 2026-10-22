"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const state = {
  app: {}, settings: null, sources: [], results: null, buildings: [], alertLog: [],
  view: "all", filter: "all", sort: "posted", scanning: false, lastScanId: null, picking: null,
};

async function api(path, { method = "GET", body } = {}) {
  const res = await fetch(path, {
    method,
    headers: method === "GET" ? {} : { "Content-Type": "application/json" },
    body: method === "GET" ? undefined : JSON.stringify(body ?? {}),
  });
  if (res.status === 401) {
    location.href = "/login";
    throw new Error("Please sign in");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

let toastTimer;
function toast(message, ms = 4500) {
  const el = $("#toast");
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, ms);
}

// ------------------------------------------------------------------ formatting

const inr = (n) => Math.round(n).toLocaleString("en-IN");

function priceText(l) {
  if (!l.price) return "Price on request";
  if (l.deal === "rent") return `₹${inr(l.price)}/mo`;
  if (l.price >= 1e7) return `₹${+(l.price / 1e7).toFixed(2)} Cr`;
  if (l.price >= 1e5) return `₹${+(l.price / 1e5).toFixed(1)} L`;
  return `₹${inr(l.price)}`;
}

const bhkText = (b) => (!b ? "" : b === 0.5 ? "1 RK" : `${b} BHK`);

function furnishing(value) {
  const t = String(value).toLowerCase();
  if (t.startsWith("semi")) return "Semi-furnished";
  if (t.startsWith("un") || t.startsWith("no")) return "Unfurnished";
  if (t.startsWith("full") || t === "furnished") return "Furnished";
  return value;
}

function parseTime(iso) {
  if (!iso) return NaN;
  return Date.parse(/Z$|[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`);
}

function ago(iso) {
  const t = parseTime(iso);
  if (Number.isNaN(t)) return "";
  const s = (Date.now() - t) / 1000;
  if (s < 3600) return s < 60 ? "just now" : `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  if (s < 86400 * 30) return `${Math.round(s / 86400)} day${s < 86400 * 1.5 ? "" : "s"} ago`;
  return new Date(t).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
}

const clock = (t) => new Date(typeof t === "number" ? t : parseTime(t)).toLocaleString("en-IN",
  { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" });

const sourceName = (key) => state.sources.find((s) => s.key === key)?.name || key;
const buildingName = (id) => state.buildings.find((b) => b.id === id)?.name || "a building you removed";
const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const dealText = (d) => ({ rent: "Rent", sale: "Sale", both: "Rent or sale" }[d]);

// ------------------------------------------------------------------ settings

let savedTimer;
async function save(patch) {
  try {
    state.settings = await api("/api/settings", { method: "POST", body: patch });
    const el = $("#save-state");
    el.textContent = "✓ Saved";
    clearTimeout(savedTimer);
    savedTimer = setTimeout(() => { el.textContent = ""; }, 1500);
    return true;
  } catch (e) {
    toast(`Couldn't save: ${e.message}`);
    return false;
  }
}

const ALERT_FIELDS = {
  "#al-sound": "sound", "#al-browser": "browser", "#al-windows": "windows_toast",
  "#al-ntfy-topic": "ntfy_topic", "#al-tg-token": "telegram_token", "#al-tg-chat": "telegram_chat_id",
  "#al-email-to": "email_to", "#al-smtp-user": "smtp_user", "#al-smtp-pass": "smtp_password",
  "#al-smtp-host": "smtp_host", "#al-smtp-port": "smtp_port",
};
const SCHEDULE_CHOICES = [[0, "Off"], [5, "every 5 min"], [10, "every 10 min"], [15, "every 15 min"],
  [30, "every 30 min"], [60, "every hour"], [120, "every 2 hours"], [360, "every 6 hours"]];

function fillForm() {
  const s = state.settings;
  $("#radius").value = s.area?.radius_km ?? 2;
  $("#radius-value").textContent = `${$("#radius").value} km`;
  $("#max-rent").value = s.max_rent ?? "";
  $("#max-price").value = s.max_price ?? "";
  $("#min-bedrooms").value = s.min_bedrooms ?? "";
  $("#search-input").value = s.area?.label || "";
  $("#schedule").innerHTML = SCHEDULE_CHOICES
    .filter(([m]) => !m || m >= state.app.min_schedule)
    .map(([m, label]) => `<option value="${m}">${label}</option>`).join("");
  $("#schedule").value = String(s.schedule_min || 0);
  for (const [sel, key] of Object.entries(ALERT_FIELDS)) {
    const el = $(sel);
    if (el.type === "checkbox") el.checked = !!s.alerts[key];
    else el.value = s.alerts[key] ?? "";
  }
  $("#al-windows-row").hidden = !state.app.windows_toast;
  $("#test-windows").hidden = !state.app.windows_toast;
  renderDeal();
  renderPriceHint();
  renderSourceToggles();
  renderCustomPages();
  renderAreaSummary();
  renderScheduleNote();
}

function renderDeal() {
  const deal = state.settings.deal;
  $$("#deal button").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.value === deal)));
  $("#max-rent-field").hidden = deal === "sale";
  $("#max-price-field").hidden = deal === "rent";
}

function renderPriceHint() {
  const v = Number($("#max-price").value);
  $("#max-price-hint").textContent = v ? `= ${priceText({ price: v, deal: "sale" })}` : "";
}

function renderAreaSummary() {
  const s = state.settings;
  const a = s.area;
  const what = { rent: "for rent", sale: "for sale", both: "for rent or sale" }[s.deal];
  const parts = [];
  if (a) parts.push(`${a.place || a.label} · within ${a.radius_km} km · homes ${what}`);
  if (state.buildings.length) parts.push(`${state.buildings.length} building${state.buildings.length === 1 ? "" : "s"}`);
  $("#area-summary").textContent = parts.join(" · ") || "Nothing watched yet: pick an area or a building";
  $("#clear-area").hidden = !a;
}

function renderScheduleNote() {
  const minutes = Number(state.settings.schedule_min || 0);
  const where = state.app.server
    ? "on the server, even when this page is closed"
    : "while Rent Radar is running on this PC (keep its black window open)";
  let text = minutes
    ? `Auto-check is on: every ${minutes >= 60 ? `${minutes / 60} h` : `${minutes} min`}, ${where}.`
    : "Auto-check is off: sites are only checked when you press Refresh. Turn it on in the top bar.";
  const last = state.results?.summary?.finished;
  if (minutes && last) text += ` Next check around ${clock(parseTime(last) + minutes * 60000)}.`;
  text += " Phone push, Telegram or email reach you when you're away from this page.";
  $("#schedule-note").textContent = text;
}

function renderSourceToggles() {
  $("#source-toggles").innerHTML = state.sources.map((src) => `
    <label><input type="checkbox" data-source="${esc(src.key)}" ${state.settings.sources[src.key] !== false ? "checked" : ""}>
      <span>${esc(src.name)}${src.site ? ` <a class="muted small" href="${esc(src.site)}" target="_blank" rel="noopener">${esc(new URL(src.site).host)}</a>` : ""}</span>
    </label>`).join("");
}

function renderCustomPages() {
  $("#custom-pages").innerHTML = state.settings.custom_pages.map((p, i) => `
    <div class="custom-page" data-i="${i}">
      <input value="${esc(p.url)}" data-field="url" placeholder="https://…">
      <select data-field="deal">
        <option value="rent" ${p.deal === "rent" ? "selected" : ""}>Rent</option>
        <option value="sale" ${p.deal === "sale" ? "selected" : ""}>Sale</option>
      </select>
      <button class="icon-btn" data-remove="${i}" title="Remove" type="button">✕</button>
    </div>`).join("");
}

function readCustomPages() {
  return $$("#custom-pages .custom-page").map((row) => ({
    url: $("[data-field=url]", row).value.trim(),
    deal: $("[data-field=deal]", row).value,
  })).filter((p) => p.url);
}

function bindSettings() {
  $("#deal").addEventListener("click", async (e) => {
    const b = e.target.closest("button[data-value]");
    if (!b) return;
    await save({ deal: b.dataset.value });
    renderDeal();
    renderAreaSummary();
  });
  $("#max-rent").addEventListener("change", (e) => save({ max_rent: e.target.value || null }));
  $("#max-price").addEventListener("input", renderPriceHint);
  $("#max-price").addEventListener("change", (e) => save({ max_price: e.target.value || null }));
  $("#min-bedrooms").addEventListener("change", (e) => save({ min_bedrooms: e.target.value || null }));

  $("#radius").addEventListener("input", (e) => {
    $("#radius-value").textContent = `${e.target.value} km`;
    if (areaCircle) areaCircle.setRadius(Number(e.target.value) * 1000);
  });
  $("#radius").addEventListener("change", async (e) => {
    const a = state.settings.area;
    if (!a) return;
    await save({ area: { ...a, radius_km: Number(e.target.value) } });
    drawArea();
    renderAreaSummary();
    renderResults();
  });
  $("#clear-area").addEventListener("click", async () => {
    if (!confirm("Stop watching this area? Your building watches stay.")) return;
    await save({ area: null });
    $("#search-input").value = "";
    drawArea();
    renderAreaSummary();
    renderResults();
  });

  $("#source-toggles").addEventListener("change", (e) => {
    const key = e.target.dataset.source;
    if (key) save({ sources: { [key]: e.target.checked } });
  });
  $("#add-page").addEventListener("click", () => {
    state.settings.custom_pages = [...readCustomPages(), { url: "", deal: state.settings.deal === "sale" ? "sale" : "rent" }];
    renderCustomPages();
    $("#custom-pages .custom-page:last-child input").focus();
  });
  $("#custom-pages").addEventListener("change", () => save({ custom_pages: readCustomPages() }));
  $("#custom-pages").addEventListener("click", async (e) => {
    const i = e.target.dataset.remove;
    if (i === undefined) return;
    await save({ custom_pages: readCustomPages().filter((_, j) => j !== Number(i)) });
    renderCustomPages();
  });

  for (const [sel, key] of Object.entries(ALERT_FIELDS)) {
    $(sel).addEventListener("change", (e) => {
      const el = e.target;
      save({ alerts: { [key]: el.type === "checkbox" ? el.checked : el.value } });
      if (key === "browser" && el.checked) askNotificationPermission();
    });
  }
  $("#gen-topic").addEventListener("click", () => {
    const rand = Array.from(crypto.getRandomValues(new Uint8Array(10)),
      (b) => "abcdefghijkmnpqrstuvwxyz23456789"[b % 32]).join("");
    $("#al-ntfy-topic").value = `rent-radar-${rand}`;
    save({ alerts: { ntfy_topic: $("#al-ntfy-topic").value } });
  });
  $$("[data-test]").forEach((b) => b.addEventListener("click", async () => {
    b.disabled = true;
    try {
      await api("/api/alerts/test", { method: "POST", body: { channel: b.dataset.test } });
      toast("Test alert sent ✓");
    } catch (e) {
      toast(`Test failed: ${e.message}`, 8000);
    } finally {
      b.disabled = false;
    }
  }));
  $("[data-test-local]").addEventListener("click", async () => {
    unlockAudio();
    playChime();
    await askNotificationPermission();
    if (!browserNotify("Rent Radar test alert", "Browser notifications are working.")) {
      toast("The sound played. Browser notifications are blocked: allow them from the address-bar icon.");
    }
  });

  $("#schedule").addEventListener("change", async (e) => {
    await save({ schedule_min: Number(e.target.value) });
    $("#schedule").value = String(state.settings.schedule_min || 0);
    renderScheduleNote();
    if (state.settings.schedule_min) toast("Auto-check is on. Set up phone push under “How to alert you” to get alerts anywhere.", 6000);
  });
  $("#logout").addEventListener("click", async () => {
    await api("/api/auth/logout", { method: "POST" }).catch(() => {});
    location.href = "/login";
  });
}

// ------------------------------------------------------------------ map

let map, areaMarker, areaCircle, listingLayer, buildingLayer;
const markers = new Map();

function initMap() {
  map = L.map("map").setView([22.5, 79], 5);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  }).addTo(map);
  listingLayer = L.layerGroup().addTo(map);
  buildingLayer = L.layerGroup().addTo(map);
  map.on("click", (e) => {
    if (state.picking) placeBuilding(e.latlng.lat, e.latlng.lng);
    else setCenter(e.latlng.lat, e.latlng.lng);
  });
  drawArea();
  drawBuildings();
  fitMap();
}

function drawArea() {
  if (!map) return;
  const a = state.settings.area;
  if (!a) {
    areaMarker?.remove();
    areaCircle?.remove();
    areaMarker = areaCircle = null;
    return;
  }
  const ll = [a.lat, a.lon];
  if (!areaMarker) {
    areaMarker = L.marker(ll, { draggable: true, title: "Search centre: drag to move" }).addTo(map);
    areaMarker.on("dragend", () => {
      const p = areaMarker.getLatLng();
      setCenter(p.lat, p.lng);
    });
    areaCircle = L.circle(ll, { radius: a.radius_km * 1000, color: cssVar("--accent"), weight: 2, fillOpacity: 0.06 }).addTo(map);
  } else {
    areaMarker.setLatLng(ll);
    areaCircle.setLatLng(ll).setRadius(a.radius_km * 1000);
  }
}

function drawBuildings() {
  if (!buildingLayer) return;
  buildingLayer.clearLayers();
  const icon = L.divIcon({ className: "bldg-pin", html: "🏢", iconSize: [28, 28], iconAnchor: [14, 14] });
  for (const b of state.buildings) {
    L.circle([b.lat, b.lon], { radius: b.radius_km * 1000, color: cssVar("--new"), weight: 1.5, dashArray: "4 4", fillOpacity: 0.04 })
      .addTo(buildingLayer);
    L.marker([b.lat, b.lon], { icon, title: b.name }).bindPopup(`<b>${esc(b.name)}</b><br>${esc(buildingStatus(b).text)}`)
      .addTo(buildingLayer);
  }
}

function fitMap() {
  if (!map) return;
  const layers = [areaCircle, ...buildingLayer.getLayers().filter((l) => l instanceof L.Circle)].filter(Boolean);
  if (!layers.length) return;
  map.fitBounds(L.featureGroup(layers).getBounds(), { padding: [20, 20], maxZoom: 15 });
}

async function setCenter(lat, lon, place) {
  if (!place) {
    try {
      place = await api(`/api/reverse?lat=${lat}&lon=${lon}`);
    } catch (e) {
      place = { label: `${lat.toFixed(5)}, ${lon.toFixed(5)}`, place: "", city: "" };
      toast(e.message);
    }
  }
  const ok = await save({ area: { lat, lon, radius_km: Number($("#radius").value), label: place.label,
    place: place.locality || place.place, city: place.city } });
  if (!ok) return;
  $("#search-input").value = place.label;
  $("#panel-where").classList.remove("attention");
  drawArea();
  fitMap();
  renderAreaSummary();
  renderResults();
}

function bindLocation() {
  const list = $("#search-results");
  $("#search-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const q = $("#search-input").value.trim();
    if (!q) return;
    list.hidden = false;
    list.innerHTML = '<li class="muted">Searching…</li>';
    try {
      const places = await api(`/api/geocode?q=${encodeURIComponent(q)}`);
      list.innerHTML = places.length
        ? places.map((p, i) => `<li tabindex="0" data-i="${i}">${esc(p.label)}</li>`).join("")
        : '<li class="muted">Nothing found. Try adding the city name, or click the map.</li>';
      list.onclick = list.onkeydown = (ev) => {
        if (ev.type === "keydown" && ev.key !== "Enter") return;
        const li = ev.target.closest("li[data-i]");
        if (!li) return;
        list.hidden = true;
        const p = places[li.dataset.i];
        setCenter(p.lat, p.lon, p);
      };
    } catch (err) {
      list.innerHTML = `<li class="muted">${esc(err.message)}</li>`;
    }
  });
  $("#use-location").addEventListener("click", () => {
    if (!navigator.geolocation) return toast("This browser can't share its location.");
    toast("Finding you…");
    navigator.geolocation.getCurrentPosition(
      (pos) => setCenter(pos.coords.latitude, pos.coords.longitude),
      (err) => toast(`Couldn't get your location: ${err.message}`),
      { enableHighAccuracy: true, timeout: 15000 });
  });
}

// ------------------------------------------------------------------ buildings

function buildingStatus(b) {
  const s = b.status || {};
  if (s.error && !s.checked_at) return { cls: "err", text: s.error };
  if (!s.checked_at) return { cls: "", text: "Not checked yet" };
  const when = `checked ${ago(s.checked_at)}`;
  if (s.error) return { cls: "err", text: `${s.error} (last good ${when})` };
  return s.available
    ? { cls: "ok", text: `${s.available} flat${s.available === 1 ? "" : "s"} listed now · ${when}` }
    : { cls: "", text: `Nothing listed right now · ${when}` };
}

function shortPlace(b) {
  // "Mantri Classic, Kormangala East, Bengaluru South City Corporation, …" -> "Kormangala East, Bengaluru"
  const parts = (b.label || "").split(",").map((p) => p.trim()).filter((p) => p && p !== b.name && !/corporation|india|^\d+$/i.test(p));
  return parts.slice(0, 2).join(", ") || b.place || "the pin";
}

function renderBuildings() {
  const list = $("#building-list");
  list.innerHTML = state.buildings.map((b) => {
    const st = buildingStatus(b);
    return `
      <li class="building" data-id="${esc(b.id)}">
        <div class="b-main">
          <b>🏢 ${esc(b.name)}</b>
          <span class="muted tiny">${esc(dealText(b.deal))} · within ${b.radius_km} km of ${esc(shortPlace(b))}</span>
          <span class="b-status ${st.cls}">${esc(st.text)}</span>
        </div>
        <div class="b-actions">
          ${b.status?.available ? `<button class="link-btn small" data-show="${esc(b.id)}" type="button">Show</button>` : ""}
          <button class="icon-btn" data-remove-building="${esc(b.id)}" title="Stop watching" type="button">✕</button>
        </div>
      </li>`;
  }).join("");
  const full = state.buildings.length >= state.app.max_buildings;
  $("#building-form").hidden = full;
  if (full) list.insertAdjacentHTML("beforeend", `<li class="muted tiny">That's the maximum of ${state.app.max_buildings} buildings.</li>`);
}

async function addBuilding(draft, place, radiusKm) {
  try {
    const b = await api("/api/buildings", { method: "POST", body: {
      name: draft.name, deal: draft.deal, lat: place.lat, lon: place.lon, radius_km: radiusKm,
      label: place.label, place: place.locality || place.place, city: place.city,
    } });
    state.buildings.push(b);
    $("#building-form").reset();
    $("#b-results").hidden = true;
    renderBuildings();
    drawBuildings();
    fitMap();
    renderAreaSummary();
    toast(`Watching ${b.name}. Checking it now…`);
    refresh();
  } catch (e) {
    toast(`Couldn't add the building: ${e.message}`, 7000);
  }
}

async function placeBuilding(lat, lon) {
  const draft = state.picking;
  state.picking = null;
  $("#b-pick-hint").hidden = true;
  $("#map").classList.remove("picking");
  let place;
  try {
    place = await api(`/api/reverse?lat=${lat}&lon=${lon}`);
  } catch (e) {
    return toast(e.message);
  }
  addBuilding(draft, place, 1);
}

function bindBuildings() {
  const results = $("#b-results");
  $("#building-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const draft = { name: $("#b-name").value.trim(), deal: $("#b-deal").value };
    const where = $("#b-where").value.trim();
    if (draft.name.length < 3) return toast("Enter the building or society name");
    results.hidden = false;
    results.innerHTML = '<li class="muted">Looking for it on the map…</li>';
    try {
      // The building itself, if OpenStreetMap knows it; otherwise the area it is in (a wider circle).
      const exact = await api(`/api/geocode?q=${encodeURIComponent(where ? `${draft.name}, ${where}` : draft.name)}`);
      const areas = where && exact.length < 2 ? await api(`/api/geocode?q=${encodeURIComponent(where)}`) : [];
      const options = [...exact.map((p) => ({ p, radius: 1, kind: "" })),
        ...areas.map((p) => ({ p, radius: 2, kind: "Area: " }))];
      results.innerHTML = options.map((o, i) => `<li tabindex="0" data-i="${i}">${esc(o.kind)}${esc(o.p.label)}</li>`).join("") +
        '<li tabindex="0" data-pick>📍 Not listed? Click its spot on the map instead</li>';
      results.onclick = results.onkeydown = (ev) => {
        if (ev.type === "keydown" && ev.key !== "Enter") return;
        const li = ev.target.closest("li");
        if (!li) return;
        results.hidden = true;
        if (li.dataset.pick !== undefined) {
          state.picking = draft;
          $("#b-pick-hint").hidden = false;
          $("#map").classList.add("picking");
          $("#map").scrollIntoView({ behavior: "smooth", block: "center" });
        } else if (li.dataset.i !== undefined) {
          const o = options[li.dataset.i];
          addBuilding(draft, o.p, o.radius);
        }
      };
    } catch (err) {
      results.innerHTML = `<li class="muted">${esc(err.message)}</li>`;
    }
  });
  $("#b-pick-cancel").addEventListener("click", () => {
    state.picking = null;
    $("#b-pick-hint").hidden = true;
    $("#map").classList.remove("picking");
  });
  $("#building-list").addEventListener("click", async (e) => {
    const remove = e.target.dataset.removeBuilding;
    const show = e.target.dataset.show;
    if (remove) {
      const b = state.buildings.find((x) => x.id === remove);
      if (!confirm(`Stop watching ${b?.name}?`)) return;
      await api(`/api/buildings/${remove}`, { method: "DELETE" });
      state.buildings = state.buildings.filter((x) => x.id !== remove);
      if (state.filter === `bldg:${remove}`) state.filter = "all";
      renderBuildings();
      drawBuildings();
      renderAreaSummary();
      renderResults();
    } else if (show) {
      state.filter = `bldg:${show}`;
      state.view = "all";
      renderResults();
      $(".results").scrollIntoView({ behavior: "smooth" });
    }
  });
}

// ------------------------------------------------------------------ alerts in the page

let audioCtx;
function unlockAudio() {
  try {
    audioCtx = audioCtx || new AudioContext();
    if (audioCtx.state === "suspended") audioCtx.resume();
  } catch { /* no audio available */ }
}

function playChime() {
  if (!audioCtx) return;
  const t0 = audioCtx.currentTime + 0.05;
  [[0, 660], [0.16, 880], [0.32, 1320], [0.9, 660], [1.06, 880], [1.22, 1320]].forEach(([dt, freq]) => {
    const osc = audioCtx.createOscillator();
    const gain = audioCtx.createGain();
    osc.type = "triangle";
    osc.frequency.value = freq;
    gain.gain.setValueAtTime(0.0001, t0 + dt);
    gain.gain.exponentialRampToValueAtTime(0.35, t0 + dt + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, t0 + dt + 0.5);
    osc.connect(gain).connect(audioCtx.destination);
    osc.start(t0 + dt);
    osc.stop(t0 + dt + 0.55);
  });
}

async function askNotificationPermission() {
  if ("Notification" in window && Notification.permission === "default" && state.settings.alerts.browser) {
    await Notification.requestPermission();
  }
}

function browserNotify(title, body) {
  if (!("Notification" in window) || Notification.permission !== "granted") return false;
  const n = new Notification(title, { body, tag: "rent-radar", requireInteraction: true, icon: "/static/icon-192.png" });
  n.onclick = () => { window.focus(); n.close(); showNew(); };
  return true;
}

let titleTimer;
function flashTitle(text) {
  clearInterval(titleTimer);
  let on = false;
  titleTimer = setInterval(() => { document.title = (on = !on) ? `🔔 ${text}` : "Rent Radar"; }, 900);
  const stop = () => { clearInterval(titleTimer); document.title = "Rent Radar"; };
  if (document.hasFocus()) setTimeout(stop, 12000);
  window.addEventListener("focus", stop, { once: true });
}

function announce(alert) {
  $("#alert-text").textContent = alert.title;
  $("#alert-banner").hidden = false;
  const prefs = state.settings.alerts;
  if (prefs.sound) playChime();
  if (prefs.browser) browserNotify(alert.title, alert.groups.map((g) => g.heading).join("\n"));
  flashTitle(alert.title);
  navigator.vibrate?.([200, 100, 200]);
}

function showNew() {
  state.view = "new";
  state.filter = "all";
  renderResults();
  $(".results").scrollIntoView({ behavior: "smooth" });
}

// ------------------------------------------------------------------ scanning and updates

const STATUS_ICON = { queued: "•", running: "⟳", ok: "✓", blocked: "⛔", disallowed: "🚫", error: "⚠", skipped: "–", off: "–" };

function renderProgress(scan) {
  $("#progress").hidden = false;
  $("#progress-stage").textContent = (scan.trigger === "schedule" ? "Automatic check: " : "") +
    (scan.state === "running" ? `${scan.stage}…` : scan.stage);
  $("#progress-sources").innerHTML = Object.values(scan.sources).filter((s) => s.status !== "off").map((s) => `
    <li>
      <span class="status-icon st-${s.status}">${STATUS_ICON[s.status] || "•"}</span>
      <b>${esc(s.name)}</b>
      <span class="msg muted">${esc(s.status === "running" ? `${s.message}…` : s.message || s.status)}</span>
      <span>${s.found ? `${s.found} found` : ""}</span>
    </li>`).join("");
}

function setScanning(on) {
  state.scanning = on;
  const b = $("#refresh");
  b.disabled = on;
  b.classList.toggle("spinning", on);
  $("span", b).textContent = on ? "Checking…" : "Refresh";
  if (!on) setTimeout(() => { if (!state.scanning) $("#progress").hidden = true; }, 2500);
}

async function watchScan(scan) {
  setScanning(true);
  try {
    renderProgress(scan);
    while (scan.state === "running") {
      await sleep(1000);
      scan = await api(`/api/scan/${scan.id}`);
      renderProgress(scan);
    }
    if (scan.state === "failed") throw new Error(scan.error);
    applyUpdates(await api("/api/updates"), { announceNew: true });
  } catch (e) {
    toast(`Check failed: ${e.message}`, 9000);
  } finally {
    setScanning(false);
  }
}

async function refresh() {
  if (state.scanning) return;
  if (!state.settings.area && !state.buildings.length) {
    $("#panel-where").classList.add("attention");
    $("#search-input").focus();
    toast("Pick an area, or add a building to watch, first.");
    return;
  }
  unlockAudio();
  await askNotificationPermission();
  try {
    await watchScan(await api("/api/scan", { method: "POST" }));
  } catch (e) {
    toast(e.message, 7000);
  }
}

function applyUpdates(bundle, { announceNew = false } = {}) {
  const scanId = bundle.results?.scan_id || null;
  const isNew = scanId && scanId !== state.lastScanId;
  state.results = bundle.results;
  state.buildings = bundle.buildings;
  state.alertLog = bundle.alert_log;
  state.lastScanId = scanId;
  const alert = bundle.results?.summary?.alert;
  if (announceNew && isNew && alert) {
    state.view = "new";
    state.filter = "all";
    announce(alert);
  }
  renderBuildings();
  drawBuildings();
  renderAreaSummary();
  renderScheduleNote();
  renderAlertLog();
  renderResults();
}

async function poll() {
  if (state.scanning) return;
  try {
    const bundle = await api("/api/updates");
    if (bundle.running_scan) {
      watchScan(await api(`/api/scan/${bundle.running_scan}`));  // an automatic check is under way
    } else if ((bundle.results?.scan_id || null) !== state.lastScanId) {
      applyUpdates(bundle, { announceNew: true });
    }
  } catch { /* offline for a moment; try again next time */ }
}

// ------------------------------------------------------------------ results

const listingKey = (l) => `${l.source}|${l.source_id}`;

function sorted(listings) {
  const by = {
    posted: (a, b) => (b.unread - a.unread) ||
      ((parseTime(b.posted_at || b.first_seen) || 0) - (parseTime(a.posted_at || a.first_seen) || 0)),
    distance: (a, b) => (a.distance_km ?? 999) - (b.distance_km ?? 999),
    price: (a, b) => (a.price || Infinity) - (b.price || Infinity),
  }[state.sort];
  return [...listings].sort(by);
}

function sameArea(a, b) {
  return a && b && Math.abs(a.lat - b.lat) < 1e-4 && Math.abs(a.lon - b.lon) < 1e-4 && a.radius_km === b.radius_km;
}

function renderSummary(r) {
  const s = r.summary;
  const parts = [];
  if (s.area) {
    parts.push(`${s.in_area} home${s.in_area === 1 ? "" : "s"} within ${s.area.radius_km} km of ${s.area.place || s.area.label}`);
    if (s.filtered_out) parts.push(`${s.filtered_out} hidden by your budget/bedroom filters`);
  }
  const nb = Object.keys(s.buildings || {}).length;
  if (nb) parts.push(`${nb} building${nb === 1 ? "" : "s"} checked`);
  parts.push(`${s.trigger === "schedule" ? "automatic check" : "checked"} ${clock(s.finished)}`);
  const stale = (state.settings.area || s.area) && !sameArea(s.area, state.settings.area);
  $("#results-summary").innerHTML = (stale ? "<b>Your area changed since this check. Press Refresh to check the new one.</b> " : "") +
    esc(parts.join(" · "));

  $("#source-chips").innerHTML = Object.entries(s.sources)
    .filter(([key, x]) => x.status !== "off" && !(key === "custom" && x.status === "skipped"))
    .map(([, x]) => {
      const tip = [x.message, ...(x.notes || [])].filter(Boolean).join("\n");
      let label = `${x.name} · ${x.shown}`;
      if (x.status === "skipped") label = `${x.name}: not available here`;
      else if (x.status !== "ok") label = `${x.name}: ${x.status}`;
      else if (x.problems?.length) label += ` · ${[...new Set(x.problems.map((p) => `${p.deal}: ${p.status}`))].join(", ")}`;
      const bad = x.status !== "skipped" && (x.status !== "ok" || x.problems?.length);
      return `<span class="chip ${bad ? "bad" : ""}" title="${esc(tip)}">${esc(label)}</span>`;
    }).join("");

  const note = $("#baseline-note");
  if (s.baseline?.length) {
    const feeds = s.baseline.map((f) => (f.startsWith("page:")
      ? new URL(f.slice(5)).host
      : `${sourceName(f.split(":")[0])} (${f.split(":")[1]})`));
    note.hidden = false;
    note.innerHTML = `<b>First check of ${esc(feeds.join(", "))} for this area.</b> What's listed now is saved as the ` +
      "starting point, so it doesn't trigger alerts. From now on you're alerted only about listings that weren't there before.";
  } else {
    note.hidden = true;
  }
}

function card(l) {
  const meta = [bhkText(l.bedrooms), l.area_sqft && `${inr(l.area_sqft)} sq ft`,
    l.distance_km != null && `${l.distance_km.toFixed(1)} km away`, l.locality].filter(Boolean);
  const d = l.details || {};
  const extra = [l.building, d.Furnishing && furnishing(d.Furnishing), d["Posted by"] && `by ${d["Posted by"]}`,
    d.Deposit && `Deposit ${d.Deposit}`].filter(Boolean);
  const posted = l.posted_at ? `Posted ${ago(l.posted_at)}` : `Seen ${ago(l.first_seen)}`;
  const watched = (l.tags || []).filter((t) => t.startsWith("bldg:"))
    .map((t) => `<span class="watch-tag">🏢 ${esc(buildingName(t.slice(5)))}</span>`).join("");
  return `
    <article class="card ${l.unread ? "is-new" : ""}" data-key="${esc(listingKey(l))}">
      <div class="card-img">🏠${l.image ? `<img loading="lazy" referrerpolicy="no-referrer" src="${esc(l.image)}" alt="">` : ""}
        ${l.unread ? '<span class="badge-new">NEW</span>' : ""}
        <span class="badge-deal">${l.deal === "rent" ? "For rent" : "For sale"}</span>
      </div>
      <div class="card-body">
        ${watched ? `<div class="watch-tags">${watched}</div>` : ""}
        <div class="price">${esc(priceText(l))}</div>
        <div class="title">${esc(l.title)}</div>
        <div class="meta">${esc(meta.join(" · "))}</div>
        ${extra.length ? `<div class="meta">${esc(extra.join(" · "))}</div>` : ""}
        <div class="card-foot">
          <span class="src">${esc(sourceName(l.source))}</span>
          <span class="muted">${esc(posted)}</span>
          <a href="${esc(l.url)}" target="_blank" rel="noopener">Open ↗</a>
        </div>
      </div>
    </article>`;
}

function renderMarkers(listings) {
  if (!listingLayer) return;
  listingLayer.clearLayers();
  markers.clear();
  const newColor = cssVar("--new");
  const oldColor = cssVar("--accent");
  for (const l of listings) {
    if (l.lat == null || l.lon == null) continue;
    const m = L.circleMarker([l.lat, l.lon], {
      radius: l.unread ? 8 : 6, color: "#fff", weight: 1.5, fillOpacity: 0.95, fillColor: l.unread ? newColor : oldColor,
    }).bindPopup(`<b>${esc(priceText(l))}</b><br>${esc(l.title)}<br><a href="${esc(l.url)}" target="_blank" rel="noopener">Open on ${esc(sourceName(l.source))} ↗</a>`);
    m.on("click", () => focusCard(listingKey(l)));
    m.addTo(listingLayer);
    markers.set(listingKey(l), m);
  }
}

function focusCard(key) {
  $$(".card.focus").forEach((c) => c.classList.remove("focus"));
  const el = $(`.card[data-key="${CSS.escape(key)}"]`);
  if (el) {
    el.classList.add("focus");
    el.scrollIntoView({ behavior: "smooth", block: "center" });
  }
}

function renderWatchFilter(pool) {
  const count = (tag) => pool.filter((l) => (l.tags || []).includes(tag)).length;
  const chips = [["all", `Everything · ${pool.length}`]];
  if (state.settings.area || pool.some((l) => l.tags?.includes("area"))) chips.push(["area", `Area · ${count("area")}`]);
  for (const b of state.buildings) chips.push([`bldg:${b.id}`, `🏢 ${b.name} · ${count(`bldg:${b.id}`)}`]);
  $("#watch-filter").innerHTML = chips.length > 2 || state.buildings.length
    ? chips.map(([v, label]) => `<button class="chip filter ${state.filter === v ? "on" : ""}" data-filter="${esc(v)}" type="button">${esc(label)}</button>`).join("")
    : "";
}

function renderResults() {
  const r = state.results;
  const all = r?.listings || [];
  const fresh = all.filter((l) => l.unread);
  $("#count-new").textContent = fresh.length;
  $("#count-new").classList.toggle("has", fresh.length > 0);
  $("#count-all").textContent = all.length;
  $$(".tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.view === state.view)));
  $("#mark-read").hidden = !fresh.length;

  const cards = $("#cards");
  if (!r) {
    $("#results-summary").textContent = "";
    $("#source-chips").innerHTML = "";
    $("#watch-filter").innerHTML = "";
    $("#baseline-note").hidden = true;
    cards.innerHTML = state.settings.area || state.buildings.length
      ? '<div class="empty"><b>Ready when you are</b>Press <b>Refresh</b> to check the sites now, or turn on Auto-check.</div>'
      : '<div class="empty"><b>What should we watch?</b>Search for an area on the left (or click the map) and choose a radius, or add a building you want to live in. Then press Refresh.</div>';
    listingLayer?.clearLayers();
    return;
  }
  renderSummary(r);
  const pool = state.view === "new" ? fresh : all;
  renderWatchFilter(pool);
  const shown = sorted(state.filter === "all" ? pool : pool.filter((l) => (l.tags || []).includes(state.filter)));
  if (!shown.length) {
    cards.innerHTML = state.view === "new" && all.length
      ? '<div class="empty"><b>Nothing new since you last looked</b>Leave Auto-check on and you\'ll be alerted as soon as something comes up.</div>'
      : state.filter.startsWith("bldg:")
        ? '<div class="empty"><b>No flats listed in this building right now</b>You\'ll get an alert the moment one is.</div>'
        : '<div class="empty"><b>No homes found here</b>Try a bigger radius or relax your budget and bedroom filters.</div>';
  } else {
    cards.innerHTML = shown.map(card).join("");
    $$("img", cards).forEach((img) => img.addEventListener("error", () => img.remove()));
  }
  renderMarkers(shown);
}

function renderAlertLog() {
  $("#history-count").textContent = state.alertLog.length;
  $("#alert-log").innerHTML = state.alertLog.length
    ? state.alertLog.map((a) => {
      const sent = Object.entries(a.channels || {});
      const how = sent.length
        ? sent.map(([ch, res]) => `<span class="${res === "sent" ? "st-ok" : "st-error"}" title="${esc(res)}">${esc(ch)} ${res === "sent" ? "✓" : "✗"}</span>`).join(" ")
        : '<span class="muted">shown in the app only</span>';
      const links = (a.groups || []).flatMap((g) => g.listings).slice(0, 4)
        .map((l) => `<a href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.price)} · ${esc(l.title)}</a>`).join("");
      return `<li><b>${esc(a.title)}</b><span class="muted tiny">${esc(clock(a.sent_at))} · ${how}</span>${links}</li>`;
    }).join("")
    : '<li class="muted small">No alerts yet. They appear here when something new comes up.</li>';
}

function bindResults() {
  $(".tabs").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-view]");
    if (!b) return;
    state.view = b.dataset.view;
    renderResults();
  });
  $("#watch-filter").addEventListener("click", (e) => {
    const b = e.target.closest("[data-filter]");
    if (!b) return;
    state.filter = b.dataset.filter;
    renderResults();
  });
  $("#sort").addEventListener("change", (e) => { state.sort = e.target.value; renderResults(); });
  $("#mark-read").addEventListener("click", async () => {
    await api("/api/read", { method: "POST" });
    state.results?.listings.forEach((l) => { l.unread = false; });
    $("#alert-banner").hidden = true;
    state.view = "all";
    renderResults();
  });
  $("#cards").addEventListener("click", (e) => {
    if (e.target.closest("a")) return;
    const el = e.target.closest(".card");
    const m = el && markers.get(el.dataset.key);
    if (m) {
      map.setView(m.getLatLng(), Math.max(map.getZoom(), 15));
      m.openPopup();
    }
  });
  $("#alert-show").addEventListener("click", () => { $("#alert-banner").hidden = true; showNew(); });
  $("#alert-close").addEventListener("click", () => { $("#alert-banner").hidden = true; });
}

// ------------------------------------------------------------------ start

async function main() {
  let data;
  try {
    data = await api("/api/state");
  } catch (e) {
    document.body.innerHTML = `<p style="padding:40px">Rent Radar's server isn't reachable (${esc(e.message)}).</p>`;
    return;
  }
  Object.assign(state, {
    app: data.app, settings: data.settings, sources: data.sources, results: data.results,
    buildings: data.buildings, alertLog: data.alert_log, lastScanId: data.results?.scan_id || null,
  });
  state.view = state.results?.listings.some((l) => l.unread) ? "new" : "all";
  if (state.app.server) {
    $("#user-menu").hidden = false;
    $("#user-email").textContent = state.app.email;
  }
  $("#storage-warning").hidden = state.app.persistent;
  fillForm();
  bindSettings();
  bindLocation();
  bindBuildings();
  bindResults();
  renderBuildings();
  renderAlertLog();
  if (window.L) initMap();
  else $("#map").innerHTML = '<p class="empty">The map could not load (no internet?). Search still works.</p>';
  renderResults();
  $("#refresh").addEventListener("click", () => refresh());
  document.addEventListener("click", unlockAudio, { once: true });
  if (data.running_scan) watchScan(await api(`/api/scan/${data.running_scan}`));
  setInterval(poll, 30000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });
}

main();
