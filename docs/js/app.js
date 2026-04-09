(function () {
  "use strict";

  const CHANNELS = ["case", "chat"];
  const CH_LABEL = { case: "Case", chat: "Chat" };
  var DEFAULT_TEAM = "primary";

  function teamLabel(slug) {
    if (slug === "primary") return "Primary";
    var s = String(slug || "").replace(/_/g, " ");
    return s.replace(/\b\w/g, function (c) {
      return c.toUpperCase();
    });
  }

  function normalizeTeam(val) {
    if (val == null || String(val).trim() === "") return DEFAULT_TEAM;
    var s = String(val)
      .trim()
      .toLowerCase()
      .replace(/\s+/g, "_")
      .replace(/[^a-z0-9_]/g, "");
    return s || DEFAULT_TEAM;
  }

  function hourLabel(h) {
    if (h === 0) return "12:00 AM";
    if (h < 12) return h + ":00 AM";
    if (h === 12) return "12:00 PM";
    return h - 12 + ":00 PM";
  }

  function normHeader(s) {
    return String(s || "")
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9]/g, "");
  }

  function findColumn(headers, candidates) {
    const map = {};
    headers.forEach(function (h) {
      map[normHeader(h)] = h;
    });
    for (let i = 0; i < candidates.length; i++) {
      const k = normHeader(candidates[i]);
      if (map[k] !== undefined) return map[k];
    }
    return null;
  }

  function normalizeChannel(val) {
    const s = String(val || "")
      .trim()
      .toLowerCase();
    if (["case", "cases", "ticket", "tickets", "email", "async"].indexOf(s) >= 0) return "case";
    if (["chat", "chats", "messaging", "live_chat", "live chat", "livechat"].indexOf(s) >= 0)
      return "chat";
    return null;
  }

  function parseCSV(text) {
    const lines = text
      .split(/\r?\n/)
      .map(function (l) {
        return l.trim();
      })
      .filter(function (l) {
        return l && !l.startsWith("#");
      });
    if (!lines.length) return { headers: [], rows: [] };
    const headers = lines[0].split(",").map(function (h) {
      return h.trim();
    });
    const rows = [];
    for (let i = 1; i < lines.length; i++) {
      const cols = lines[i].split(",").map(function (c) {
        return c.trim();
      });
      const obj = {};
      headers.forEach(function (h, j) {
        obj[h] = cols[j] !== undefined ? cols[j] : "";
      });
      rows.push(obj);
    }
    return { headers: headers, rows: rows };
  }

  function rowKey(h, ch, team) {
    return h + "|" + ch + "|" + (team || DEFAULT_TEAM);
  }

  function buildEmptyFrame() {
    const rows = [];
    CHANNELS.forEach(function (ch) {
      for (let h = 0; h < 24; h++) {
        rows.push({
          hour: h,
          channel: ch,
          team: DEFAULT_TEAM,
          interval: hourLabel(h),
          volume: 0,
          staff_available: 0,
        });
      }
    });
    return rows;
  }

  function mergeHourlyVolume(baseRows, parsed) {
    const headers = parsed.headers;
    const csvRows = parsed.rows;
    const hcol = findColumn(headers, ["hour", "hr", "hourofday"]);
    const vcol = findColumn(headers, ["volume", "contacts", "calls", "workload", "arrivals"]);
    const chcol = findColumn(headers, ["channel", "queue", "media", "type", "skill"]);
    const tcol = findColumn(headers, ["team", "squad", "group", "pod"]);
    if (!hcol || !vcol) throw new Error("Volume CSV needs hour and volume columns.");

    const byKey = {};
    baseRows.forEach(function (r) {
      var tm = r.team || DEFAULT_TEAM;
      byKey[rowKey(r.hour, r.channel, tm)] = JSON.parse(JSON.stringify(r));
    });

    function ensureKey(h, ch, tm) {
      var k = rowKey(h, ch, tm);
      if (!byKey[k]) {
        byKey[k] = {
          hour: h,
          channel: ch,
          team: tm,
          interval: hourLabel(h),
          volume: 0,
          staff_available: 0,
        };
      }
      return k;
    }

    if (chcol) {
      const sums = {};
      csvRows.forEach(function (row) {
        const h = parseInt(row[hcol], 10);
        const ch = normalizeChannel(row[chcol]);
        const vol = parseFloat(row[vcol]) || 0;
        const tm = tcol ? normalizeTeam(row[tcol]) : DEFAULT_TEAM;
        if (ch === null || isNaN(h) || h < 0 || h > 23) return;
        const k = ensureKey(h, ch, tm);
        sums[k] = (sums[k] || 0) + vol;
      });
      Object.keys(sums).forEach(function (k) {
        byKey[k].volume = sums[k];
      });
    } else {
      const sums = {};
      csvRows.forEach(function (row) {
        const h = parseInt(row[hcol], 10);
        const vol = parseFloat(row[vcol]) || 0;
        const tm = tcol ? normalizeTeam(row[tcol]) : DEFAULT_TEAM;
        if (isNaN(h) || h < 0 || h > 23) return;
        CHANNELS.forEach(function (ch) {
          const k = ensureKey(h, ch, tm);
          sums[k] = (sums[k] || 0) + vol;
        });
      });
      Object.keys(sums).forEach(function (k) {
        byKey[k].volume = sums[k];
      });
    }
    return Object.keys(byKey)
      .map(function (k) {
        return byKey[k];
      })
      .sort(function (a, b) {
        return (
          a.hour - b.hour ||
          a.channel.localeCompare(b.channel) ||
          String(a.team).localeCompare(String(b.team))
        );
      });
  }

  function mergeStaffByHour(baseRows, parsed) {
    const headers = parsed.headers;
    const csvRows = parsed.rows;
    const hcol = findColumn(headers, ["hour", "hr"]);
    const scol = findColumn(headers, [
      "staff_available",
      "staff",
      "headcount",
      "fte",
      "scheduled",
      "roster",
      "agents",
    ]);
    const chcol = findColumn(headers, ["channel", "queue", "media", "type", "skill"]);
    const tcol = findColumn(headers, ["team", "squad", "group", "pod"]);
    if (!hcol || !scol) throw new Error("Staffing CSV needs hour and staff columns.");

    const byKey = {};
    baseRows.forEach(function (r) {
      var tm = r.team || DEFAULT_TEAM;
      byKey[rowKey(r.hour, r.channel, tm)] = JSON.parse(JSON.stringify(r));
    });

    function ensureKey(h, ch, tm) {
      var k = rowKey(h, ch, tm);
      if (!byKey[k]) {
        byKey[k] = {
          hour: h,
          channel: ch,
          team: tm,
          interval: hourLabel(h),
          volume: 0,
          staff_available: 0,
        };
      }
      return k;
    }

    if (chcol) {
      const sums = {};
      csvRows.forEach(function (row) {
        const h = parseInt(row[hcol], 10);
        const ch = normalizeChannel(row[chcol]);
        const st = parseFloat(row[scol]) || 0;
        const tm = tcol ? normalizeTeam(row[tcol]) : DEFAULT_TEAM;
        if (ch === null || isNaN(h) || h < 0 || h > 23) return;
        const k = ensureKey(h, ch, tm);
        sums[k] = (sums[k] || 0) + st;
      });
      Object.keys(sums).forEach(function (k) {
        byKey[k].staff_available = sums[k];
      });
    } else {
      const sums = {};
      csvRows.forEach(function (row) {
        const h = parseInt(row[hcol], 10);
        const st = parseFloat(row[scol]) || 0;
        const tm = tcol ? normalizeTeam(row[tcol]) : DEFAULT_TEAM;
        if (isNaN(h) || h < 0 || h > 23) return;
        CHANNELS.forEach(function (ch) {
          const k = ensureKey(h, ch, tm);
          sums[k] = (sums[k] || 0) + st;
        });
      });
      Object.keys(sums).forEach(function (k) {
        byKey[k].staff_available = sums[k];
      });
    }
    return Object.keys(byKey)
      .map(function (k) {
        return byKey[k];
      })
      .sort(function (a, b) {
        return (
          a.hour - b.hour ||
          a.channel.localeCompare(b.channel) ||
          String(a.team).localeCompare(String(b.team))
        );
      });
  }

  function clamp01(x, lo, hi) {
    lo = lo !== undefined ? lo : 0.05;
    hi = hi !== undefined ? hi : 1.0;
    return Math.max(lo, Math.min(hi, x));
  }

  function erlangCDelayProbability(A, n) {
    if (n <= 0) return 1;
    if (A <= 0) return 0;
    if (A >= n) return 1;
    var s = 0;
    var term = 1;
    for (var k = 1; k < n; k++) {
      term *= A / k;
      s += term;
    }
    var termN = term * (A / n);
    var block = termN * (n / (n - A));
    var denom = s + block;
    if (denom <= 0) return 1;
    return block / denom;
  }

  function serviceLevelMmC(A, n, ahtSec, volumePerHour, serviceTimeSec) {
    var lam = volumePerHour / 3600;
    var mu = 1 / Math.max(ahtSec, 1e-9);
    var pw = erlangCDelayProbability(A, n);
    var diff = n * mu - lam;
    if (diff <= 1e-12) return Math.max(0, Math.min(1, 1 - pw));
    return Math.max(0, Math.min(1, 1 - pw * Math.exp(-diff * serviceTimeSec)));
  }

  function minAgentsErlangSla(volume, ahtSec, slaTarget, serviceTimeSec) {
    if (volume <= 1e-12) return 0;
    ahtSec = Math.max(ahtSec, 1);
    serviceTimeSec = Math.max(serviceTimeSec, 0);
    slaTarget = clamp01(slaTarget, 0.01, 0.999);
    var A = (volume * ahtSec) / 3600;
    var n0 = Math.max(1, Math.floor(A) + 1);
    var n;
    for (n = n0; n < n0 + 2000; n++) {
      if (n <= A) continue;
      var sl = serviceLevelMmC(A, n, ahtSec, volume, serviceTimeSec);
      if (sl + 1e-9 >= slaTarget) return n;
    }
    return n0 + 1999;
  }

  function requiredHcSimpleHour(volume, ahtSec, shrinkage, occupancy, utilization) {
    var sh = shrinkage >= 0.999 ? 0 : shrinkage;
    var occ = clamp01(occupancy);
    var util = clamp01(utilization);
    var raw = (volume * ahtSec) / 3600;
    return raw / (1 - sh) / occ / util;
  }

  function requiredHcErlangInflated(volume, p) {
    var n = minAgentsErlangSla(volume, p.ahtSec, p.slaTarget, p.serviceTimeSec);
    var shrink = p.shrinkage >= 0.999 ? 0 : p.shrinkage;
    var occ = clamp01(p.occupancy);
    var util = clamp01(p.utilization);
    return n / (1 - shrink) / occ / util;
  }

  function requiredHcForVolume(volume, p, channel) {
    if (volume <= 0) return 0;
    var wl = requiredHcSimpleHour(volume, p.ahtSec, p.shrinkage, p.occupancy, p.utilization);
    var base;
    if (p.model === "simple") base = wl;
    else if (p.model === "erlang") base = requiredHcErlangInflated(volume, p);
    else if (p.model === "hybrid") base = Math.max(wl, requiredHcErlangInflated(volume, p));
    else base = wl;
    if (channel === "chat") {
      var cc = Math.max(1, p.chatConcurrency != null ? p.chatConcurrency : 1);
      return base / cc;
    }
    return base;
  }

  function getHcParams() {
    return {
      model: state.hcModel,
      ahtSec: state.ahtSec,
      shrinkage: state.shrink,
      occupancy: state.occPct / 100,
      utilization: state.utilPct / 100,
      slaTarget: state.slaTarget,
      serviceTimeSec: state.serviceTimeSec,
      chatConcurrency: state.chatConcurrency,
    };
  }

  function addMetrics(rows) {
    var p = getHcParams();
    return rows.map(function (r) {
      var hc_required = requiredHcForVolume(r.volume, {
        model: p.model,
        ahtSec: p.ahtSec,
        shrinkage: p.shrinkage,
        occupancy: p.occupancy,
        utilization: p.utilization,
        slaTarget: p.slaTarget,
        serviceTimeSec: p.serviceTimeSec,
        chatConcurrency: p.chatConcurrency,
      }, r.channel);
      var variance = r.staff_available - hc_required;
      var status = "Balanced";
      if (variance > 1e-6) status = "Over";
      else if (variance < -1e-6) status = "Under";
      return Object.assign({}, r, {
        hc_required: hc_required,
        variance: variance,
        status: status,
      });
    });
  }

  function aggregateHourlyRows(rowsWithMetrics) {
    const byHour = {};
    rowsWithMetrics.forEach(function (r) {
      if (!byHour[r.hour]) {
        byHour[r.hour] = {
          hour: r.hour,
          channel: "all",
          team: "all",
          interval: hourLabel(r.hour),
          volume: 0,
          staff_available: 0,
          hc_required: 0,
        };
      }
      byHour[r.hour].volume += r.volume;
      byHour[r.hour].staff_available += r.staff_available;
      byHour[r.hour].hc_required += r.hc_required;
    });
    return Object.keys(byHour)
      .map(function (h) {
        var row = byHour[h];
        var variance = row.staff_available - row.hc_required;
        var status = "Balanced";
        if (variance > 1e-6) status = "Over";
        else if (variance < -1e-6) status = "Under";
        return Object.assign({}, row, { variance: variance, status: status });
      })
      .sort(function (a, b) {
        return a.hour - b.hour;
      });
  }

  function filterView(rowsWithMetrics, channel, team) {
    var filtered = rowsWithMetrics.filter(function (r) {
      var tm = r.team || DEFAULT_TEAM;
      var chOk = channel === "all" || r.channel === channel;
      var tmOk = team === "all" || tm === team;
      return chOk && tmOk;
    });
    var counts = {};
    filtered.forEach(function (r) {
      counts[r.hour] = (counts[r.hour] || 0) + 1;
    });
    var multi = false;
    Object.keys(counts).forEach(function (h) {
      if (counts[h] > 1) multi = true;
    });
    if (multi) {
      return aggregateHourlyRows(filtered);
    }
    return filtered.map(function (r) {
      var variance = r.staff_available - r.hc_required;
      var status = "Balanced";
      if (variance > 1e-6) status = "Over";
      else if (variance < -1e-6) status = "Under";
      return Object.assign({}, r, { variance: variance, status: status });
    });
  }

  const state = {
    rows: buildEmptyFrame(),
    hcModel: "simple",
    ahtSec: 300,
    shrink: 0.15,
    occPct: 100,
    utilPct: 100,
    slaTarget: 0.95,
    serviceTimeSec: 15,
    chatConcurrency: 2,
    channelFilter: "all",
    teamFilter: "all",
  };

  let chartLine = null;
  let chartBar = null;

  function getFullMetrics() {
    return addMetrics(state.rows);
  }

  function syncErlangInputsDisabled() {
    var slaOn = state.hcModel === "erlang" || state.hcModel === "hybrid";
    var sla = document.getElementById("sla");
    var svc = document.getElementById("svcTime");
    if (sla) sla.disabled = !slaOn;
    if (svc) svc.disabled = !slaOn;
  }

  function destroyCharts() {
    if (chartLine) {
      chartLine.destroy();
      chartLine = null;
    }
    if (chartBar) {
      chartBar.destroy();
      chartBar = null;
    }
  }

  function renderCharts(viewRows) {
    destroyCharts();
    const labels = viewRows.map(function (r) {
      return hourLabel(r.hour);
    });
    const staff = viewRows.map(function (r) {
      return r.staff_available;
    });
    const req = viewRows.map(function (r) {
      return r.hc_required;
    });
    const variance = viewRows.map(function (r) {
      return r.variance;
    });

    const lineCtx = document.getElementById("chartLine");
    const barCtx = document.getElementById("chartBar");
    if (!lineCtx || !barCtx) return;

    var chartInk = "#1e293b";
    var chartGrid = "#e2e8f0";

    chartLine = new Chart(lineCtx, {
      type: "line",
      data: {
        labels: labels,
        datasets: [
          {
            label: "Staff available",
            data: staff,
            borderColor: "#059669",
            backgroundColor: "rgba(5, 150, 105, 0.12)",
            tension: 0.2,
            pointRadius: 4,
            pointHoverRadius: 6,
            borderWidth: 2.5,
          },
          {
            label: "HC required",
            data: req,
            borderColor: "#c2410c",
            backgroundColor: "rgba(194, 65, 12, 0.08)",
            tension: 0.2,
            pointRadius: 4,
            pointHoverRadius: 6,
            borderWidth: 2.5,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: {
            position: "top",
            labels: { color: chartInk, font: { size: 12, family: "'Plus Jakarta Sans', system-ui, sans-serif" } },
          },
          title: {
            display: true,
            text: "Staff vs requirement (FTE)",
            color: chartInk,
            font: { size: 15, weight: "600", family: "'Plus Jakarta Sans', system-ui, sans-serif" },
          },
        },
        scales: {
          x: {
            title: { display: true, text: "Hour of day", color: chartInk, font: { size: 11 } },
            ticks: { color: chartInk, maxRotation: 45, minRotation: 45, font: { size: 10 } },
            grid: { color: chartGrid },
          },
          y: {
            title: { display: true, text: "FTE (headcount)", color: chartInk, font: { size: 11 } },
            ticks: { color: chartInk },
            grid: { color: chartGrid },
            beginAtZero: true,
          },
        },
      },
    });

    chartBar = new Chart(barCtx, {
      type: "bar",
      data: {
        labels: labels,
        datasets: [
          {
            label: "Variance",
            data: variance,
            backgroundColor: variance.map(function (v) {
              return v >= 0 ? "#059669" : "#dc2626";
            }),
            borderRadius: 4,
            borderSkipped: false,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { display: false },
          title: {
            display: true,
            text: "Variance (FTE) — green = over, red = under",
            color: chartInk,
            font: { size: 15, weight: "600", family: "'Plus Jakarta Sans', system-ui, sans-serif" },
          },
        },
        scales: {
          x: {
            title: { display: true, text: "Hour of day", color: chartInk, font: { size: 11 } },
            ticks: { color: chartInk, maxRotation: 45, minRotation: 45, font: { size: 10 } },
            grid: { display: false },
          },
          y: {
            title: { display: true, text: "Variance (FTE)", color: chartInk, font: { size: 11 } },
            ticks: { color: chartInk },
            grid: { color: chartGrid },
          },
        },
      },
    });
  }

  function rowChannelLabel(r) {
    var c = r.channel;
    if (c === "all") return "All channels";
    if (c === "case") return "Case";
    return "Chat";
  }

  function rowTeamLabel(r) {
    var t = r.team || DEFAULT_TEAM;
    if (t === "all") return "All teams";
    return teamLabel(t);
  }

  function populateTeamFilter() {
    var sel = document.getElementById("teamFilter");
    if (!sel) return;
    var seen = {};
    state.rows.forEach(function (r) {
      seen[r.team || DEFAULT_TEAM] = true;
    });
    var list = Object.keys(seen).sort();
    var cur = state.teamFilter;
    var html = '<option value="all">All teams</option>';
    list.forEach(function (t) {
      if (t === "all") return;
      html +=
        '<option value="' +
        escapeHtml(t) +
        '">' +
        escapeHtml(teamLabel(t)) +
        "</option>";
    });
    sel.innerHTML = html;
    if (cur === "all" || seen[cur]) {
      sel.value = cur;
    } else {
      sel.value = "all";
      state.teamFilter = "all";
    }
  }

  function renderTable() {
    populateTeamFilter();
    const full = getFullMetrics();
    const view = filterView(full, state.channelFilter, state.teamFilter);
    const tbody = document.getElementById("tbody");
    if (!tbody) return;

    const readonly =
      state.channelFilter === "all" || state.teamFilter === "all";
    if (readonly) {
      tbody.innerHTML = view
        .map(function (r) {
          return (
            "<tr><td>" +
            escapeHtml(r.interval) +
            "</td><td>" +
            escapeHtml(rowChannelLabel(r)) +
            "</td><td>" +
            escapeHtml(rowTeamLabel(r)) +
            "</td><td>" +
            fmtNum(r.volume) +
            "</td><td>" +
            fmtNum(r.hc_required) +
            "</td><td>" +
            fmtNum(r.staff_available) +
            "</td><td>" +
            fmtNum(r.variance) +
            '</td><td class="st">' +
            escapeHtml(r.status) +
            "</td></tr>"
          );
        })
        .join("");
    } else {
      tbody.innerHTML = view
        .map(function (r, idx) {
          return (
            "<tr data-hour=\"" +
            r.hour +
            '" data-channel="' +
            r.channel +
            '" data-team="' +
            escapeHtml(r.team || DEFAULT_TEAM) +
            '"><td>' +
            escapeHtml(r.interval) +
            "</td><td>" +
            escapeHtml(rowChannelLabel(r)) +
            "</td><td>" +
            escapeHtml(rowTeamLabel(r)) +
            '</td><td><input type="number" min="0" step="0.1" data-field="volume" data-i="' +
            idx +
            '" value="' +
            r.volume +
            '" /></td><td>' +
            fmtNum(r.hc_required) +
            '</td><td><input type="number" min="0" step="0.1" data-field="staff" data-i="' +
            idx +
            '" value="' +
            r.staff_available +
            '" /></td><td>' +
            fmtNum(r.variance) +
            '</td><td class="st">' +
            escapeHtml(r.status) +
            "</td></tr>"
          );
        })
        .join("");

      tbody.querySelectorAll("input").forEach(function (inp) {
        inp.addEventListener("change", onCellChange);
      });
    }

    const over = view.filter(function (r) {
      return r.variance > 1e-6;
    }).length;
    const under = view.filter(function (r) {
      return r.variance < -1e-6;
    }).length;
    const worst = view.reduce(
      function (w, r) {
        return !w || r.variance < w.variance ? r : w;
      },
      null
    );

    document.getElementById("mTotalVol").textContent = fmtNum(
      view.reduce(function (s, r) {
        return s + r.volume;
      }, 0)
    );
    document.getElementById("mOver").textContent = String(over);
    document.getElementById("mUnder").textContent = String(under);
    document.getElementById("mWorst").textContent = worst ? fmtNum(worst.variance) : "—";

    var chName =
      state.channelFilter === "all"
        ? "All channels"
        : state.channelFilter === "case"
          ? "Case"
          : "Chat";
    var tmName =
      state.teamFilter === "all" ? "All teams" : teamLabel(state.teamFilter);
    var fa = document.getElementById("filterActive");
    if (fa) {
      fa.textContent = "Active filters · Channel: " + chName + " · Team: " + tmName;
    }

    renderCharts(view);
  }

  function onCellChange(e) {
    const inp = e.target;
    const tr = inp.closest("tr");
    const hour = parseInt(tr.getAttribute("data-hour"), 10);
    const channel = tr.getAttribute("data-channel");
    const team = tr.getAttribute("data-team") || DEFAULT_TEAM;
    const field = inp.getAttribute("data-field");
    const val = parseFloat(inp.value) || 0;
    state.rows.forEach(function (r) {
      if (
        r.hour === hour &&
        r.channel === channel &&
        (r.team || DEFAULT_TEAM) === team
      ) {
        if (field === "volume") r.volume = val;
        if (field === "staff") r.staff_available = val;
      }
    });
    renderTable();
  }

  function escapeHtml(s) {
    const d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
  }

  function fmtNum(x) {
    if (typeof x !== "number" || isNaN(x)) return "—";
    return Math.abs(x - Math.round(x)) < 1e-6 ? String(Math.round(x)) : x.toFixed(2);
  }

  function readFile(f, cb) {
    const r = new FileReader();
    r.onload = function () {
      cb(r.result);
    };
    r.readAsText(f);
  }

  function bind() {
    document.getElementById("hcModel").addEventListener("change", function () {
      state.hcModel = document.getElementById("hcModel").value;
      syncErlangInputsDisabled();
      renderTable();
    });
    document.getElementById("aht").addEventListener("input", function () {
      state.ahtSec = parseFloat(document.getElementById("aht").value) || 300;
      renderTable();
    });
    document.getElementById("shrink").addEventListener("input", function () {
      state.shrink = (parseFloat(document.getElementById("shrink").value) || 0) / 100;
      renderTable();
    });
    document.getElementById("occ").addEventListener("input", function () {
      state.occPct = parseFloat(document.getElementById("occ").value) || 100;
      renderTable();
    });
    document.getElementById("util").addEventListener("input", function () {
      state.utilPct = parseFloat(document.getElementById("util").value) || 100;
      renderTable();
    });
    document.getElementById("chatConc").addEventListener("input", function () {
      state.chatConcurrency = parseFloat(document.getElementById("chatConc").value) || 2;
      renderTable();
    });
    document.getElementById("sla").addEventListener("input", function () {
      state.slaTarget = parseFloat(document.getElementById("sla").value) || 0.95;
      renderTable();
    });
    document.getElementById("svcTime").addEventListener("input", function () {
      state.serviceTimeSec = parseFloat(document.getElementById("svcTime").value) || 15;
      renderTable();
    });
    document.getElementById("channelFilter").addEventListener("change", function () {
      state.channelFilter = document.getElementById("channelFilter").value;
      renderTable();
    });
    document.getElementById("teamFilter").addEventListener("change", function () {
      state.teamFilter = document.getElementById("teamFilter").value;
      renderTable();
    });

    document.getElementById("volFile").addEventListener("change", function (e) {
      const f = e.target.files && e.target.files[0];
      if (!f) return;
      readFile(f, function (text) {
        try {
          state.rows = mergeHourlyVolume(state.rows, parseCSV(text));
          renderTable();
        } catch (err) {
          alert(err.message || String(err));
        }
      });
    });
    document.getElementById("staffFile").addEventListener("change", function (e) {
      const f = e.target.files && e.target.files[0];
      if (!f) return;
      readFile(f, function (text) {
        try {
          state.rows = mergeStaffByHour(state.rows, parseCSV(text));
          renderTable();
        } catch (err) {
          alert(err.message || String(err));
        }
      });
    });

    document.getElementById("btnReset").addEventListener("click", function () {
      state.rows = buildEmptyFrame();
      renderTable();
    });

    document.querySelectorAll(".tabs button").forEach(function (btn) {
      btn.addEventListener("click", function () {
        document.querySelectorAll(".tabs button").forEach(function (b) {
          b.classList.remove("active");
        });
        document.querySelectorAll(".panel").forEach(function (p) {
          p.classList.remove("active");
        });
        btn.classList.add("active");
        const id = btn.getAttribute("data-tab");
        document.getElementById(id).classList.add("active");
        if (id === "panelCharts") {
          setTimeout(function () {
            renderTable();
          }, 80);
        }
      });
    });

    document.getElementById("hcModel").value = state.hcModel;
    document.getElementById("aht").value = String(state.ahtSec);
    document.getElementById("shrink").value = String(Math.round(state.shrink * 100));
    document.getElementById("occ").value = String(state.occPct);
    document.getElementById("util").value = String(state.utilPct);
    document.getElementById("chatConc").value = String(state.chatConcurrency);
    document.getElementById("sla").value = String(state.slaTarget);
    document.getElementById("svcTime").value = String(state.serviceTimeSec);
    syncErlangInputsDisabled();
    renderTable();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bind);
  } else {
    bind();
  }
})();
