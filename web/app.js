const chart = document.querySelector("#weather-chart");
const todayLabel = document.querySelector("#today-label");
const toast = document.querySelector("#toast");
const humidityBars = document.querySelector("#humidity-bars");
let monitorData = null;
let toastTimer;

const formatDate = (value, options = { month: "short", day: "numeric" }) => {
  if (!value) return "—";
  return new Intl.DateTimeFormat("en", { timeZone: "UTC", ...options }).format(new Date(`${value}T00:00:00Z`));
};

function showToast(message) {
  toast.textContent = message;
  toast.classList.add("show");
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => toast.classList.remove("show"), 4500);
}

function setMetric(id, value, precision = 1) {
  const element = document.querySelector(`#${id}`);
  const unit = element.querySelector("span");
  const formatted = typeof value === "number" ? value.toFixed(precision) : "—";
  element.firstChild.textContent = formatted;
  if (unit) unit.hidden = typeof value !== "number";
}

function renderChart(series, range) {
  const days = range === 7 ? series.slice(-7) : series.slice(-30);
  const buckets = range === 7
    ? days.map((item) => ({ label: formatDate(item.date), temperature: item.temperature_c, rainfall: item.rainfall_mm }))
    : Array.from({ length: Math.ceil(days.length / 5) }, (_, index) => {
        const group = days.slice(index * 5, (index + 1) * 5);
        const temperatures = group.map((item) => item.temperature_c).filter(Number.isFinite);
        const rain = group.map((item) => item.rainfall_mm).filter(Number.isFinite);
        return {
          label: group[0] ? formatDate(group[0].date) : "",
          temperature: temperatures.length ? temperatures.reduce((sum, value) => sum + value, 0) / temperatures.length : null,
          rainfall: rain.length ? rain.reduce((sum, value) => sum + value, 0) : null,
        };
      });

  const usable = buckets.filter((item) => Number.isFinite(item.temperature) || Number.isFinite(item.rainfall));
  if (!usable.length) {
    chart.textContent = "No weather observations are available for this period.";
    chart.setAttribute("aria-label", "No weather observations available");
    return;
  }

  const width = 620;
  const height = 143;
  const left = 31;
  const right = 8;
  const top = 10;
  const bottom = 22;
  const plotWidth = width - left - right;
  const plotHeight = height - top - bottom;
  const maxTemperature = 40;
  const rainfallValues = usable.map((item) => item.rainfall).filter(Number.isFinite);
  const maxRainfall = Math.max(10, ...rainfallValues) * 1.15;
  const xAt = (index) => left + (plotWidth * index) / Math.max(1, buckets.length - 1);
  const yTemperature = (value) => top + (1 - value / maxTemperature) * plotHeight;
  const yRain = (value) => (value / maxRainfall) * plotHeight * 0.58;
  const gridValues = [0, 10, 20, 30, 40];

  const grid = gridValues.map((value) => {
    const y = yTemperature(value);
    return `<line class="chart-grid-line" x1="${left}" y1="${y}" x2="${width - right}" y2="${y}"/><text class="chart-axis-label" x="0" y="${y + 3}">${value}°</text>`;
  }).join("");

  const barWidth = Math.min(30, plotWidth / buckets.length * 0.48);
  const bars = buckets.map((item, index) => {
    if (!Number.isFinite(item.rainfall)) return "";
    const barHeight = yRain(item.rainfall);
    const x = xAt(index) - barWidth / 2;
    const y = top + plotHeight - barHeight;
    const current = index === buckets.length - 1 ? " current" : "";
    return `<rect class="chart-bar${current}" x="${x}" y="${y}" width="${barWidth}" height="${barHeight}" rx="3"><title>${item.label}: ${item.rainfall.toFixed(1)} mm rain</title></rect>`;
  }).join("");

  const points = buckets.map((item, index) => Number.isFinite(item.temperature)
    ? [xAt(index), yTemperature(item.temperature), index, item]
    : null).filter(Boolean);
  const line = points.map(([x, y], index) => `${index === 0 ? "M" : "L"} ${x} ${y}`).join(" ");
  const area = points.length
    ? `${line} L ${points.at(-1)[0]} ${top + plotHeight} L ${points[0][0]} ${top + plotHeight} Z`
    : "";
  const pointMarks = points.map(([x, y, index, item]) =>
    `<circle class="chart-point" cx="${x}" cy="${y}" r="3"><title>${item.label}: ${item.temperature.toFixed(1)}°C</title></circle>`
  ).join("");
  const labelStep = Math.max(1, Math.ceil(buckets.length / 7));
  const labels = buckets.map((item, index) =>
    index % labelStep === 0 || index === buckets.length - 1
      ? `<text class="chart-axis-label" x="${xAt(index)}" y="${height - 3}" text-anchor="middle">${item.label}</text>`
      : ""
  ).join("");

  chart.innerHTML = `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" aria-hidden="true">
    <defs><linearGradient id="chartFill" x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stop-color="#91ad7d" stop-opacity=".20"/><stop offset="100%" stop-color="#91ad7d" stop-opacity="0"/></linearGradient></defs>
    ${grid}${bars}${area ? `<path class="chart-temp-area" d="${area}"/>` : ""}${line ? `<path class="chart-temp-line" d="${line}"/>` : ""}${pointMarks}${labels}
  </svg>`;
  chart.setAttribute("aria-label", `Earth Engine temperature and rainfall for the last ${range} days`);
}

