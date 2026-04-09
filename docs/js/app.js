(function () {
  "use strict";

  const CHANNELS = ["case", "chat"];
  const CH_LABEL = { case: "Case", chat: "Chat" };

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

  function rowKey(h, ch) {
    return h + "|" + ch;
  }

  function buildEmptyFrame() {
    const rows = [];
    CHANNELS.forEach(function (ch) {
      for (let h = 0; h < 24; h++) {
        rows.push({
          hour: h,
          channel: ch,
          interval: hourLabel(h) + " · " + CH_LABEL[ch],
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
    if (!hcol || !vcol) throw new Error("Volume CSV needs hour and volume columns.");

    const byKey = {};
    baseRows.forEach(function (r) {
      byKey[rowKey(r.hour, r.channel)] = JSON.parse(JSON.stringify(r));
    });

    if (chcol) {
      const sums = {};
      csvRows.forEach(function (row) {
        const h = parseInt(row[hcol], 10);
        const ch = normalizeChannel(row[chcol]);
        const vol = parseFloat(row[vcol]) || 0;
        if (ch === null || isNaN(h) || h < 0 || h > 23) return;
        const k = rowKey(h, ch);
        sums[k] = (sums[k] || 0) + vol;
      });
      Object.keys(sums).forEach(function (k) {
        if (byKey[k]) byKey[k].volume = sums[k];
      });
    } else {
      const byHour = {};
      csvRows.forEach(function (row) {
        const h = parseInt(row[hcol], 10);
        const vol = parseFloat(row[vcol]) || 0;
        if (!isNaN(h) && h >= 0 && h <= 23) byHour[h] = (byHour[h] || 0) + vol;
      });
      CHANNELS.forEach(function (ch) {
        for (let h = 0; h < 24; h++) {
          const k = rowKey(h, ch);
          if (byKey[k]) byKey[k].volume = byHour[h] || 0;
        }
      });
    }
    return Object.keys(byKey)
      .map(function (k) {
        return byKey[k];
      })
      .sort(function (a, b) {
        return a.hour - b.hour || a.channel.localeCompare(b.channel);
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
    if (!hcol || !scol) throw new Error("Staffing CSV needs hour and staff columns.");

    const byKey = {};
    baseRows.forEach(function (r) {
      byKey[rowKey(r.hour, r.channel)] = JSON.parse(JSON.stringify(r));
    });

    if (chcol) {
      const sums = {};
      csvRows.forEach(function (row) {
        const h = parseInt(row[hcol], 10);
        const ch = normalizeChannel(row[chcol]);
        const st = parseFloat(row[scol]) || 0;
        if (ch === null || isNaN(h) || h < 0 || h > 23) return;
        const k = rowKey(h, ch);
        sums[k] = (sums[k] || 0) + st;
      });
      Object.keys(sums).forEach(function (k) {
        if (byKey[k]) byKey[k].staff_available = sums[k];
      });
    } else {
      const byHour = {};
      csvRows.forEach(function (row) {
        const h = parseInt(row[hcol], 10);
        const st = parseFloat(row[scol]) || 0;
        if (!isNaN(h) && h >= 0 && h <= 23) byHour[h] = (byHour[h] || 0) + st;
      });
      CHANNELS.forEach(function (ch) {
        for (let h = 0; h < 24; h++) {
          const k = rowKey(h, ch);
          if (byKey[k]) byKey[k].staff_available = byHour[h] || 0;
        }
      });
    }
    return Object.keys(byKey)
      .map(function (k) {
        return byKey[k];
      })
      .sort(function (a, b) {
        return a.hour - b.hour || a.channel.localeCompare(b.channel);
      });
  }

  function addMetrics(rows, ahtSec, shrink) {
    const sh = shrink >= 0.999 ? 0 : shrink;
    return rows.map(function (r) {
      const raw = (r.volume * ahtSec) / 3600;
      const hc_required = raw / (1 - sh);
      const variance = r.staff_available - hc_required;
      let status = "Balanced";
      if (variance > 1e-6) status = "Over";
      else if (variance < -1e-6) status = "Under";
      return Object.assign({}, r, {
        hc_required: hc_required,
        variance: variance,
        status: status,
      });
    });
  }

  function aggregateAll(rowsWithMetrics) {
    const byHour = {};
    rowsWithMetrics.forEach(function (r) {
      if (!byHour[r.hour]) {
        byHour[r.hour] = {
          hour: r.hour,
          channel: "all",
          interval: hourLabel(r.hour),
          volume: 0,
          staff_available: 0,
        };
      }
      byHour[r.hour].volume += r.volume;
      byHour[r.hour].staff_available += r.staff_available;
    });
    const list = Object.keys(byHour)
      .map(function (h) {
        return byHour[h];
      })
      .sort(function (a, b) {
        return a.hour - b.hour;
      });
    return addMetrics(list, state.ahtSec, state.shrink);
  }

  function filterView(rowsWithMetrics, channel) {
    if (channel === "all") return aggregateAll(rowsWithMetrics);
    return rowsWithMetrics.filter(function (r) {
      return r.channel === channel;
    });
  }

  const state = {
    rows: buildEmptyFrame(),
    ahtSec: 300,
    shrink: 0.15,
    channelFilter: "all",
  };

  let chartLine = null;
  let chartBar = null;

  function getFullMetrics() {
    return addMetrics(state.rows, state.ahtSec, state.shrink);
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

    chartLine = new Chart(lineCtx, {
      type: "line",
      data: {
        labels: labels,
        datasets: [
          {
            label: "Staff available",
            data: staff,
            borderColor: "#059669",
            backgroundColor: "rgba(5, 150, 105, 0.1)",
            tension: 0.15,
            pointRadius: 4,
            borderWidth: 2,
          },
          {
            label: "HC required",
            data: req,
            borderColor: "#b91c1c",
            backgroundColor: "rgba(185, 28, 28, 0.06)",
            tension: 0.15,
            pointRadius: 4,
            borderWidth: 2,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { position: "top", labels: { color: "#0f172a", font: { size: 12 } } },
          title: {
            display: true,
            text: "Staff vs requirement (FTE)",
            color: "#0f172a",
            font: { size: 14, weight: "600" },
          },
        },
        scales: {
          x: {
            title: { display: true, text: "Hour of day", color: "#0f172a" },
            ticks: { color: "#0f172a", maxRotation: 45, minRotation: 45, font: { size: 10 } },
            grid: { color: "#e2e8f0" },
          },
          y: {
            title: { display: true, text: "FTE (headcount)", color: "#0f172a" },
            ticks: { color: "#0f172a" },
            grid: { color: "#e2e8f0" },
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
              return v >= 0 ? "#15803d" : "#b91c1c";
            }),
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          title: {
            display: true,
            text: "Variance (FTE) — green = over, red = under",
            color: "#0f172a",
            font: { size: 14, weight: "600" },
          },
        },
        scales: {
          x: {
            title: { display: true, text: "Hour of day", color: "#0f172a" },
            ticks: { color: "#0f172a", maxRotation: 45, minRotation: 45, font: { size: 10 } },
            grid: { display: false },
          },
          y: {
            title: { display: true, text: "Variance (FTE)", color: "#0f172a" },
            ticks: { color: "#0f172a" },
            grid: { color: "#e2e8f0" },
          },
        },
      },
    });
  }

  function renderTable() {
    const full = getFullMetrics();
    const view = filterView(full, state.channelFilter);
    const tbody = document.getElementById("tbody");
    if (!tbody) return;

    const ch = state.channelFilter;
    if (ch === "all") {
      tbody.innerHTML = view
        .map(function (r) {
          return (
            "<tr><td>" +
            escapeHtml(r.interval) +
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
            '"><td>' +
            escapeHtml(r.interval) +
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

    renderCharts(view);
  }

  function onCellChange(e) {
    const inp = e.target;
    const hour = parseInt(inp.closest("tr").getAttribute("data-hour"), 10);
    const channel = inp.closest("tr").getAttribute("data-channel");
    const field = inp.getAttribute("data-field");
    const val = parseFloat(inp.value) || 0;
    state.rows.forEach(function (r) {
      if (r.hour === hour && r.channel === channel) {
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
    document.getElementById("aht").addEventListener("input", function () {
      state.ahtSec = parseFloat(document.getElementById("aht").value) || 300;
      renderTable();
    });
    document.getElementById("shrink").addEventListener("input", function () {
      state.shrink = (parseFloat(document.getElementById("shrink").value) || 0) / 100;
      renderTable();
    });
    document.getElementById("channelFilter").addEventListener("change", function () {
      state.channelFilter = document.getElementById("channelFilter").value;
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

    document.getElementById("aht").value = String(state.ahtSec);
    document.getElementById("shrink").value = String(Math.round(state.shrink * 100));
    renderTable();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bind);
  } else {
    bind();
  }
})();
