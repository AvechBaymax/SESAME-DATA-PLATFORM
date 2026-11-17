/* OpenSesame data: the region's mock farms, blocks, products and batches.
 *
 * Every mock value is generated deterministically from the farm code, so the
 * page shows the same numbers on every load. The real farm (from /api/monitor)
 * is added at run time by app.js and flagged `real: true`.
 */
(function () {
  const TODAY = new Date(Date.UTC(2026, 10, 17)); // reference date for the mock season
  const DAY = 86400000;

  // ---- seeded random -------------------------------------------------------------------
  function seeded(text) {
    let h = 1779033703 ^ text.length;
    for (let i = 0; i < text.length; i += 1) {
      h = Math.imul(h ^ text.charCodeAt(i), 3432918353);
      h = (h << 13) | (h >>> 19);
    }
    let a = h >>> 0;
    return () => {
      a = (a + 0x6d2b79f5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  const between = (rand, min, max) => min + rand() * (max - min);
  const pick = (rand, list) => list[Math.floor(rand() * list.length)];

  // ---- reference data ------------------------------------------------------------------
  const PROVINCES = [
    { id: "tayninh", name: "Tây Ninh", lat: 11.25, lon: 106.2 },
    { id: "dongthap", name: "Đồng Tháp", lat: 10.46, lon: 105.55 },
    { id: "vinhlong", name: "Vĩnh Long", lat: 10.05, lon: 105.96 },
    { id: "longan", name: "Long An", lat: 10.64, lon: 106.07 },
    { id: "angiang", name: "An Giang", lat: 10.5, lon: 105.08 },
    { id: "tiengiang", name: "Tiền Giang", lat: 10.38, lon: 106.28 },
    { id: "hcm", name: "TP. Hồ Chí Minh", lat: 10.98, lon: 106.45 },
  ];

  const VARIETIES = [
    { id: "vs6", code: "VS6", name: "Mè VS6 — Tây Ninh" },
    { id: "vs1", code: "VS1", name: "Mè VS1 — Cao Lãnh" },
  ];

  const PROGRAMS = [
    { id: "prog-a", name: "VS6 Tây Ninh", varietyId: "vs6", region: "Tây Ninh", season: "2026 Autumn", commitment: 520 },
    { id: "prog-b", name: "VS1 Cao Lãnh", varietyId: "vs1", region: "Cao Lãnh", season: "2026 Autumn", commitment: 280 },
  ];

  const BLOCK_VARIETIES = {
    tayninh: ["Mè Tây Ninh VS-6", "Mè Trảng Bàng VS-6"],
    dongthap: ["Mè Cao Lãnh VS-1", "Mè Lấp Vò VS-1"],
    vinhlong: ["Mè Vĩnh Long VL-03"],
    longan: ["Mè Long An LA-04"],
    angiang: ["Mè An Giang AG-05"],
    tiengiang: ["Mè Tiền Giang TG-02"],
  };

  // Growth stages by days after sowing (DAS), aligned with config/sesame_rules.json
  // (initial 0-14, development 15-39, mid-season/flowering 40-64, late 65-74, harvest 75).
  // The guide gives no season length; 75 days is the assumption used everywhere.
  const STAGES = [
    { id: "seedling", name: "Cây con", from: 0 },
    { id: "vegetative", name: "Thân lá", from: 15 },
    { id: "flowering", name: "Ra hoa", from: 40 },
    { id: "podding", name: "Kết quả", from: 65 },
    { id: "ripening", name: "Chín", from: 72 },
  ];
  const SEASON_DAYS = 75;
  const stageFor = (das) => [...STAGES].reverse().find((stage) => das >= stage.from) || STAGES[0];

  const STATUS = {
    stable: { name: "Ổn định", color: "#2f8a3a" },
    watch: { name: "Cần theo dõi", color: "#d49a2a" },
    risk: { name: "Có rủi ro", color: "#d9483b" },
    nodata: { name: "Thiếu dữ liệu", color: "#9a9a92" },
  };

  // ---- farms (mock) ----------------------------------------------------------------------
  // forecast in t, harvest window as dd/mm/yyyy, log = activity-log completeness %.
  const FARM_SPECS = [
    { code: "TG-CGO-13", name: "Liên kết Chợ Gạo", owner: "Tổ hợp tác Chợ Gạo", commune: "Xã Lương Hòa Lạc", district: "Chợ Gạo", province: "tiengiang", lat: 10.354, lon: 106.456, area: 9.4, blocks: 5, forecast: 15, harvest: ["31/12/2026", "06/01/2027"], log: 91, status: "watch" },
    { code: "VL-TRN-11", name: "Nhóm Trà Ôn", owner: "Nhóm nông dân Trà Ôn", commune: "Xã Thiện Mỹ", district: "Trà Ôn", province: "vinhlong", lat: 9.965, lon: 105.92, area: 12.1, blocks: 5, forecast: 20, harvest: ["29/12/2026", "09/01/2027"], log: 97, status: "watch" },
    { code: "LA-TTH-04", name: "Cụm mè Thạnh Hóa", owner: "HTX Thạnh Hóa", commune: "Xã Thạnh Phước", district: "Thạnh Hóa", province: "longan", lat: 10.66, lon: 106.17, area: 12.5, blocks: 6, forecast: 21, harvest: ["17/12/2026", "19/01/2027"], log: 96, status: "watch" },
    { code: "TG-CLY-06", name: "Nhóm liên kết Cai Lậy", owner: "Nhóm liên kết Cai Lậy", commune: "Xã Mỹ Thành Nam", district: "Cai Lậy", province: "tiengiang", lat: 10.41, lon: 106.11, area: 17.1, blocks: 7, forecast: 28, harvest: ["21/12/2026", "22/01/2027"], log: 98, status: "risk", nodataBlock: 4 },
    { code: "TN-DMC-10", name: "Mè Dương Minh Châu", owner: "HTX Dương Minh Châu", commune: "Xã Suối Đá", district: "Dương Minh Châu", province: "tayninh", lat: 11.37, lon: 106.25, area: 16.1, blocks: 6, forecast: 28, harvest: ["22/12/2026", "16/01/2027"], log: 100, status: "watch" },
    { code: "TN-TBG-01", name: "Nông trại mè Trảng Bàng", owner: "HTX Nông nghiệp Trảng Bàng", commune: "Phường Trảng Bàng", district: "Trảng Bàng", province: "tayninh", lat: 11.065, lon: 106.325, area: 50.5, blocks: 22, forecast: 81, harvest: ["13/12/2026", "31/01/2027"], log: 95, status: "watch", flagship: true },
    { code: "DT-TPB-08", name: "Liên kết Thanh Bình", owner: "HTX Thanh Bình", commune: "Xã Tân Thạnh", district: "Thanh Bình", province: "dongthap", lat: 10.56, lon: 105.48, area: 13.7, blocks: 6, forecast: 22, harvest: ["27/12/2026", "19/01/2027"], log: 97, status: "risk" },
    { code: "AG-TTO-05", name: "Vùng mè Tri Tôn", owner: "Tổ hợp tác Tri Tôn", commune: "Xã Lương Phi", district: "Tri Tôn", province: "angiang", lat: 10.42, lon: 104.99, area: 12.3, blocks: 6, forecast: 20, harvest: ["25/12/2026", "26/01/2027"], log: 94, status: "watch" },
    { code: "DT-LVO-02", name: "Liên kết mè Lấp Vò", owner: "HTX Lấp Vò", commune: "Xã Bình Thạnh Trung", district: "Lấp Vò", province: "dongthap", lat: 10.36, lon: 105.52, area: 22.9, blocks: 9, forecast: 35, harvest: ["22/12/2026", "20/01/2027"], log: 95, status: "risk" },
    { code: "VL-TBH-03", name: "Vườn mè Tam Bình", owner: "Hộ ông Lê Văn T.", commune: "Xã Mỹ Lộc", district: "Tam Bình", province: "vinhlong", lat: 10.05, lon: 105.98, area: 17.3, blocks: 8, forecast: 27, harvest: ["02/01/2027", "21/01/2027"], log: 96, status: "watch" },
    { code: "LA-TTA-09", name: "Vườn mè Tân Thạnh", owner: "Hộ bà Trần Thị L.", commune: "Xã Hậu Thạnh Đông", district: "Tân Thạnh", province: "longan", lat: 10.61, lon: 105.95, area: 13.2, blocks: 5, forecast: 20, harvest: ["29/12/2026", "14/01/2027"], log: 100, status: "watch" },
    { code: "TN-GDA-07", name: "Vườn mè Gò Dầu", owner: "Hộ ông Nguyễn Văn H.", commune: "Xã Thanh Phước", district: "Gò Dầu", province: "tayninh", lat: 11.09, lon: 106.26, area: 12.1, blocks: 5, forecast: 20, harvest: ["01/01/2027", "16/01/2027"], log: 96, status: "stable" },
    { code: "AG-CTH-12", name: "Vùng mè Châu Thành AG", owner: "HTX Châu Phú", commune: "Xã Bình Mỹ", district: "Châu Phú", province: "angiang", lat: 10.57, lon: 105.18, area: 13.2, blocks: 6, forecast: 21, harvest: ["03/01/2027", "14/01/2027"], log: 100, status: "stable" },
  ];

  const PROGRAM_BY_PROVINCE = { dongthap: "prog-b" };
  const ISSUES = {
    watch: ["Hạn nước kéo dài {n} ngày trong vụ", "Độ ẩm đất dưới ngưỡng {n} ngày"],
    risk: ["Ngập úng sau mưa lớn {n} ngày", "Sâu ăn lá vượt ngưỡng, cần phun", "Mưa lớn giai đoạn ra hoa {n} ngày"],
  };

  // Blocks laid out as a grid of near-square plots around the farm centre.
  function makeBlocks(spec) {
    const rand = seeded(spec.code);
    const count = spec.blocks;
    const weights = Array.from({ length: count }, () => between(rand, 0.75, 1.25));
    const weightSum = weights.reduce((a, b) => a + b, 0);
    const cols = Math.ceil(Math.sqrt(count * 1.4));
    const rows = Math.ceil(count / cols);
    const blockSide = Math.sqrt((spec.area * 10000) / count); // metres
    const mPerDegLat = 111320;
    const mPerDegLon = 111320 * Math.cos((spec.lat * Math.PI) / 180);
    const names = BLOCK_VARIETIES[spec.province] || ["Mè VS-6"];
    const statuses = blockStatuses(spec, rand);

    const blocks = weights.map((weight, index) => {
      const area = (spec.area * weight) / weightSum;
      const col = index % cols;
      const row = Math.floor(index / cols);
      const x0 = (col - cols / 2) * blockSide;
      const y0 = (rows / 2 - row - 1) * blockSide;
      const toLatLon = (x, y) => [spec.lat + y / mPerDegLat, spec.lon + x / mPerDegLon];
      const pad = blockSide * 0.03;
      const polygon = [
        toLatLon(x0 + pad, y0 + pad), toLatLon(x0 + blockSide - pad, y0 + pad),
        toLatLon(x0 + blockSide - pad, y0 + blockSide - pad), toLatLon(x0 + pad, y0 + blockSide - pad),
      ];
      const das = Math.round(between(rand, 34, 72));
      const status = statuses[index];
      const issueDays = Math.round(between(rand, 6, 25));
      return {
        id: `B${String(index + 1).padStart(2, "0")}`,
        variety: pick(rand, names),
        area,
        das,
        stage: stageFor(das),
        gddPct: Math.min(99, Math.round((das / SEASON_DAYS) * 100 * between(rand, 0.6, 1.05))),
        moisture: Math.round(between(rand, 30, 52)),
        log: Math.min(100, Math.round(spec.log + between(rand, -6, 5))),
        quality: Math.round(between(rand, 62, 96)),
        status,
        issue: ISSUES[status] ? pick(rand, ISSUES[status]).replace("{n}", issueDays) : status === "nodata"
          ? "Mất tín hiệu node IoT 3 ngày" : null,
        polygon,
        weight,
      };
    });
    // Scale block forecasts so they add up to the farm forecast.
    const raw = blocks.map((block) => block.area * between(rand, 1.3, 1.9));
    const rawSum = raw.reduce((a, b) => a + b, 0);
    blocks.forEach((block, index) => { block.forecast = (raw[index] / rawSum) * spec.forecast; });
    return blocks;
  }

  function blockStatuses(spec, rand) {
    const shares = { stable: [0.85, 0.15, 0], watch: [0.5, 0.5, 0], risk: [0.35, 0.4, 0.25] }[spec.status];
    const list = Array.from({ length: spec.blocks }, () => {
      const r = rand();
      if (r < shares[0]) return "stable";
      return r < shares[0] + shares[1] ? "watch" : "risk";
    });
    if (spec.status === "risk" && !list.includes("risk")) list[list.length - 1] = "risk";
    if (spec.status === "watch" && !list.includes("watch")) list[0] = "watch";
    if (spec.nodataBlock) list[spec.nodataBlock - 1] = "nodata";
    return list;
  }

  const parseDate = (text) => {
    const [d, m, y] = text.split("/").map(Number);
    return new Date(Date.UTC(y, m - 1, d));
  };

  const FARMS = FARM_SPECS.map((spec) => {
    const province = PROVINCES.find((p) => p.id === spec.province);
    const programId = PROGRAM_BY_PROVINCE[spec.province] || "prog-a";
    const program = PROGRAMS.find((p) => p.id === programId);
    const blocks = makeBlocks(spec);
    const rand = seeded(`${spec.code}:farm`);
    const nodes = Math.max(2, Math.ceil(spec.blocks / 4));
    return {
      id: spec.code.toLowerCase(),
      code: spec.code,
      name: spec.name,
      owner: spec.owner,
      commune: spec.commune,
      district: spec.district,
      provinceId: province.id,
      provinceName: province.name,
      lat: spec.lat,
      lon: spec.lon,
      area: spec.area,
      forecast: spec.forecast,
      forecastRange: [Math.round(spec.forecast * 0.92), Math.round(spec.forecast * 1.07)],
      confidence: Math.round(between(rand, 88, 97)),
      harvestStart: parseDate(spec.harvest[0]),
      harvestEnd: parseDate(spec.harvest[1]),
      log: spec.log,
      status: spec.status,
      flagship: Boolean(spec.flagship),
      programId,
      varietyId: program.varietyId,
      nodes,
      nodesOnline: nodes - blocks.filter((b) => b.status === "nodata").length,
      streakDays: Math.round(between(rand, 5, 24)),
      blocks,
      real: false,
    };
  });

  // ---- products & lineage ------------------------------------------------------------------
  const HARVEST_BATCHES = [
    { id: "HB-001", label: "#001", varietyId: "vs6", season: "Hè Thu 2026", date: "12/09/2026", tonnes: 64.2,
      farms: ["tn-tbg-01", "tn-dmc-10", "tn-gda-07"] },
    { id: "HB-002", label: "#002", varietyId: "vs6", season: "Hè Thu 2026", date: "20/09/2026", tonnes: 48.5,
      farms: ["la-tth-04", "tg-cly-06", "vl-tbh-03", "ag-tto-05"] },
    { id: "HB-003", label: "#003", varietyId: "vs1", season: "Hè Thu 2026", date: "26/09/2026", tonnes: 31.8,
      farms: ["dt-lvo-02", "dt-tpb-08"] },
  ];
  const FACTORY_INTAKES = [
    { id: "FI-001", harvest: "HB-001", date: "18/09/2026", factory: "Nhà máy OpenSesame Tây Ninh", moisture: 6.4, impurity: 0.7 },
    { id: "FI-002", harvest: "HB-002", date: "26/09/2026", factory: "Nhà máy OpenSesame Tây Ninh", moisture: 6.8, impurity: 0.9 },
    { id: "FI-003", harvest: "HB-003", date: "02/10/2026", factory: "Nhà máy OpenSesame Cao Lãnh", moisture: 6.1, impurity: 0.5 },
  ];
  const PROCESSING_BATCHES = [
    { id: "PB-001", label: "Process #001", intake: "FI-001", date: "24/10/2026", method: "Ép lạnh & rang vàng", yieldPct: 47.2 },
    { id: "PB-002", label: "Process #002", intake: "FI-002", date: "01/11/2026", method: "Rang thủ công", yieldPct: 45.8 },
    { id: "PB-003", label: "Process #003", intake: "FI-003", date: "08/11/2026", method: "Ép lạnh premium", yieldPct: 49.5 },
  ];
  const CATEGORIES = [
    { id: "oil", name: "Dầu mè", icon: "bottle" },
    { id: "spice", name: "Gia vị", icon: "grain" },
    { id: "sauce", name: "Sốt", icon: "jar" },
  ];
  const PRODUCTS = [
    { sku: "SKU-OIL-500", category: "oil", icon: "bottle", name: "Dầu mè nguyên chất VS6", description: "Dầu mè ép lạnh nguyên chất từ giống VS6 Tây Ninh. Hương thơm đặc trưng, màu vàng trong sáng.", produced: "28/10/2026", quantity: "4.800 chai 500ml", units: 4800, market: "Việt Nam / Nhật Bản", packaging: "Chai thủy tinh nắp vàng", price: "185k", unit: "VNĐ/500ml", harvest: "HB-001", process: "PB-001" },
    { sku: "SKU-OIL-250", category: "oil", icon: "bottle", name: "Dầu mè rang vàng VS6", description: "Dầu mè rang vàng thủ công, hương đậm đà. Phù hợp nấu lẩu, ướp thịt.", produced: "05/11/2026", quantity: "6.200 chai 250ml", units: 6200, market: "Hàn Quốc / Việt Nam", packaging: "Chai thủy tinh nắp đen", price: "98k", unit: "VNĐ/250ml", harvest: "HB-002", process: "PB-002" },
    { sku: "SKU-OIL-VS1", category: "oil", icon: "sparkle", name: "Dầu mè VS1 Premium", description: "Dầu mè từ giống VS1 Cao Lãnh hàm lượng dầu cao nhất (49.5%). Dòng xuất khẩu cao cấp.", produced: "12/11/2026", quantity: "2.400 chai 300ml", units: 2400, market: "EU / Nhật Bản", packaging: "Chai thủy tinh dòng premium", price: "265k", unit: "VNĐ/300ml", harvest: "HB-003", process: "PB-003" },
    { sku: "SKU-SEA-80", category: "spice", icon: "grain", name: "Mè rang giòn VS6 — gói 80g", description: "Mè trắng VS6 rang giòn thơm. Rắc trực tiếp lên cơm, salad, phở, bún.", produced: "29/10/2026", quantity: "12.000 gói 80g", units: 12000, market: "Việt Nam", packaging: "Túi zip foil bạc", price: "38k", unit: "VNĐ/80g", harvest: "HB-001", process: "PB-001" },
    { sku: "SKU-SEA-MIX", category: "spice", icon: "shaker", name: "Muối mè Gomasio VS6", description: "Gomasio — muối mè rang truyền thống Nhật Bản. Tỉ lệ mè:muối = 10:1 theo công thức macrobiotic.", produced: "06/11/2026", quantity: "8.500 hũ 120g", units: 8500, market: "Nhật Bản / Hàn Quốc", packaging: "Hũ thủy tinh nhỏ nắp thiếc", price: "72k", unit: "VNĐ/120g", harvest: "HB-002", process: "PB-002" },
    { sku: "SKU-SAU-200", category: "sauce", icon: "jar", name: "Sốt mè rang VS6 — 200ml", description: "Sốt mè rang đậm vị — pha trộn dầu mè VS6, mè rang và gia vị tự nhiên. Dùng làm nước chấm, sốt salad.", produced: "30/10/2026", quantity: "7.200 chai 200ml", units: 7200, market: "Việt Nam / Hàn Quốc", packaging: "Chai nhựa PET nắp flip-top", price: "52k", unit: "VNĐ/200ml", harvest: "HB-001", process: "PB-001" },
    { sku: "SKU-SAU-VS1", category: "sauce", icon: "jar", name: "Sốt tahini VS1 — 180g", description: "Tahini nguyên chất từ mè VS1 Cao Lãnh — không phụ gia, không đường. Chuẩn vị Trung Đông.", produced: "13/11/2026", quantity: "3.600 hũ 180g", units: 3600, market: "EU / Việt Nam", packaging: "Hũ thủy tinh cao cấp", price: "145k", unit: "VNĐ/180g", harvest: "HB-003", process: "PB-003" },
  ];

  window.OpenSesameData = {
    TODAY, DAY, seeded, between, PROVINCES, VARIETIES, PROGRAMS, STAGES, SEASON_DAYS, stageFor, STATUS, FARMS,
    HARVEST_BATCHES, FACTORY_INTAKES, PROCESSING_BATCHES, CATEGORIES, PRODUCTS, parseDate,
  };
})();