function updateStatus({ badge, source, detail }) {
  document.querySelector("#data-badge").lastChild.textContent = ` ${badge}`;
  document.querySelector("#source-status").textContent = source;
  document.querySelector("#source-detail").textContent = detail;
}

function showSoilAndTerrain(data) {
  const soil = data.soil;
  const terrain = data.terrain;
  document.querySelector("#soil-ph").textContent = soil?.ph?.toFixed(1) ?? "—";
  document.querySelector("#soil-sand").textContent = soil?.sand_g_kg?.toFixed(0) ?? "—";
  document.querySelector("#soil-clay").textContent = soil?.clay_g_kg?.toFixed(0) ?? "—";
  document.querySelector("#terrain-elevation").textContent = terrain?.elevation_m?.toFixed(0) ?? "—";

  const satellite = data.satellite;
  const latest = Array.isArray(satellite) ? satellite.at(-1) : null;
  if (latest) {
    setMetric("ndvi-value", latest.ndvi, 2);
    document.querySelector("#ndvi-date").textContent = formatDate(latest.date);
    document.querySelector("#ndvi-marker").style.left = `${Math.max(0, Math.min(100, (latest.ndvi + 1) * 50))}%`;
    document.querySelector("#satellite-title").textContent = `Latest NDVI · ${latest.ndvi.toFixed(2)}`;
    document.querySelector("#satellite-detail").textContent = `Sentinel-2 observation from ${formatDate(latest.date)}. NDVI is a vegetation index, not a direct crop-health diagnosis.`;
  } else {
    setMetric("ndvi-value", null);
    document.querySelector("#ndvi-date").textContent = data.errors?.satellite ? "Unavailable" : "No clear image";
    document.querySelector("#satellite-title").textContent = data.errors?.satellite
      ? "Satellite data unavailable"
      : "No clear imagery in this period";
  }
}

