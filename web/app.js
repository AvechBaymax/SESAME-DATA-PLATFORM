/* OpenSesame front end: a small router and five pages.
 *
 *   /                          Tổng quan vùng trồng (region overview)
 *   /farm/:farmId              Chi tiết nông trại
 *   /farm/:farmId/block/:id    Chi tiết ô canh tác
 *   /product                   Sản phẩm & Lineage
 *   /qr/:sku                   Truy xuất nguồn gốc
 *
 * Mock farms come from data.js. The real farm comes from /api/monitor (Earth
 * Engine + sesame rules); province weather comes live from Open-Meteo.
 */
(function () {
  const D = window.OpenSesameData;
  const app = document.querySelector("#app");
  const state = {
    filters: { variety: "all", program: null, region: "all" },
    realFarm: null,
    weather: null,
    weatherError: false,
    farmWeather: {},
    maps: [],
  };

  // ---- helpers ---------------------------------------------------------------------------
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
  const num = (value, digits = 0) => (Number.isFinite(value)
    ? value.toLocaleString("vi-VN", { minimumFractionDigits: digits, maximumFractionDigits: digits })
    : "—");
  const pad = (n) => String(n).padStart(2, "0");
  const fmtDate = (date) => (date ? `${pad(date.getUTCDate())}/${pad(date.getUTCMonth() + 1)}/${date.getUTCFullYear()}` : "—");
  const addDays = (date, days) => new Date(date.getTime() + days * D.DAY);
  const sum = (list, fn) => list.reduce((total, item) => total + (fn(item) || 0), 0);
  const statusPill = (status, label) => `<span class="status-pill status-${status}">${esc(label || D.STATUS[status].name)}</span>`;
  const icon = {
    chevron: '<svg viewBox="0 0 24 24"><path d="m9 6 6 6-6 6"/></svg>',
    back: '<svg viewBox="0 0 24 24"><path d="M19 12H5m6-6-6 6 6 6"/></svg>',
    down: '<svg class="chev" viewBox="0 0 24 24"><path d="m6 9 6 6 6-6"/></svg>',
    trendDown: '<svg viewBox="0 0 24 24"><path d="m3 7 6 6 4-4 8 8m0 0v-6m0 6h-6"/></svg>',
    trendUp: '<svg viewBox="0 0 24 24"><path d="m3 17 6-6 4 4 8-8m0 0v6m0-6h-6"/></svg>',
    sun: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M2 12h2m16 0h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>',
    cloud: '<svg viewBox="0 0 24 24"><path d="M7 18a4 4 0 1 1 .7-7.9A5.5 5.5 0 0 1 18.5 12a3 3 0 0 1-.5 6H7Z"/></svg>',
    rain: '<svg viewBox="0 0 24 24"><path d="M7 15a4 4 0 1 1 .7-7.9A5.5 5.5 0 0 1 18.5 9a3 3 0 0 1-.5 6H7Z"/><path d="m9 18-1 3m5-3-1 3m5-3-1 3"/></svg>',
    drop: '<svg viewBox="0 0 24 24"><path d="M12 3.5S6 10 6 14a6 6 0 0 0 12 0c0-4-6-10.5-6-10.5Z"/></svg>',
    wind: '<svg viewBox="0 0 24 24"><path d="M3 8h11a3 3 0 1 0-3-3M3 16h15a3 3 0 1 1-3 3M3 12h8"/></svg>',
    thermo: '<svg viewBox="0 0 24 24"><path d="M14 14.8V5a2 2 0 1 0-4 0v9.8a4 4 0 1 0 4 0Z"/></svg>',
    signal: '<svg viewBox="0 0 24 24"><path d="M12 20v-6m-4 6v-3m8 3V9M4.9 4.9a10 10 0 0 0 0 14.2M19.1 4.9a10 10 0 0 1 0 14.2"/></svg>',
    satellite: '<svg viewBox="0 0 24 24"><path d="m13 7 4 4-6 6-4-4 6-6Zm-9 9 3 3m11-11 3-3M7 13l-3 3 4 4 3-3m6-6 3-3-4-4-3 3"/></svg>',
    map: '<svg viewBox="0 0 24 24"><path d="m3 7 6-3 6 3 6-3v13l-6 3-6-3-6 3V7Z"/><path d="M9 4v13m6-10v13"/></svg>',
    qr: '<svg viewBox="0 0 24 24"><path d="M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h2v2h-2zM18 14h2v2h-2zM14 18h2v2h-2zM18 18h2v2h-2z"/></svg>',
    leaf: '<svg viewBox="0 0 24 24"><path d="M5 19c0-8 5-13 14-14-1 9-6 14-14 14Zm0 0 7-7"/></svg>',
    factory: '<svg viewBox="0 0 24 24"><path d="M3 21V10l6 4V10l6 4V5h6v16H3Z"/><path d="M7 17h2m4 0h2"/></svg>',
    flask: '<svg viewBox="0 0 24 24"><path d="M9 3h6M10 3v6L4.5 19a1.5 1.5 0 0 0 1.3 2h12.4a1.5 1.5 0 0 0 1.3-2L14 9V3"/><path d="M7 15h10"/></svg>',
    box: '<svg viewBox="0 0 24 24"><path d="m12 3 8 4.5v9L12 21l-8-4.5v-9L12 3Z"/><path d="m4.3 7.7 7.7 4.4 7.7-4.4M12 12v9"/></svg>',
    check: '<svg viewBox="0 0 24 24"><path d="m5 12 5 5 9-10"/></svg>',
    bottle: '<svg viewBox="0 0 24 24"><path d="M10 2h4v3l2 3v12a2 2 0 0 1-2 2h-4a2 2 0 0 1-2-2V8l2-3V2Z"/><path d="M8 12h8"/></svg>',
    sparkle: '<svg viewBox="0 0 24 24"><path d="m12 3 1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8L12 3Z"/><path d="M19 17v4m-2-2h4"/></svg>',
    grain: '<svg viewBox="0 0 24 24"><path d="M12 21V8"/><path d="M12 8c-2.5 0-4-1.5-4-4 2.5 0 4 1.5 4 4Zm0 0c2.5 0 4-1.5 4-4-2.5 0-4 1.5-4 4Zm0 6c-2.5 0-4-1.5-4-4 2.5 0 4 1.5 4 4Zm0 0c2.5 0 4-1.5 4-4-2.5 0-4 1.5-4 4Z"/></svg>',
    shaker: '<svg viewBox="0 0 24 24"><path d="M8 10h8l-1 11H9L8 10Z"/><path d="M9 10V7a3 3 0 0 1 6 0v3"/><path d="M11 4.5h.01M13 4.5h.01M12 3h.01"/></svg>',
    jar: '<svg viewBox="0 0 24 24"><path d="M7 3h10v3H7z"/><path d="M6 6h12v13a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2V6Z"/><path d="M9 12h6"/></svg>',
    flame: '<svg viewBox="0 0 24 24"><path d="M12 3c1 3 5 5 5 10a5 5 0 0 1-10 0c0-3 2-4 2-7 1.5 1 3 2.5 3 4 0-3-1-5 0-7Z"/></svg>',
  };
  // Icons are decorative: the text next to them carries the meaning.
  Object.keys(icon).forEach((key) => { icon[key] = icon[key].replace("<svg ", '<svg aria-hidden="true" focusable="false" '); });

  const fmtTime = (date) => date.toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit" });
  const STATUS_DASH = { stable: null, watch: "8 5", risk: "2 5", nodata: "1 7" };

  // One polite status region: background loads are announced as a full sentence, focus stays put.
  function announce(message) {
    const region = document.querySelector("#live-status");
    if (region) region.textContent = message;
  }

  function dataNote() {
    const mock = D.FARMS.length;
    const real = state.realFarm;
    let realText = "ruộng thật đang tải từ Earth Engine";
    if (real?.monitor) {
      const latest = real.monitor.weather?.latest?.date;
      realText = `${real.code} là dữ liệu thật (Earth Engine${latest ? `, ERA5-Land đến ${latest.split("-").reverse().join("/")}` : ""})`;
    } else if (real?.error) {
      realText = `không kết nối được dữ liệu thật của ${real.code}`;
    }
    return `${mock} nông trại là <strong>dữ liệu mô phỏng</strong> để trình diễn; ${esc(realText)}.`;
  }

  let toastTimer;
  function toast(message) {
    const el = document.querySelector("#toast");
    el.textContent = message;
    el.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove("show"), 4500);
  }

  // ---- data ------------------------------------------------------------------------------
  function allFarms() {
    return state.realFarm ? [state.realFarm, ...D.FARMS] : D.FARMS;
  }

  function filteredFarms({ withRegion = false } = {}) {
    const { variety, program, region } = state.filters;
    return allFarms().filter((farm) => {
      if (variety !== "all" && farm.varietyId !== variety) return false;
      if (program && farm.programId !== program) return false;
      if (withRegion && region !== "all" && farm.provinceId !== region) return false;
      return true;
    });
  }

  const findFarm = (id) => allFarms().find((farm) => farm.id === id);

  // The real farm: a placeholder until /api/monitor answers.
  const RULE_TO_STATUS = { VIOLATION: "risk", WARN: "watch", OK: "stable" };
  function buildRealFarm(monitor, error) {
    const farm = monitor?.farm || { id: "CUCHI_02", latitude: 11.0, longitude: 106.4, area_m2: 5000 };
    const rules = monitor?.rules || null;
    const areaHa = (farm.area_m2 || 5000) / 10000;
    const sown = rules?.planting_date ? D.parseDate(rules.planting_date.split("-").reverse().join("/")) : null;
    const das = rules?.das ?? null;
    const status = rules ? (RULE_TO_STATUS[rules.overall_status] || "stable") : "nodata";
    const ring = farm.boundary?.coordinates?.[0]
      || [[farm.longitude - 0.0005, farm.latitude - 0.00032], [farm.longitude + 0.0005, farm.latitude - 0.00032],
        [farm.longitude + 0.0005, farm.latitude + 0.00032], [farm.longitude - 0.0005, farm.latitude + 0.00032]];
    const worst = (rules?.results || []).find((rule) => rule.status === "VIOLATION")
      || (rules?.results || []).find((rule) => rule.status === "WARN");
    const forecast = areaHa * 1.2; // t, assumed 1.2 t/ha until the yield model exists
    const block = {
      id: "B01",
      variety: "Mè đen (thực nghiệm)",
      area: areaHa,
      das: das ?? 0,
      stage: das !== null && das >= 0 ? D.stageFor(das) : null,
      gddPct: das !== null && das >= 0 ? Math.min(99, Math.round((das / D.SEASON_DAYS) * 100)) : 0,
      moisture: null,
      log: null,
      quality: null,
      status,
      issue: worst ? `${worst.id}: ${worst.message}` : null,
      polygon: ring.map(([lon, lat]) => [lat, lon]),
      forecast,
    };
    return {
      id: String(farm.id).toLowerCase(),
      code: farm.id,
      name: `Ruộng mè ${farm.id}`,
      owner: "Ruộng thực nghiệm",
      commune: "Huyện Củ Chi",
      district: "Củ Chi",
      provinceId: "hcm",
      provinceName: "TP. Hồ Chí Minh",
      lat: farm.latitude,
      lon: farm.longitude,
      area: areaHa,
      forecast,
      forecastRange: [forecast * 0.85, forecast * 1.15],
      confidence: rules ? 70 : null,
      // R09: check for 2/3 yellow pods from day 70, expected harvest day 75.
      harvestStart: sown ? addDays(sown, 70) : null,
      harvestEnd: sown ? addDays(sown, 80) : null,
      log: null,
      status,
      flagship: false,
      programId: null,
      varietyId: null,
      nodes: 0,
      nodesOnline: 0,
      blocks: [block],
      real: true,
      monitor,
      error,
    };
  }

  async function loadRealFarm() {
    state.realFarm = buildRealFarm(null, null);
    try {
      const response = await fetch("/api/monitor?days=30", { cache: "no-store" });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
      state.realFarm = buildRealFarm(payload, null);
      announce(`Đã tải dữ liệu thật của ${state.realFarm.name} từ Earth Engine.`);
    } catch (error) {
      state.realFarm = buildRealFarm(null, error.message);
      announce(`Không tải được dữ liệu thật của ruộng ${state.realFarm.code}.`);
    }
    rerender();
  }

  const WEATHER_PROVINCES = () => D.PROVINCES.filter((p) => allFarms().some((farm) => farm.provinceId === p.id));

  async function loadWeather() {
    const provinces = WEATHER_PROVINCES();
    const params = new URLSearchParams({
      latitude: provinces.map((p) => p.lat).join(","),
      longitude: provinces.map((p) => p.lon).join(","),
      current: "temperature_2m,relative_humidity_2m,wind_speed_10m,weather_code",
      daily: "precipitation_sum,et0_fao_evapotranspiration",
      past_days: "7",
      forecast_days: "1",
      timezone: "Asia/Bangkok",
    });
    try {
      const response = await fetch(`https://api.open-meteo.com/v1/forecast?${params}`);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = await response.json();
      const list = Array.isArray(payload) ? payload : [payload];
      state.weather = Object.fromEntries(provinces.map((p, index) => [p.id, list[index]]));
      state.weatherAt = new Date();
      announce(`Đã cập nhật thời tiết ${provinces.length} vùng trồng.`);
    } catch {
      state.weatherError = true;
    }
    if (currentRoute().name === "overview") rerender();
  }

  async function loadFarmWeather(farm) {
    if (state.farmWeather[farm.id]) return state.farmWeather[farm.id];
    const params = new URLSearchParams({
      latitude: farm.lat, longitude: farm.lon,
      current: "temperature_2m,relative_humidity_2m,wind_speed_10m,weather_code", timezone: "Asia/Bangkok",
    });
    try {
      const response = await fetch(`https://api.open-meteo.com/v1/forecast?${params}`);
      state.farmWeather[farm.id] = response.ok ? (await response.json()).current : null;
    } catch {
      state.farmWeather[farm.id] = null;
    }
    return state.farmWeather[farm.id];
  }

  // ---- maps ------------------------------------------------------------------------------
  const TILES = {
    // CARTO basemaps now need an API key; Esri's street map does not.
    voyager: ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}",
      { attribution: "Tiles © Esri — HERE, Garmin, OpenStreetMap contributors", maxZoom: 19 }],
    imagery: ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
      { attribution: "Imagery © Esri, Maxar, Earthstar Geographics", maxZoom: 20, maxNativeZoom: 19 }],
    labels: ["https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}",
      { maxZoom: 20, maxNativeZoom: 19 }],
  };
  const tile = (name) => L.tileLayer(...TILES[name]);

  function createMap(element, options = {}) {
    if (!window.L || !element) {
      if (element) element.innerHTML = '<p class="empty">Không tải được bản đồ.</p>';
      return null;
    }
    const map = L.map(element, { scrollWheelZoom: false, ...options });
    map.on("click", () => map.scrollWheelZoom.enable());
    map.on("mouseout", () => map.scrollWheelZoom.disable());
    // Redraw when the container changes size (web fonts, breakpoints, sidebars),
    // otherwise Leaflet leaves grey, untiled areas.
    if (window.ResizeObserver) {
      const observer = new ResizeObserver(() => map.invalidateSize({ pan: false }));
      observer.observe(element);
      map.on("unload", () => observer.disconnect());
    }
    state.maps.push(map);
    return map;
  }

  function farmMarker(farm) {
    const color = D.STATUS[farm.status].color;
    // Shape as well as colour tells the status apart (circle, diamond, triangle, hollow).
    const classes = ["farm-marker", farm.status, farm.flagship ? "flagship" : "", farm.real ? "real" : ""].join(" ");
    return L.marker([farm.lat, farm.lon], {
      icon: L.divIcon({ className: "", html: `<div class="${classes}" style="color:${color}"><i style="background:${color}"></i></div>`, iconSize: [16, 16], iconAnchor: [8, 8] }),
      title: `${farm.name} – ${D.STATUS[farm.status].name}`,
  alt: `${farm.name} – ${D.STATUS[farm.status].name}`,
    }).bindPopup(`<div class="popup-title">${esc(farm.name)}</div>
      <div class="popup-meta">${esc(farm.code)} · ${esc(farm.provinceName)}<br>${num(farm.area, 1)} ha · dự báo ${num(farm.forecast, farm.real ? 1 : 0)} t</div>
      ${statusPill(farm.status)}<br><br><a class="popup-link" href="/farm/${esc(farm.id)}" data-link>Xem chi tiết →</a>`);
  }

  function legendControl(position = "bottomleft") {
    const control = L.control({ position });
    control.onAdd = () => {
      const div = L.DomUtil.create("div", "map-legend");
      div.innerHTML = Object.entries(D.STATUS).map(([id, s]) =>
        `<div><span class="farm-marker mini ${id}" style="color:${s.color}" aria-hidden="true"><i style="background:${s.color}"></i></span>${s.name}</div>`).join("");
      return div;
    };
    return control;
  }

  // ---- overview ---------------------------------------------------------------------------
  function programSummaries(farms) {
    const { variety, program } = state.filters;
    return D.PROGRAMS.filter((p) => (variety === "all" || p.varietyId === variety) && (!program || p.id === program))
      .map((p) => {
        const forecast = sum(D.FARMS.filter((f) => f.programId === p.id), (f) => f.forecast);
        return { ...p, forecast, gap: forecast - p.commitment, variety: D.VARIETIES.find((v) => v.id === p.varietyId) };
      })
      .filter((p) => farms.some((f) => f.programId === p.id));
  }

  function overviewStats(farms) {
    const blocks = farms.flatMap((farm) => farm.blocks);
    const logged = farms.filter((farm) => Number.isFinite(farm.log));
    const confident = farms.filter((farm) => Number.isFinite(farm.confidence));
    const starts = farms.map((farm) => farm.harvestStart).filter(Boolean);
    const ends = farms.map((farm) => farm.harvestEnd).filter(Boolean);
    return {
      forecast: sum(farms, (f) => f.forecast),
      range: [sum(farms, (f) => f.forecastRange[0]), sum(farms, (f) => f.forecastRange[1])],
      area: sum(farms, (f) => f.area),
      farms: farms.length,
      riskTonnes: sum(blocks.filter((b) => b.status === "watch" || b.status === "risk"), (b) => b.forecast),
      log: logged.length ? sum(logged, (f) => f.log * f.area) / sum(logged, (f) => f.area) : null,
      nodataBlocks: blocks.filter((b) => b.status === "nodata").length,
      watchFarms: farms.filter((f) => f.status === "watch" || f.status === "risk").length,
      confidence: confident.length ? sum(confident, (f) => f.confidence * f.forecast) / sum(confident, (f) => f.forecast) : null,
      harvestStart: starts.length ? new Date(Math.min(...starts)) : null,
      harvestEnd: ends.length ? new Date(Math.max(...ends)) : null,
      stages: D.STAGES.map((stage) => blocks.filter((b) => b.stage && b.stage.id === stage.id).length),
    };
  }

  function chip(label, active, attrs) {
    return `<button type="button" class="chip${active ? " active" : ""}" aria-pressed="${active}" ${attrs}>${label}</button>`;
  }

  function renderOverview() {
    const farms = filteredFarms();
    const regionFarms = filteredFarms({ withRegion: true });
    const s = overviewStats(farms);
    const { variety, program, region } = state.filters;
    const regions = D.PROVINCES.filter((p) => farms.some((f) => f.provinceId === p.id));
    const stageColors = ["#cfe5f8", "#8cc2ec", "#1488d8", "#0a2c8f", "#d4a24c"];
    const stageTotal = s.stages.reduce((a, b) => a + b, 0) || 1;
    const rangeSpan = Math.max(1, s.range[1] - s.range[0]);
    const span = { min: s.range[0] * 0.97, max: s.range[1] * 1.03 };
    const pos = (v) => ((v - span.min) / (span.max - span.min)) * 100;

    app.innerHTML = `
      <h1 class="page-title">Tổng quan vùng trồng mè</h1>
      <p class="data-note">${dataNote()}</p>
      <div class="filters" role="group" aria-label="Bộ lọc">
        <span class="kicker">Giống</span>
        ${chip("Tất cả", variety === "all" && !program, 'data-variety="all"')}
        ${D.VARIETIES.map((v) => chip(v.code, variety === v.id && !program, `data-variety="${v.id}"`)).join("")}
        <span class="filter-sep"></span>
        <span class="kicker">Chương trình</span>
        ${D.PROGRAMS.map((p) => chip(esc(p.name), program === p.id, `data-program="${p.id}"`)).join("")}
      </div>

      <section class="alerts">${programSummaries(farms).map((p) => {
        const short = p.gap < 0;
        const pct = Math.round((Math.abs(p.gap) / p.commitment) * 100);
        return `<article class="alert-card${short ? "" : " ok"}">
          <span class="alert-icon">${short ? icon.trendDown : icon.trendUp}</span>
          <div class="alert-body">
            <div class="alert-title"><span class="badge badge-variety">${esc(p.variety.code)}</span>${esc(p.region)} — ${esc(p.season)}</div>
            <div class="alert-sub">Dự báo <strong>${num(p.forecast)} t</strong> / Cam kết <strong>${num(p.commitment)} t</strong></div>
          </div>
          <div class="alert-gap"><strong>${short ? "-" : "+"}${num(Math.abs(p.gap))}<small>t</small></strong><span>${short ? "Thiếu" : "Vượt"} ${pct}%</span></div>
        </article>`;
      }).join("")}</section>

      <section class="kpis">
        <article class="card kpi"><div class="kpi-label">Dự báo sản lượng</div><div class="kpi-value">${num(s.forecast)}<small>tấn</small></div></article>
        <article class="card kpi"><div class="kpi-label">Diện tích quản lý</div><div class="kpi-value">${num(s.area, 1)}<small>ha</small></div></article>
        <article class="card kpi"><div class="kpi-label">Nông trại</div><div class="kpi-value">${num(s.farms)}<small>trại</small></div></article>
        <article class="card kpi"><div class="kpi-label">Sản lượng rủi ro</div><div class="kpi-value gold">${num(s.riskTonnes, 1)}<small>tấn</small></div></article>
        <article class="card kpi"><div class="kpi-label">Đủ nhật ký canh tác</div><div class="kpi-value">${num(s.log)}<small>%</small></div></article>
        <article class="card kpi"><div class="kpi-label">Ô thiếu dữ liệu</div><div class="kpi-value ${s.nodataBlocks ? "red" : ""}">${num(s.nodataBlocks)}<small>ô</small></div></article>
      </section>

      <section class="grid-main">
        <article class="card card-pad">
          <div class="card-head">
            <h2 class="card-title">Bản đồ vùng sản xuất</h2>
            <div class="filters" style="margin:0">
              ${chip("Tất cả", region === "all", 'data-region="all"')}
              ${regions.map((p) => chip(esc(p.name), region === p.id, `data-region="${p.id}"`)).join("")}
            </div>
          </div>
          <div class="map" id="region-map" role="region" aria-label="Bản đồ các nông trại trong vùng"></div>
        </article>
        <div class="stack">
          <article class="card supply-card">
            <div class="kpi-label">Dự báo nguồn cung toàn vùng</div>
            <div class="supply-value">${num(s.forecast)}<small>tấn</small></div>
            <div class="range"><span style="left:${pos(s.range[0])}%;right:${100 - pos(s.range[1])}%"></span><i style="left:${pos(s.forecast)}%"></i></div>
            <div class="range-labels"><span>${num(s.range[0])} t</span><span>${num(s.range[1])} t</span></div>
            <div class="supply-meta">
              <div><span>Thu hoạch</span><strong>${fmtDate(s.harvestStart)} → ${fmtDate(s.harvestEnd)}</strong></div>
              <div><span>Tin cậy</span><strong>${num(s.confidence)}%</strong></div>
            </div>
          </article>
          <article class="card card-pad">
            <h2 class="card-title" style="margin-bottom:14px">Rủi ro sản xuất</h2>
            <ul class="risk-list">
              <li><span class="dot" style="background:var(--watch)"></span>${num(s.watchFarms)} nông trại cần theo dõi</li>
              <li><span class="dot" style="background:var(--nodata)"></span>${num(s.nodataBlocks)} ô thiếu dữ liệu</li>
              <li><span class="dot" style="background:var(--risk)"></span>${num(s.riskTonnes, 1)} tấn trong vùng rủi ro</li>
            </ul>
          </article>
        </div>
      </section>

      <section class="grid-lower">
        <article class="card card-pad">
          <h2 class="card-title" style="margin-bottom:14px">Phân bố giai đoạn vụ mùa</h2>
          <div class="stage-bar">${s.stages.map((count, i) => `<span style="width:${(count / stageTotal) * 100}%;background:${stageColors[i]}" title="${D.STAGES[i].name}: ${count} ô"></span>`).join("")}</div>
          <div class="stage-counts">${D.STAGES.map((stage, i) => `<div><strong>${s.stages[i]}</strong>${stage.name}</div>`).join("")}</div>
        </article>
        <section>
          <h2 class="section-title">Khí tượng và thủy văn các vùng trồng <span class="muted-inline">${state.weatherAt ? `Open-Meteo · cập nhật ${fmtTime(state.weatherAt)}` : ""}</span></h2>
          <div class="weather-grid">${renderWeatherCards()}</div>
        </section>
      </section>

      <article class="card table-card">
        <h2 class="card-title card-pad" style="padding-bottom:12px">Danh sách nông trại</h2>
        <div class="table-scroll">
          <table class="farm-table">
            <thead><tr><th>Nông trại</th><th>Vùng</th><th class="num">Diện tích</th><th class="num">Ô</th><th class="num">Dự báo</th><th class="num">Thu hoạch</th><th class="num">Nhật ký</th><th>Trạng thái</th></tr></thead>
            <tbody>${regionFarms.map((farm) => `
              <tr data-href="/farm/${esc(farm.id)}">
                <td><div class="farm-name"><strong><a href="/farm/${esc(farm.id)}" data-link>${esc(farm.name)}</a>${farm.flagship ? '<span class="badge badge-gold">Trọng điểm</span>' : ""}${farm.real ? '<span class="badge badge-real">Dữ liệu thật</span>' : ""}</strong><span>${esc(farm.code)}</span></div></td>
                <td>${esc(farm.district)}, ${esc(farm.provinceName)}</td>
                <td class="num">${num(farm.area, 1)} ha</td>
                <td class="num">${farm.blocks.length}</td>
                <td class="num forecast">${num(farm.forecast, farm.real ? 1 : 0)} t</td>
                <td class="num">${fmtDate(farm.harvestStart)} → ${fmtDate(farm.harvestEnd)}</td>
                <td class="num">${Number.isFinite(farm.log) ? `${farm.log}%` : "—"}</td>
                <td>${statusPill(farm.status, farm.real && farm.status === "nodata" ? (farm.error ? "Mất kết nối" : "Đang tải") : null)}</td>
              </tr>`).join("")}</tbody>
          </table>
        </div>
      </article>`;

    const map = createMap(document.querySelector("#region-map"));
    if (map) {
      tile("voyager").addTo(map);
      const markers = regionFarms.map((farm) => farmMarker(farm).addTo(map));
      legendControl().addTo(map);
      const group = L.featureGroup(markers);
      if (markers.length) map.fitBounds(group.getBounds(), { padding: [40, 40], maxZoom: 11 });
      else map.setView([10.6, 105.9], 8);
    }
  }

  function renderWeatherCards() {
    const provinces = WEATHER_PROVINCES();
    if (!state.weather) {
      const text = state.weatherError ? "Không tải được dữ liệu Open-Meteo." : "Đang tải thời tiết…";
      return provinces.map((p) => `<article class="card weather-card"><div class="weather-top">${esc(p.name)}</div><p class="weather-balance" style="margin-top:10px">${text}</p></article>`).join("");
    }
    return provinces.map((p) => {
      const w = state.weather[p.id];
      if (!w) return "";
      const rain = (w.daily?.precipitation_sum || []).slice(0, 7);
      const et0 = (w.daily?.et0_fao_evapotranspiration || []).slice(0, 7);
      const rainTotal = sum(rain, (v) => v);
      const balance = rainTotal - sum(et0, (v) => v);
      const maxRain = Math.max(5, ...rain);
      const code = w.current?.weather_code ?? 0;
      const weatherIcon = code >= 51 ? icon.rain : code >= 2 ? icon.cloud : icon.sun;
      return `<article class="card weather-card">
        <div class="weather-top">${esc(p.name)}<span class="weather-temp">${weatherIcon}${num(w.current?.temperature_2m)}°C</span></div>
        <div class="weather-meta"><span>${icon.drop}${num(rainTotal)}mm/7n</span><span>${icon.wind}${num(w.current?.wind_speed_10m)}km/h</span></div>
        <div class="rain-bars">${rain.map((v) => `<i class="${v >= 1 ? "wet" : ""}" style="height:${Math.max(8, (v / maxRain) * 100)}%" title="${num(v, 1)} mm"></i>`).join("")}</div>
        <div class="weather-balance">Cân bằng nước 7 ngày: ${balance >= 0 ? "+" : ""}${num(balance)} mm</div>
      </article>`;
    }).join("");
  }

  // ---- farm detail -----------------------------------------------------------------------
  const MAP_MODES = {
    status: { name: "Trạng thái SX", color: (b) => D.STATUS[b.status].color },
    forecast: { name: "Dự báo năng suất", color: (b) => scale(b.forecast / b.area, 1.1, 2.0) },
    log: { name: "Đủ nhật ký", color: (b) => scale(b.log, 80, 100) },
    quality: { name: "Chất lượng canh tác", color: (b) => scale(b.quality, 60, 96) },
  };
  function scale(value, low, high) {
    if (!Number.isFinite(value)) return D.STATUS.nodata.color;
    const t = Math.max(0, Math.min(1, (value - low) / (high - low)));
    const stops = [[217, 72, 59], [212, 154, 42], [47, 138, 58]];
    const [a, b, u] = t < 0.5 ? [stops[0], stops[1], t * 2] : [stops[1], stops[2], (t - 0.5) * 2];
    return `rgb(${a.map((c, i) => Math.round(c + (b[i] - c) * u)).join(",")})`;
  }

  function farmScore(farm) {
    const stable = farm.blocks.filter((b) => b.status === "stable").length / farm.blocks.length;
    const quality = sum(farm.blocks, (b) => b.quality) / farm.blocks.length;
    const score = Math.round(farm.log * 2.4 + quality * 1.6 + stable * 120);
    return { score, level: Math.min(5, Math.max(1, Math.floor(score / 120))) };
  }

  function renderFarm(farmId) {
    const farm = findFarm(farmId);
    if (!farm) {
      app.innerHTML = `${breadcrumbs([["Tổng quan", "/"], ["Chi tiết nông trại"]])}<p class="empty">Không tìm thấy nông trại.</p>`;
      return;
    }
    const blocks = [...farm.blocks].sort((a, b) => ["stable", "watch", "risk", "nodata"].indexOf(a.status) - ["stable", "watch", "risk", "nodata"].indexOf(b.status));
    const fr = farm.forecastRange;
    const span = { min: fr[0] * 0.95, max: fr[1] * 1.05 };
    const pos = (v) => ((v - span.min) / (span.max - span.min)) * 100;
    const issues = farm.blocks.filter((b) => b.issue);
    const score = farm.real ? null : farmScore(farm);

    app.innerHTML = `
      ${breadcrumbs([["Tổng quan", "/"], ["Chi tiết nông trại"]])}
      <a class="back-link" href="/" data-link>${icon.back} Tổng quan vùng trồng</a>
      <div class="farm-header">
        <div>
          <h1>${esc(farm.name)} ${statusPill(farm.status, farm.real && farm.status === "nodata" ? (farm.error ? "Mất kết nối" : "Đang tải") : null)}${farm.real ? '<span class="badge badge-real">Dữ liệu thật</span>' : '<span class="badge badge-sim">Mô phỏng</span>'}</h1>
          <p>${esc(farm.code)}, ${esc(farm.commune)}, ${esc(farm.district)}, ${esc(farm.provinceName)}</p>
        </div>
        <div class="segmented" role="group" aria-label="Kiểu bản đồ">
          <button type="button" class="active" aria-pressed="true" data-basemap="imagery">${icon.satellite} Ảnh vệ tinh</button>
          <button type="button" aria-pressed="false" data-basemap="voyager">${icon.map} Bản đồ</button>
        </div>
      </div>

      <section class="kpis kpis-farm">
        <article class="card kpi"><div class="kpi-label">Tổng diện tích</div><div class="kpi-value">${num(farm.area, farm.area < 10 ? 2 : 1)} ha</div></article>
        <article class="card kpi"><div class="kpi-label">Dự báo vụ này</div><div class="kpi-value">${num(farm.forecast, farm.real ? 1 : 0)} t</div></article>
        <article class="card kpi"><div class="kpi-label">Đủ nhật ký</div><div class="kpi-value">${Number.isFinite(farm.log) ? `${farm.log}%` : "—"}</div></article>
        <article class="card kpi"><div class="kpi-label">Cửa sổ thu hoạch</div><div class="kpi-value compact" style="font-size:18px">${fmtDate(farm.harvestStart)} →<br><small style="margin:0;color:var(--ink);font-size:13px">${fmtDate(farm.harvestEnd)}</small></div></article>
        ${score ? `<article class="card kpi score-card"><div class="kpi-label">Điểm trang trại</div><div class="kpi-value">${score.score}</div>
          <div class="score-meta"><span>${icon.flame}${farm.streakDays} ngày liên tiếp</span><b>CẤP ${score.level}</b></div></article>`
          : `<article class="card kpi score-card"><div class="kpi-label">Quy tắc canh tác</div><div class="kpi-value" style="font-size:20px">${esc(farm.monitor?.rules ? RULE_TEXT[farm.monitor.rules.overall_status] : "—")}</div>
          <div class="score-meta"><span>${esc(farm.monitor?.rules?.stage_label || "")}</span><b>${farm.monitor?.rules?.das != null ? `NGÀY ${farm.monitor.rules.das}` : ""}</b></div></article>`}
      </section>

      <section class="grid-main">
        <article class="card card-pad">
          <div class="card-head"><h2 class="card-title">Bản đồ nông trại</h2>${farm.real ? '<span class="muted" style="font-size:12px">Ranh giới ' + (farm.monitor?.farm?.boundary_source === "configured" ? "đã cấu hình" : "ước tính (nét đứt)") + "</span>" : ""}</div>
          <div class="map map-farm" id="farm-map" role="region" aria-label="Bản đồ vệ tinh nông trại và các ô canh tác"></div>
        </article>
        <div class="stack">
          <article class="card card-pad">
            <h2 class="card-title" style="margin-bottom:8px">Forecast nông trại</h2>
            <div class="range light"><span style="left:${pos(fr[0])}%;right:${100 - pos(fr[1])}%"></span><i style="left:${pos(farm.forecast)}%"></i></div>
            <div class="range-labels light"><span>${num(fr[0], farm.real ? 2 : 0)} t</span><span>${num(fr[1], farm.real ? 2 : 0)} t</span></div>
            <div class="forecast-meta"><span>Tin cậy <strong>${num(farm.confidence)}%</strong></span><span>${num(farm.area, farm.area < 10 ? 2 : 1)} ha trong vụ</span></div>
            ${farm.real ? '<p class="real-note">Năng suất giả định 1,2 t/ha cho đến khi có mô hình dự báo.</p>' : ""}
          </article>
          <article class="card card-pad">
            <h2 class="card-title" style="margin-bottom:12px">Môi trường hiện tại</h2>
            <div class="env-grid" id="farm-env">${envTiles(farm, null)}</div>
            ${farm.real ? "" : `<ul class="node-list">${farm.blocks.filter((b) => b.status === "nodata" || b.status === "risk").slice(0, 3).map((b, i) => `
              <li><span class="dot" style="background:${D.STATUS[b.status].color}"></span>Ô ${b.id}, IoT-${farm.code.slice(-2)}${pad(i + 1)}<span>${b.status === "nodata" ? "3 ngày trước" : "2h trước"}</span></li>`).join("")}</ul>`}
          </article>
          <article class="card card-pad">
            <h2 class="card-title" style="margin-bottom:12px">Cảnh báo vận hành</h2>
            <ul class="ops-list">${issues.length ? issues.map((b) => `<li class="${b.status}">${farm.real ? "" : `Ô ${b.id}: `}${esc(b.issue)}</li>`).join("")
              : `<li class="ok">${farm.real && !farm.monitor ? esc(farm.error ? `Không kết nối được /api/monitor: ${farm.error}` : "Đang tải dữ liệu thật…") : "Không có cảnh báo."}</li>`}</ul>
          </article>
        </div>
      </section>

      ${farm.real ? renderRealFarmRules(farm) : ""}

      <article class="card card-pad">
        <h2 class="card-title" style="margin-bottom:14px">Ô canh tác</h2>
        <div class="block-grid">${blocks.map((b) => {
          const color = D.STATUS[b.status].color;
          return `<a class="block-card" href="/farm/${esc(farm.id)}/block/${b.id}" data-link>
            <div class="block-card-top">Ô ${b.id}<span class="dot" style="background:${color}" title="${D.STATUS[b.status].name}"></span><span class="sr-only">, ${D.STATUS[b.status].name}</span></div>
            <small>${esc(b.variety)}</small>
            <div class="progress"><span style="width:${b.gddPct}%;background:${color}"></span></div>
            <div class="block-card-foot"><span>${num(b.forecast, 1)} t</span><span>${Number.isFinite(b.moisture) ? `ẩm ${b.moisture}%` : (b.stage ? b.stage.name : "Chưa gieo")}</span><span>${b.gddPct}% GDD</span></div>
          </a>`;
        }).join("")}</div>
      </article>`;

    mountFarmMap(farm);
    if (!farm.real) {
      loadFarmWeather(farm).then((current) => {
        const el = document.querySelector("#farm-env");
        if (el && currentRoute().params.farmId === farm.id) el.innerHTML = envTiles(farm, current);
      });
    }
  }

  function envTiles(farm, current) {
    if (farm.real) {
      const latest = farm.monitor?.weather?.latest;
      const ndvi = farm.monitor?.satellite?.at?.(-1);
      return `
        <div class="env-tile"><span>${icon.thermo} Nhiệt độ</span><strong>${latest ? `${num(latest.temperature_c, 1)}°C` : "—"}</strong></div>
        <div class="env-tile"><span>${icon.drop} Độ ẩm KK</span><strong>${latest ? `${num(latest.humidity_pct)}%` : "—"}</strong></div>
        <div class="env-tile"><span>${icon.rain} Mưa ngày</span><strong>${latest ? `${num(latest.rainfall_mm, 1)} mm` : "—"}</strong></div>
        <div class="env-tile"><span>${icon.leaf} NDVI</span><strong>${ndvi ? num(ndvi.ndvi, 2) : "—"}</strong></div>
        <p class="real-note" style="grid-column:1/-1">ERA5-Land ${latest ? `ngày ${esc(latest.date)}` : ""} · Sentinel-2 ${ndvi ? `ngày ${esc(ndvi.date)}` : ""} (Google Earth Engine)</p>`;
    }
    const value = (v, unit, d = 0) => (current && Number.isFinite(v) ? `${num(v, d)}${unit}` : "…");
    return `
      <div class="env-tile"><span>${icon.thermo} Nhiệt độ</span><strong>${value(current?.temperature_2m, "°C", 1)}</strong></div>
      <div class="env-tile"><span>${icon.drop} Độ ẩm KK</span><strong>${value(current?.relative_humidity_2m, "%")}</strong></div>
      <div class="env-tile"><span>${icon.wind} Gió</span><strong>${value(current?.wind_speed_10m, " km/h")}</strong></div>
      <div class="env-tile"><span>${icon.signal} Node IoT</span><strong>${farm.nodesOnline}/${farm.nodes}</strong></div>`;
  }

  function mountFarmMap(farm) {
    const map = createMap(document.querySelector("#farm-map"));
    if (!map) return;
    const bases = { imagery: tile("imagery"), voyager: tile("voyager") };
    bases.imagery.addTo(map);
    let mode = "status";
    const blockLayer = L.layerGroup().addTo(map);
    const drawBlocks = () => {
      blockLayer.clearLayers();
      farm.blocks.forEach((b) => {
        const color = farm.real ? "#ffe066" : MAP_MODES[mode].color(b);
        const dash = farm.real
          ? (farm.monitor?.farm?.boundary_source !== "configured" ? "6 5" : null)
          : (mode === "status" ? STATUS_DASH[b.status] : null);
        L.polygon(b.polygon, {
          color, weight: mode === "status" && b.status !== "stable" ? 3 : 2, fillColor: color,
          fillOpacity: farm.real ? 0.08 : 0.32, dashArray: dash,
        }).bindTooltip(`Ô ${b.id}`, {
          // Permanent labels only when there is room for them; otherwise on hover/tap.
          permanent: farm.blocks.length <= 30 && map.getSize().x >= 600, direction: "center", className: "block-label" })
          .on("click", () => navigate(`/farm/${farm.id}/block/${b.id}`))
          .on("mouseover", (event) => event.target.setTooltipContent(`Ô ${b.id} · ${D.STATUS[b.status].name}`))
          .on("mouseout", (event) => event.target.setTooltipContent(`Ô ${b.id}`))
          .addTo(blockLayer);
      });
    };
    drawBlocks();
    map.fitBounds(L.featureGroup(blockLayer.getLayers()).getBounds(), { padding: [30, 30], maxZoom: 18 });

    if (farm.real) {
      const layers = farm.monitor?.map_layers;
      const overlays = {};
      const baseLayers = { "Ảnh vệ tinh · Esri": bases.imagery };
      if (layers) {
        const attribution = "Sentinel-2 · Copernicus via Google Earth Engine";
        baseLayers["Sentinel-2 · ảnh gần đây (10 m)"] = L.tileLayer(layers.true_color, { attribution, maxZoom: 20, maxNativeZoom: 18 });
        overlays["NDVI · Sentinel-2"] = L.tileLayer(layers.ndvi, { attribution, maxZoom: 20, maxNativeZoom: 18, opacity: 0.7 });
      }
      overlays["Tên địa danh"] = tile("labels");
      L.control.layers(baseLayers, overlays, { collapsed: false }).addTo(map);
    } else {
      const modes = L.control({ position: "topright" });
      modes.onAdd = () => {
        const div = L.DomUtil.create("div", "map-modes");
        div.innerHTML = Object.entries(MAP_MODES).map(([id, m]) => `<button type="button" data-mode="${id}" class="${id === mode ? "active" : ""}" aria-pressed="${id === mode}">${m.name}</button>`).join("");
        L.DomEvent.disableClickPropagation(div);
        div.addEventListener("click", (event) => {
          const button = event.target.closest("button[data-mode]");
          if (!button) return;
          mode = button.dataset.mode;
          div.querySelectorAll("button").forEach((b) => { b.classList.toggle("active", b === button); b.setAttribute("aria-pressed", String(b === button)); });
          drawBlocks();
        });
        return div;
      };
      modes.addTo(map);
      // Status legend: colour plus outline pattern, so the map does not rely on colour alone.
      const legend = L.control({ position: "bottomleft" });
      legend.onAdd = () => {
        const div = L.DomUtil.create("div", "map-legend");
        div.innerHTML = Object.entries(D.STATUS).map(([id, st]) => `<div><svg width="26" height="10" aria-hidden="true">
          <line x1="1" y1="5" x2="25" y2="5" stroke="${st.color}" stroke-width="3" ${STATUS_DASH[id] ? `stroke-dasharray="${STATUS_DASH[id]}"` : ""}/></svg>${st.name}</div>`).join("");
        return div;
      };
      legend.addTo(map);
    }
    L.control.scale({ imperial: false }).addTo(map);
    document.querySelectorAll("[data-basemap]").forEach((button) => button.addEventListener("click", () => {
      document.querySelectorAll("[data-basemap]").forEach((b) => { b.classList.toggle("active", b === button); b.setAttribute("aria-pressed", String(b === button)); });
      Object.values(bases).forEach((layer) => layer.remove());
      bases[button.dataset.basemap].addTo(map);
      bases[button.dataset.basemap].bringToBack();
    }));
  }

  const RULE_TEXT = { OK: "Đạt", WARN: "Cảnh báo", VIOLATION: "Vi phạm", NO_DATA: "Thiếu dữ liệu", "N/A": "Không áp dụng", INFO: "Thông tin" };
  const RULE_CLASS = { OK: "stable", WARN: "watch", VIOLATION: "risk" };
  const TASK_TEXT = { DUE: "Đến hạn", UPCOMING: "Sắp tới", FUTURE: "Sau", PAST: "Đã qua", MANUAL: "Khi thấy" };

  function renderRealFarmRules(farm) {
    const rules = farm.monitor?.rules;
    if (!rules) return "";
    const schedule = rules.results.find((r) => r.id === "R09");
    const tasks = [...(schedule?.tasks || []).filter((t) => t.state !== "PAST"), ...(schedule?.manual_tasks || [])];
    return `<section class="grid-main">
      <article class="card card-pad">
        <div class="card-head"><h2 class="card-title">Quy tắc canh tác · mè đen Bình Thuận</h2>${statusPill(RULE_CLASS[rules.overall_status] || "stable", `Tổng: ${RULE_TEXT[rules.overall_status]}`)}</div>
        <ul class="rule-list">${rules.results.filter((r) => r.id !== "R09").map((r) => {
          const value = r.value === null || r.value === undefined ? "" : `${typeof r.value === "number" ? num(r.value, Number.isInteger(r.value) ? 0 : Math.abs(r.value) < 1 ? 2 : 1) : esc(r.value)} ${r.unit && r.unit !== "NDVI" ? esc(r.unit) : ""}`;
          return `<li class="rule-item">${statusPill(RULE_CLASS[r.status] || "nodata", RULE_TEXT[r.status] || r.status)}
            <span class="rule-label">${esc(r.label)}<small>${esc(r.id)}</small></span><span class="rule-value">${value}</span>
            <span class="rule-message">${esc(r.message)}</span></li>`;
        }).join("")}</ul>
        <p class="real-note">Ngày gieo ${esc(rules.planting_date || "chưa đặt")} · ${esc(rules.stage_label || "")}. Ngưỡng lấy từ config/sesame_rules.json; ngưỡng giả định: ${esc((rules.assumed_thresholds || []).join(", "))}.</p>
      </article>
      <article class="card card-pad">
        <h2 class="card-title" style="margin-bottom:8px">Lịch chăm sóc</h2>
        <ul class="task-list">${tasks.length ? tasks.map((t) => `<li class="task-item"><strong>${esc(t.label)}</strong>${statusPill(t.state === "DUE" ? "watch" : t.state === "UPCOMING" ? "stable" : "nodata", TASK_TEXT[t.state] || t.state)}
          <span>${t.target_date ? `${esc(t.target_date)} · ngày ${t.das}` : `Khi: ${esc(t.trigger)}`}</span></li>`).join("") : '<li class="task-item"><span>Chưa có lịch.</span></li>'}</ul>
      </article>
    </section>`;
  }

  // ---- block detail ----------------------------------------------------------------------
  // Activity plan from the Binh Thuan guide (same steps as R09 in config/sesame_rules.json).
  const ACTIVITIES = [
    [-3, "Làm đất: cày sâu 15 cm, lên luống nếu khó thoát nước"], [-1, "Phun Ronstar tiền nảy mầm"],
    [0, "Xử lý hạt giống (Rovral 2 g/kg)"], [0, "Bón lót (hữu cơ, vôi, super lân, 1/3 urê, 1/3 KCl)"],
    [0, "Gieo hàng 60 × 15 cm, 3 kg/ha"], [12, "Tỉa thưa (2 lá thật)"], [15, "Bón thúc lần 1"],
    [15, "Phun Dual / xới xáo lần 1"], [25, "Bón thúc lần 2"], [25, "Phun Dual / xới xáo lần 2"],
    [35, "Thăm đồng sâu bệnh (chỉ phun khi có dấu hiệu)"], [40, "Ra hoa: tưới đều, không để hạn"],
    [70, "Kiểm tra thu hoạch (2/3 trái vàng)"], [75, "Thu hoạch, bó dựng 3–5 ngày, phơi 2–3 nắng"],
  ];

  function renderBlock(farmId, blockId) {
    const farm = findFarm(farmId);
    const block = farm?.blocks.find((b) => b.id === blockId);
    if (!block) {
      app.innerHTML = `${breadcrumbs([["Tổng quan", "/"], ["Chi tiết ô"]])}<p class="empty">Không tìm thấy ô canh tác.</p>`;
      return;
    }
    const rand = D.seeded(`${farm.code}:${block.id}:log`);
    const sown = addDays(D.TODAY, -block.das);
    const timeline = farm.real
      ? (farm.monitor?.rules?.results.find((r) => r.id === "R09")?.tasks || []).map((t) => ({
        date: t.target_date, label: t.label, state: t.state === "PAST" ? "done" : t.state === "DUE" ? "late" : "planned",
      }))
      : ACTIVITIES.map(([das, label]) => {
        let stateName = das <= block.das ? "done" : "planned";
        if (stateName === "done" && block.status !== "stable") {
          const r = rand();
          if (r < 0.12) stateName = "missing";
          else if (r < 0.3) stateName = "late";
        }
        return { date: fmtDate(addDays(sown, das)), label, state: stateName };
      });
    const STATE = { done: ["stable", "Đã ghi"], late: ["watch", "Ghi trễ"], missing: ["risk", "Thiếu nhật ký"], planned: ["nodata", "Kế hoạch"] };

    app.innerHTML = `
      ${breadcrumbs([["Tổng quan", "/"], [farm.name, `/farm/${farm.id}`], [`Ô ${block.id}`]])}
      <a class="back-link" href="/farm/${esc(farm.id)}" data-link>${icon.back} ${esc(farm.name)}</a>
      <div class="farm-header"><div>
        <h1>Ô ${esc(block.id)} ${statusPill(block.status)}</h1>
        <p>${esc(block.variety)} · ${esc(farm.name)}</p>
      </div></div>
      <section class="kpis kpis-farm">
        <article class="card kpi"><div class="kpi-label">Diện tích</div><div class="kpi-value">${num(block.area, 2)} ha</div></article>
        <article class="card kpi"><div class="kpi-label">Giai đoạn</div><div class="kpi-value">${block.stage ? esc(block.stage.name) : "Chưa gieo"}</div><span class="muted" style="font-size:11px">Ngày ${block.das} sau gieo</span></article>
        <article class="card kpi"><div class="kpi-label">Dự báo</div><div class="kpi-value">${num(block.forecast, 1)} t</div></article>
        <article class="card kpi"><div class="kpi-label">Độ ẩm đất</div><div class="kpi-value">${Number.isFinite(block.moisture) ? `${block.moisture}%` : "—"}</div></article>
        <article class="card kpi"><div class="kpi-label">Đủ nhật ký</div><div class="kpi-value">${Number.isFinite(block.log) ? `${block.log}%` : "—"}</div></article>
      </section>
      <section class="grid-main">
        <article class="card card-pad"><h2 class="card-title" style="margin-bottom:12px">Vị trí ô</h2><div class="map map-block" id="block-map" role="region" aria-label="Vị trí ô trên ảnh vệ tinh"></div></article>
        <article class="card card-pad">
          <h2 class="card-title" style="margin-bottom:4px">${farm.real ? "Mưa theo ngày (ERA5-Land)" : "Độ ẩm đất 30 ngày"}</h2>
          <p class="muted" style="font-size:11px;margin-bottom:6px">${farm.real ? "Ước tính vệ tinh/tái phân tích, 30 ngày gần nhất" : "Cảm biến TEROS 12 · độ sâu 15 cm"}</p>
          <div class="chart">${farm.real ? rainChart(farm) : moistureChart(farm, block)}</div>
          ${block.issue ? `<ul class="ops-list" style="margin-top:10px"><li class="${block.status}">${esc(block.issue)}</li></ul>` : ""}
        </article>
      </section>
      <article class="card card-pad">
        <h2 class="card-title" style="margin-bottom:6px">Nhật ký canh tác</h2>
        <ul class="timeline">${timeline.length ? timeline.map((item) => `<li class="${item.state}"><time>${esc(item.date)}</time><span>${esc(item.label)}</span>${statusPill(...STATE[item.state])}</li>`).join("")
          : '<li class="planned"><span>Chưa có lịch canh tác.</span></li>'}</ul>
      </article>`;

    const map = createMap(document.querySelector("#block-map"));
    if (map) {
      tile("imagery").addTo(map);
      farm.blocks.forEach((b) => {
        const active = b.id === block.id;
        L.polygon(b.polygon, { color: active ? "#ffe066" : "#ffffff", weight: active ? 3 : 1, fillOpacity: active ? 0.15 : 0.04 })
          .on("click", () => navigate(`/farm/${farm.id}/block/${b.id}`)).addTo(map);
      });
      map.fitBounds(L.polygon(block.polygon).getBounds(), { padding: [60, 60], maxZoom: 18 });
    }
  }

  function lineChart(values, { min, max, unit, bars = false, labels = [], title = "" }) {
    // Charts are images to screen readers, with a one-line summary; every point/bar has a tooltip.
    const finite = values.filter(Number.isFinite);
    const summary = `${title}: ${labels[0] || ""} – ${labels.at(-1) || ""}, thấp nhất ${num(Math.min(...finite), 1)}${unit}, cao nhất ${num(Math.max(...finite), 1)}${unit}`;
    // Drawn at about the panel's real width (~360 px) so axis text renders at its CSS size.
    const w = 360, h = 180, left = 34, right = 8, top = 10, bottom = 22;
    const x = (i) => left + ((w - left - right) * i) / Math.max(1, values.length - 1);
    const y = (v) => top + (1 - (v - min) / (max - min)) * (h - top - bottom);
    const ticks = [min, (min + max) / 2, max];
    const grid = ticks.map((t) => `<line class="grid" x1="${left}" x2="${w - right}" y1="${y(t)}" y2="${y(t)}"/><text class="axis" x="0" y="${y(t) + 3}">${num(t)}${unit}</text>`).join("");
    const xl = labels.map((label, i) => (i % Math.ceil(labels.length / 4) === 0 ? `<text class="axis" x="${x(i)}" y="${h - 4}" text-anchor="middle">${esc(label)}</text>` : "")).join("");
    if (bars) {
      const bw = ((w - left - right) / values.length) * 0.6;
      return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img" aria-label="${esc(summary)}">${grid}${values.map((v, i) => `<rect x="${x(i) - bw / 2}" y="${y(v || 0)}" width="${bw}" height="${y(min) - y(v || 0)}" rx="2" fill="#4d9fe0"><title>${esc(labels[i] || "")}: ${num(v, 1)} ${unit}</title></rect>`).join("")}${xl}</svg>`;
    }
    const path = values.map((v, i) => `${i ? "L" : "M"}${x(i)} ${y(v)}`).join(" ");
    return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img" aria-label="${esc(summary)}">${grid}
      <path d="${path} L${x(values.length - 1)} ${y(min)} L${x(0)} ${y(min)} Z" fill="#1488d8" opacity=".12"/>
      <path d="${path}" fill="none" stroke="#0a2c8f" stroke-width="2"/>${values.map((v, i) => `<circle class="point" cx="${x(i)}" cy="${y(v)}" r="2.5"><title>${esc(labels[i] || "")}: ${num(v, 1)}${unit}</title></circle>`).join("")}${xl}</svg>`;
  }

  function moistureChart(farm, block) {
    const rand = D.seeded(`${farm.code}:${block.id}:vwc`);
    // Walk backwards from today's reading: slow drying, occasional rain/irrigation jumps.
    let v = block.moisture;
    const values = Array.from({ length: 30 }, () => {
      const value = v;
      v += rand() < 0.15 ? -between(rand, 3, 7) : between(rand, 0.2, 1.1);
      v = Math.max(24, Math.min(56, v));
      return value;
    }).reverse();
    const labels = values.map((_, i) => fmtDate(addDays(D.TODAY, i - 29)).slice(0, 5));
    return lineChart(values, { min: 20, max: 60, unit: "%", labels, title: "Độ ẩm đất 30 ngày" });
  }
  const between = (rand, a, b) => a + rand() * (b - a);

  function rainChart(farm) {
    const series = farm.monitor?.weather?.series || [];
    if (!series.length) return '<p class="empty">Chưa có dữ liệu.</p>';
    const values = series.map((d) => d.rainfall_mm || 0);
    return lineChart(values, { min: 0, max: Math.max(10, ...values), unit: "mm", bars: true, title: "Mưa theo ngày (ERA5-Land)", labels: series.map((d) => d.date.slice(8, 10) + "/" + d.date.slice(5, 7)) });
  }

  // ---- products --------------------------------------------------------------------------
  const byId = (list, id) => list.find((item) => item.id === id);

  function renderProducts(category = "all") {
    const products = D.PRODUCTS.filter((p) => category === "all" || p.category === category);
    const units = sum(D.PRODUCTS, (p) => p.units);
    app.innerHTML = `
      ${breadcrumbs([["Tổng quan", "/"], ["Sản phẩm & Lineage"]])}
      <h1 class="page-title">Sản phẩm &amp; Lineage</h1>
      <p class="page-subtitle">Truy xuất nguồn gốc từ giống mè đến sản phẩm — <strong>${D.PRODUCTS.length} batch, ${num(units)} đơn vị</strong></p>
      <section class="count-cards">
        ${countCard(icon.leaf, "#eaf6ec", "#2f8a3a", D.HARVEST_BATCHES.length, "Harvest Batches")}
        ${countCard(icon.factory, "#f4efe6", "#7c6544", D.FACTORY_INTAKES.length, "Factory Intakes")}
        ${countCard(icon.flask, "#fbeee9", "#a0522d", D.PROCESSING_BATCHES.length, "Processing Batches")}
        ${countCard(icon.box, "#e6f0fb", "#0a2c8f", D.PRODUCTS.length, "Product Batches")}
      </section>
      <div class="filters">
        <button type="button" class="chip chip-lg${category === "all" ? " active" : ""}" data-category="all" aria-pressed="${category === "all"}">Tất cả sản phẩm <span class="count">${D.PRODUCTS.length}</span></button>
        ${D.CATEGORIES.map((c) => `<button type="button" class="chip chip-lg${category === c.id ? " active" : ""}" data-category="${c.id}" aria-pressed="${category === c.id}">${icon[c.icon]} ${esc(c.name)} <span class="count">${D.PRODUCTS.filter((p) => p.category === c.id).length}</span></button>`).join("")}
      </div>
      <section class="product-grid">${products.map(productCard).join("")}</section>`;
  }

  function countCard(svg, bg, color, value, label) {
    return `<article class="card count-card"><span class="count-icon" style="background:${bg};color:${color}">${svg}</span><div><strong style="color:${color}">${value}</strong><span>${label}</span></div></article>`;
  }

  function productCard(p) {
    const category = D.CATEGORIES.find((c) => c.id === p.category);
    const harvest = byId(D.HARVEST_BATCHES, p.harvest);
    const process = byId(D.PROCESSING_BATCHES, p.process);
    return `<article class="card product-card">
      <div class="product-main">
        <span class="product-icon ${p.category}">${icon[p.icon] || icon.box}</span>
        <div>
          <div class="product-tags"><span class="badge badge-real">${esc(category.name)}</span>${esc(p.sku)}</div>
          <h2 class="product-name">${esc(p.name)}</h2>
          <p>${esc(p.description)}</p>
        </div>
      </div>
      <div class="product-facts">
        <div><span>Sản xuất</span><strong>${esc(p.produced)}</strong></div>
        <div><span>Số lượng</span><strong>${esc(p.quantity)}</strong></div>
        <div><span>Thị trường</span><strong>${esc(p.market)}</strong></div>
        <div><span>Đóng gói</span><strong>${esc(p.packaging)}</strong></div>
      </div>
      <div class="product-price"><span><strong>${esc(p.price)}</strong><small>${esc(p.unit)}</small></span>
        <a class="btn" href="/qr/${esc(p.sku.toLowerCase())}" data-link>${icon.qr} Truy xuất</a></div>
      <details class="lineage">
        <summary><strong>Nguồn gốc:</strong> ${icon.leaf} ${esc(harvest.label)} <span>→</span> ${esc(process.label)} ${icon.down}</summary>
        ${lineageChain(p)}
      </details>
    </article>`;
  }

  function lineageChain(p) {
    const harvest = byId(D.HARVEST_BATCHES, p.harvest);
    const process = byId(D.PROCESSING_BATCHES, p.process);
    const intake = byId(D.FACTORY_INTAKES, process.intake);
    const variety = D.VARIETIES.find((v) => v.id === harvest.varietyId);
    const farms = harvest.farms.map((id) => findFarm(id)).filter(Boolean);
    return `<ol class="chain">
      <li><strong>Vùng trồng · Harvest ${esc(harvest.label)}</strong><span>${esc(variety.name)} · ${esc(harvest.season)} · ${esc(harvest.date)} · ${num(harvest.tonnes, 1)} t</span><br>
        <span>${farms.map((f) => `<a href="/farm/${esc(f.id)}" data-link>${esc(f.name)}</a>`).join(", ")}</span></li>
      <li><strong>Nhập nhà máy · ${esc(intake.id)}</strong><span>${esc(intake.factory)} · ${esc(intake.date)} · ẩm ${num(intake.moisture, 1)}% · tạp chất ${num(intake.impurity, 1)}%</span></li>
      <li><strong>Chế biến · ${esc(process.label)}</strong><span>${esc(process.method)} · ${esc(process.date)} · hiệu suất ${num(process.yieldPct, 1)}%</span></li>
      <li><strong>Thành phẩm · ${esc(p.sku)}</strong><span>${esc(p.quantity)} · ${esc(p.produced)}</span></li>
    </ol>`;
  }

  // ---- QR trace --------------------------------------------------------------------------
  function renderTrace(sku) {
    const p = D.PRODUCTS.find((item) => item.sku.toLowerCase() === sku);
    if (!p) {
      app.innerHTML = `${breadcrumbs([["Tổng quan", "/"], ["Sản phẩm", "/product"], ["Truy xuất nguồn gốc"]])}<p class="empty">Không tìm thấy mã truy xuất.</p>`;
      return;
    }
    const harvest = byId(D.HARVEST_BATCHES, p.harvest);
    const process = byId(D.PROCESSING_BATCHES, p.process);
    const intake = byId(D.FACTORY_INTAKES, process.intake);
    const variety = D.VARIETIES.find((v) => v.id === harvest.varietyId);
    const farms = harvest.farms.map((id) => findFarm(id)).filter(Boolean);
    const traceUrl = `${location.origin}/qr/${p.sku.toLowerCase()}`;

    app.innerHTML = `
      ${breadcrumbs([["Tổng quan", "/"], ["Sản phẩm", "/product"], ["Truy xuất nguồn gốc"]])}
      <article class="card trace-hero">
        <div>
          <span class="verified">${icon.check.replace("<svg", '<svg width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.5"')} Đã xác thực nguồn gốc</span>
          <h1>${esc(p.name)}</h1>
          <p class="muted">${esc(p.description)}</p>
          <div class="product-facts" style="padding:16px 0 0;grid-template-columns:repeat(4,minmax(0,1fr))">
            <div><span>Mã SKU</span><strong>${esc(p.sku)}</strong></div>
            <div><span>Sản xuất</span><strong>${esc(p.produced)}</strong></div>
            <div><span>Giống</span><strong>${esc(variety.name)}</strong></div>
            <div><span>Thị trường</span><strong>${esc(p.market)}</strong></div>
          </div>
        </div>
        <div class="qr-box"><div id="qr-code"></div><span>Quét để xem trang này</span></div>
      </article>
      <section class="trace-steps">
        <article class="card trace-step"><span class="kicker">${icon.leaf.replace("<svg", '<svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8"')} 1 · Vùng trồng</span><strong>Harvest ${esc(harvest.label)}</strong>
          <dl><dt>Vụ</dt><dd>${esc(harvest.season)}</dd><dt>Thu hoạch</dt><dd>${esc(harvest.date)}</dd><dt>Sản lượng</dt><dd>${num(harvest.tonnes, 1)} t</dd><dt>Nông trại</dt><dd>${farms.length}</dd></dl></article>
        <article class="card trace-step"><span class="kicker">${icon.factory.replace("<svg", '<svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8"')} 2 · Nhập nhà máy</span><strong>${esc(intake.id)}</strong>
          <dl><dt>Ngày</dt><dd>${esc(intake.date)}</dd><dt>Độ ẩm</dt><dd>${num(intake.moisture, 1)}%</dd><dt>Tạp chất</dt><dd>${num(intake.impurity, 1)}%</dd><dt>Nơi nhận</dt><dd>${esc(intake.factory.replace("Nhà máy OpenSesame ", ""))}</dd></dl></article>
        <article class="card trace-step"><span class="kicker">${icon.flask.replace("<svg", '<svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8"')} 3 · Chế biến</span><strong>${esc(process.label)}</strong>
          <dl><dt>Ngày</dt><dd>${esc(process.date)}</dd><dt>Phương pháp</dt><dd>${esc(process.method)}</dd><dt>Hiệu suất</dt><dd>${num(process.yieldPct, 1)}%</dd></dl></article>
        <article class="card trace-step"><span class="kicker">${icon.box.replace("<svg", '<svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8"')} 4 · Thành phẩm</span><strong>${esc(p.sku)}</strong>
          <dl><dt>Số lượng</dt><dd>${esc(p.quantity)}</dd><dt>Đóng gói</dt><dd>${esc(p.packaging)}</dd><dt>Giá</dt><dd>${esc(p.price)} ${esc(p.unit)}</dd></dl></article>
      </section>
      <section class="grid-main">
        <article class="card card-pad"><h2 class="card-title" style="margin-bottom:12px">Nông trại nguồn</h2><div class="map map-farm" id="trace-map" role="region" aria-label="Bản đồ nông trại nguồn"></div></article>
        <article class="card card-pad">
          <h2 class="card-title" style="margin-bottom:6px">Danh sách nông trại</h2>
          <ul class="task-list">${farms.map((f) => `<li class="task-item"><a href="/farm/${esc(f.id)}" data-link><strong>${esc(f.name)}</strong></a>${statusPill(f.status)}<span>${esc(f.code)} · ${esc(f.district)}, ${esc(f.provinceName)} · ${num(f.area, 1)} ha</span></li>`).join("")}</ul>
        </article>
      </section>`;

    const qrBox = document.querySelector("#qr-code");
    if (window.qrcode) {
      const qr = window.qrcode(0, "M");
      qr.addData(traceUrl);
      qr.make();
      qrBox.innerHTML = qr.createSvgTag({ cellSize: 4, margin: 2, scalable: true });
    } else {
      qrBox.innerHTML = `<p style="max-width:150px;word-break:break-all">${esc(traceUrl)}</p>`;
    }
    const map = createMap(document.querySelector("#trace-map"));
    if (map) {
      tile("voyager").addTo(map);
      const markers = farms.map((f) => farmMarker(f).addTo(map));
      map.fitBounds(L.featureGroup(markers).getBounds(), { padding: [50, 50], maxZoom: 11 });
    }
  }

  // ---- router ----------------------------------------------------------------------------
  function breadcrumbs(items) {
    return `<nav class="breadcrumbs" aria-label="Breadcrumb">${items.map(([label, href], i) => {
      const last = i === items.length - 1;
      const item = last ? `<strong>${esc(label)}</strong>` : `<a href="${href}" data-link>${esc(label)}</a>`;
      return item + (last ? "" : icon.chevron);
    }).join("")}</nav>`;
  }

  const ROUTES = [
    { name: "overview", tab: "region", pattern: /^\/$/, render: () => renderOverview() },
    { name: "block", tab: "region", pattern: /^\/farm\/([^/]+)\/block\/([^/]+)\/?$/, keys: ["farmId", "blockId"], render: (p) => renderBlock(p.farmId, p.blockId) },
    { name: "farm", tab: "region", pattern: /^\/farm\/([^/]+)\/?$/, keys: ["farmId"], render: (p) => renderFarm(p.farmId) },
    { name: "product", tab: "product", pattern: /^\/product\/?$/, render: () => renderProducts(state.productCategory || "all") },
    { name: "qr", tab: "product", pattern: /^\/qr\/([^/]+)\/?$/, keys: ["sku"], render: (p) => renderTrace(p.sku) },
  ];

  function currentRoute() {
    const path = decodeURIComponent(location.pathname);
    for (const route of ROUTES) {
      const match = path.match(route.pattern);
      if (match) {
        // Farm ids and SKUs are lowercase in URLs; block ids are upper case (B01).
        const params = Object.fromEntries((route.keys || []).map((key, i) => [
          key, key === "blockId" ? match[i + 1].toUpperCase() : match[i + 1].toLowerCase()]));
        return { ...route, params };
      }
    }
    return { name: "notfound", tab: null, params: {}, render: () => { app.innerHTML = '<p class="empty">Không tìm thấy trang. <a href="/" data-link>Về tổng quan</a></p>'; } };
  }

  function rerender({ scroll = false } = {}) {
    state.maps.forEach((map) => map.remove());
    state.maps = [];
    const route = currentRoute();
    document.querySelectorAll(".tab").forEach((tab) => {
      const active = tab.dataset.tab === route.tab;
      tab.classList.toggle("active", active);
      if (active) tab.setAttribute("aria-current", "page"); else tab.removeAttribute("aria-current");
    });
    route.render(route.params);
    const heading = app.querySelector("h1")?.textContent.trim();
    document.title = heading ? `${heading} · OpenSesame` : "OpenSesame — Production Digital Twin cho vùng trồng mè";
    if (scroll) {
      window.scrollTo(0, 0);
      app.focus({ preventScroll: true }); // screen readers start reading the new page
    }
  }

  function navigate(href) {
    if (href === location.pathname) return;
    history.pushState({}, "", href);
    rerender({ scroll: true });
  }

  document.addEventListener("click", (event) => {
    const link = event.target.closest("a[data-link]");
    if (link && !event.metaKey && !event.ctrlKey && !event.shiftKey && link.origin === location.origin) {
      event.preventDefault();
      navigate(link.pathname);
      return;
    }
    const row = event.target.closest("tr[data-href]");
    if (row) {
      navigate(row.dataset.href);
      return;
    }
    const filter = event.target.closest("[data-variety], [data-program], [data-region], [data-category]");
    if (!filter) return;
    const f = state.filters;
    if (filter.dataset.variety) { f.variety = filter.dataset.variety; f.program = null; }
    if (filter.dataset.program) {
      const same = f.program === filter.dataset.program;
      f.program = same ? null : filter.dataset.program;
      f.variety = same ? "all" : D.PROGRAMS.find((p) => p.id === f.program).varietyId;
    }
    if (filter.dataset.region) f.region = filter.dataset.region;
    if (filter.dataset.category) state.productCategory = filter.dataset.category;
    const y = window.scrollY;
    rerender();
    window.scrollTo(0, y);
  });
  window.addEventListener("popstate", () => rerender());

  rerender();
  loadRealFarm();
  loadWeather();
})();