function updateDashboard(data) {
  monitorData = data;
  const latest = data.weather.latest;
  setMetric("temperature-value", latest.temperature_c);
  setMetric("rainfall-value", latest.rainfall_mm);
  setMetric("humidity-value", latest.humidity_pct, 0);
  document.querySelector("#temperature-date").textContent = latest.date ? formatDate(latest.date) : "Unavailable";
  document.querySelector("#rainfall-date").textContent = latest.date ? formatDate(latest.date) : "Unavailable";
  document.querySelector("#humidity-date").textContent = latest.date ? formatDate(latest.date) : "Unavailable";
  document.querySelector("#rainfall-progress").style.width = `${Math.min(100, Math.max(0, (latest.rainfall_mm || 0) / 50 * 100))}%`;
  document.querySelector("#humidity-bars").innerHTML = Array.from({ length: 20 }, (_, index) =>
    `<i class="${Number.isFinite(latest.humidity_pct) && index < Math.round(latest.humidity_pct / 5) ? "filled" : ""}"></i>`
  ).join("");
  const temperatures = data.weather.series.slice(-7).map((item) => item.temperature_c).filter(Number.isFinite);
  if (temperatures.length > 1) {
    const min = Math.min(...temperatures) - 1;
    const max = Math.max(...temperatures) + 1;
    const points = temperatures.map((value, index) => [
      (120 * index) / (temperatures.length - 1),
      25 - ((value - min) / Math.max(1, max - min)) * 20,
    ]);
    const path = points.map(([x, y], index) => `${index ? "L" : "M"}${x} ${y}`).join(" ");
    document.querySelector("#temperature-spark").innerHTML =
      `<svg viewBox="0 0 120 30" preserveAspectRatio="none" aria-hidden="true"><path d="${path}"/></svg>`;
  }
  document.querySelector("#chart-source").innerHTML = `<span class="chart-foot-dot"></span> ERA5-Land · data through ${formatDate(latest.date)}`;
  document.querySelector(".updated-label").innerHTML = `<span class="live-dot"></span> Retrieved ${new Intl.DateTimeFormat("en", { hour: "numeric", minute: "2-digit", timeZone: "UTC", timeZoneName: "short" }).format(new Date(data.generated_at))}`;
  showSoilAndTerrain(data);

  const optionalErrors = Object.keys(data.errors || {});
  updateStatus(optionalErrors.length
    ? { badge: "PARTIAL DATA", source: "Earth Engine", detail: "Some datasets unavailable" }
    : { badge: "EARTH ENGINE", source: "Earth Engine", detail: "Satellite + reanalysis" });
  document.querySelector("#today-label").textContent = `· latest ${formatDate(latest.date)}`;
  document.querySelector("#field .field-stats > div:nth-child(2) strong").textContent =
    `${data.farm.latitude.toFixed(2)}° N, ${data.farm.longitude.toFixed(2)}° E`;
  document.querySelector("#farm-coordinates").textContent =
    `${data.farm.latitude.toFixed(2)}° N, ${data.farm.longitude.toFixed(2)}° E`;
  document.querySelector("#chart-source").setAttribute("title", "Reanalysis estimates, not on-field sensors");
  renderChart(data.weather.series, Number(document.querySelector(".range-button.selected").dataset.range));
  if (optionalErrors.length) {
    showToast(`Weather loaded; unavailable datasets: ${optionalErrors.join(", ")}.`);
  }
}

async function loadData() {
  const button = document.querySelector("#refresh-button");
  button.disabled = true;
  button.classList.add("loading");
  updateStatus({ badge: "CONNECTING", source: "Earth Engine", detail: "Loading field data…" });
  try {
    const response = await fetch("/api/monitor?days=30", { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || `Request failed (${response.status})`);
    updateDashboard(payload);
  } catch (error) {
    updateStatus({ badge: "NOT CONNECTED", source: "Earth Engine", detail: "Check server configuration" });
    document.querySelector("#chart-source").textContent = "Earth Engine data unavailable";
    chart.textContent = "Connect Earth Engine to load weather history.";
    showToast(error.message);
  } finally {
    button.disabled = false;
    button.classList.remove("loading");
  }
}

document.querySelectorAll(".range-button").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelector(".range-button.selected")?.classList.remove("selected");
    button.classList.add("selected");
    if (monitorData) {
      renderChart(monitorData.weather.series, Number(button.dataset.range));
    }
  });
});

document.querySelector("#refresh-button").addEventListener("click", loadData);
document.querySelector("#map-button").addEventListener("click", () => {
  if (!monitorData) {
    showToast("Farm coordinates are unavailable until Earth Engine connects.");
    return;
  }
  const { latitude, longitude } = monitorData.farm;
  showToast(`Farm coordinates: ${latitude.toFixed(2)}° N, ${longitude.toFixed(2)}° E.`);
});

todayLabel.textContent = "· connecting to Earth Engine";
loadData();
