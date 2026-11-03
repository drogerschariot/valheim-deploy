// Valheim dashboard client. Plain DOM, no build step, no framework: cloning
// the repository should be the whole install.

const $ = (id) => document.getElementById(id);

// The server every panel is showing. Persisted so a reload stays put.
let current = null;
const serverPath = (what) => `/api/servers/${encodeURIComponent(current)}/${what}`;

async function api(path, options = {}) {
    const res = await fetch(path, {
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        ...options,
    });
    if (res.status === 401) {
        window.location.reload();       // session expired; back to sign-in
        throw new Error("unauthorised");
    }
    let body = {};
    try { body = await res.json(); } catch (e) { /* non-JSON error page */ }
    return { status: res.status, body };
}

function toast(message, bad = false) {
    const existing = document.querySelector(".toast");
    if (existing) existing.remove();
    const el = document.createElement("div");
    el.className = "toast" + (bad ? " bad" : "");
    el.textContent = message;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 9000);
}

function bytes(n) {
    if (n === null || n === undefined) return "—";
    const units = ["B", "KB", "MB", "GB", "TB"];
    let i = 0, v = n;
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
    return `${v < 10 && i > 0 ? v.toFixed(1) : Math.round(v)} ${units[i]}`;
}

function duration(seconds) {
    if (seconds === null || seconds === undefined) return "—";
    const d = Math.floor(seconds / 86400);
    const h = Math.floor((seconds % 86400) / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    if (d) return `${d}d ${h}h`;
    if (h) return `${h}h ${m}m`;
    return `${m}m`;
}

function shortTime(iso) {
    if (!iso) return "—";
    // Journal stamps arrive as short-iso; keep them readable rather than exact.
    return String(iso).replace("T", " ").replace(/[+-]\d{2}:?\d{2}$/, "").slice(0, 19);
}

// -- rendering ------------------------------------------------------------

function renderStatus(s) {
    const svc = s.service || {};
    const q = s.query || {};
    const act = s.activity || {};

    $("world-name").textContent = q.reachable ? (q.world || "") : "";
    const code = act.join_code;
    const codeRow = $("join-code");
    if (codeRow) codeRow.textContent = code || "not logged yet";

    const pill = $("status-pill");
    const running = svc.active === "active";
    pill.className = "pill " + (running ? (svc.ready ? "up" : "warn") : "down");
    $("status-text").textContent = running
        ? (svc.ready ? "accepting players" : (svc.sub === "installing" ? "installing" : "starting"))
        : (svc.sub === "installing" ? "installing" : (svc.active || "stopped"));

    $("t-players").textContent = q.reachable ? `${q.players} / ${q.max_players}` : "—";
    $("t-players-sub").textContent = q.reachable ? "from the query port" : (q.reason || "query port silent");

    $("t-uptime").textContent = duration(svc.uptime_seconds);
    $("t-restarts").textContent = svc.restarts && svc.restarts !== "0"
        ? `${svc.restarts} restarts` : "";

    const save = act.last_save;
    $("t-save").textContent = save ? `${save.duration_ms} ms` : "—";
    $("t-save-sub").textContent = save ? shortTime(save.at) : "no save recorded yet";

    $("t-world").textContent = bytes((s.world || {}).total_bytes);
    const hb = act.heartbeat;
    $("t-zdos").textContent = hb ? `${hb.zdos.toLocaleString()} objects` : "";

    $("t-mem").textContent = bytes(svc.memory_bytes);
    const disk = s.disk || {};
    $("t-disk").textContent = disk.percent !== undefined
        ? `disk ${disk.percent}% used, ${bytes(disk.free)} free` : "";

    // Host (VM) metrics. cpu_percent and disk_io are null on the very first
    // poll after the dashboard itself restarts -- they are rate measurements
    // with nothing yet to diff against, not a broken collector.
    const vm = s.vm || {};
    $("t-cpu").textContent = vm.cpu_percent === null || vm.cpu_percent === undefined
        ? "—" : `${vm.cpu_percent}%`;
    const load = vm.load_average || {};
    $("t-cpu-sub").textContent = vm.cpu_cores
        ? `${vm.cpu_cores} cores · load ${load["1m"] ?? "—"}/${load["5m"] ?? "—"}/${load["15m"] ?? "—"}`
        : "";

    const mem = vm.memory || {};
    $("t-ram").textContent = mem.percent !== undefined && mem.percent !== null
        ? `${mem.percent}%` : "—";
    $("t-ram-sub").textContent = mem.total_bytes
        ? `${bytes(mem.used_bytes)} / ${bytes(mem.total_bytes)}` : "";

    $("t-hostdisk").textContent = disk.percent !== undefined ? `${disk.percent}%` : "—";
    $("t-hostdisk-sub").textContent = disk.total !== undefined
        ? `${bytes(disk.used)} / ${bytes(disk.total)}` : "";

    const io = vm.disk_io;
    $("t-diskio").textContent = io
        ? `${bytes(io.read_bytes_per_sec)}/s / ${bytes(io.write_bytes_per_sec)}/s`
        : "—";

    // Launch flags, read from the running process.
    const launch = s.launch || {};
    const body = $("flags-body");
    body.innerHTML = "";
    const flags = launch.flags || {};
    const interesting = ["name", "world", "port", "public", "saveinterval",
                         "backups", "backupshort", "backuplong", "preset",
                         "crossplay", "savedir"];
    const seen = new Set();
    interesting.forEach((k) => {
        if (flags[k] === undefined) return;
        seen.add(k);
        addRow(body, k, String(flags[k]));
    });
    Object.keys(flags).sort().forEach((k) => {
        if (seen.has(k) || k === "password") return;
        addRow(body, k, String(flags[k]));
    });
    if (!launch.available) {
        addRow(body, "process", "not running — nothing to read");
    }

    const mods = launch.modifiers || {};
    const keys = launch.keys || [];
    const parts = Object.keys(mods).sort().map((k) => `<span class="tag">${escapeHtml(k)}: ${escapeHtml(mods[k])}</span>`);
    keys.forEach((k) => parts.push(`<span class="tag">${escapeHtml(k)}</span>`));
    $("modifier-line").innerHTML = parts.length
        ? "World modifiers in force: " + parts.join(" ")
        : "No world modifiers set — the world runs on Valheim's defaults.";

    // Recent players.
    const pb = $("players-body");
    pb.innerHTML = "";
    (act.recent_players || []).forEach((p) => {
        const tr = document.createElement("tr");
        tr.innerHTML = `<td>${escapeHtml(p.name)}</td><td class="mono muted">${shortTime(p.at)}</td>`;
        pb.appendChild(tr);
    });
    if (!pb.children.length) {
        pb.innerHTML = `<tr><td colspan="2" class="muted">Nobody has joined since the server started.</td></tr>`;
    }

    renderList(s.lists || {});
    $("generated").textContent = shortTime(s.generated);
}

function addRow(tbody, key, value) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td class="mono">${escapeHtml(key)}</td><td>${escapeHtml(value)}</td>`;
    tbody.appendChild(tr);
}

function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
}

let LISTS = {};

function renderList(lists) {
    LISTS = lists;
    const which = $("list-name").value;
    const body = $("list-body");
    body.innerHTML = "";
    (lists[which] || []).forEach((entry) => {
        const tr = document.createElement("tr");
        const td = document.createElement("td");
        td.className = "mono";
        td.textContent = entry;
        const act = document.createElement("td");
        act.className = "num";
        const btn = document.createElement("button");
        btn.textContent = "Remove";
        btn.className = "danger";
        btn.onclick = () => mutateList(which, entry, "remove");
        act.appendChild(btn);
        tr.appendChild(td); tr.appendChild(act);
        body.appendChild(tr);
    });
    if (!body.children.length) {
        body.innerHTML = `<tr><td colspan="2" class="muted">Empty.</td></tr>`;
    }
}

async function mutateList(list, entry, op) {
    const { status, body } = await api(serverPath("list"), {
        method: "POST",
        body: JSON.stringify({ list, entry, op }),
    });
    if (status === 200 && body.ok) {
        LISTS[list] = body.entries;
        renderList(LISTS);
        toast(`${op === "add" ? "Added" : "Removed"} ${entry}`);
    } else {
        toast(body.error || `Failed (${status})`, true);
    }
}

// -- actions --------------------------------------------------------------

async function doAction(action, confirmed = false) {
    const buttons = document.querySelectorAll("[data-action]");
    buttons.forEach((b) => (b.disabled = true));
    try {
        const { status, body } = await api(serverPath("action"), {
            method: "POST",
            body: JSON.stringify({ action, confirm: confirmed }),
        });
        if (status === 428) {
            if (window.confirm(`${body.detail}\n\nGo ahead with "${action}"?`)) {
                return doAction(action, true);
            }
            return;
        }
        toast(body.detail || (body.ok ? "Done." : "Failed."), !body.ok);
        await refresh();
    } finally {
        buttons.forEach((b) => (b.disabled = false));
    }
}

// -- loading --------------------------------------------------------------

async function refresh() {
    // Nothing to show while the wizard or a job has the screen, and a server
    // being deleted must not flash up as unreachable.
    if (!current || $("content").hidden) return;
    const asked = current;
    try {
        const { status, body } = await api(serverPath("status"));
        if (asked !== current) return;      // the dropdown moved on meanwhile
        const off = $("offline");
        const info = serverInfo();
        if (info && info.status !== "installed") {
            off.hidden = false;
            off.textContent = info.can_retry
                ? "Setup did not finish for this server. Use Retry install or Delete server below."
                : "Setup did not finish for this server. Use Delete server below to remove anything it created.";
            $("status-pill").className = "pill down";
            $("status-text").textContent = "not set up";
        } else if (body && body.service) {
            off.hidden = true;
            renderStatus(body);
        } else {
            off.hidden = false;
            off.textContent = `Can't reach this server: ${body.error || status}. ` +
                (info && info.cloud ? "If your internet address changed, use Fix my IP below." : "Check that its host is on.");
            $("status-pill").className = "pill down";
            $("status-text").textContent = "unreachable";
        }
    } catch (e) { /* reload already triggered on 401 */ }
}

let logsPollTimer = null;

async function loadLogs() {
    try {
        if (!current) return;
        const { body } = await api(serverPath("logs"));
        const el = $("logs");
        // Stick to the bottom only if the reader was already there. Scrolling
        // up to read history should not get fought by the next poll -- that
        // is the one thing that makes an auto-refreshing log unusable.
        const wasAtBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 40;
        el.textContent = (body.lines || []).join("\n");
        if (wasAtBottom) el.scrollTop = el.scrollHeight;
    } catch (e) { /* reload already triggered on 401 */ }
}

function startLogsPolling() {
    stopLogsPolling();
    loadLogs();
    logsPollTimer = setInterval(loadLogs, 3000);
}

function stopLogsPolling() {
    if (logsPollTimer) {
        clearInterval(logsPollTimer);
        logsPollTimer = null;
    }
}

// Backgrounded tabs should not keep polling a server they cannot show. Only
// resume if the Logs panel is still the one on screen when focus returns.
document.addEventListener("visibilitychange", () => {
    const onLogsPanel = $("panel-logs").classList.contains("active");
    if (document.hidden) {
        stopLogsPolling();
    } else if (onLogsPanel) {
        startLogsPolling();
    }
});

async function loadBackups() {
    const { body } = await api(serverPath("backups"));
    const tb = $("backups-body");
    tb.innerHTML = "";
    (body.backups || []).forEach((b) => {
        const tr = document.createElement("tr");
        tr.innerHTML = `<td class="mono">${escapeHtml(b.name)}</td>` +
                       `<td class="num">${bytes(b.bytes)}</td>` +
                       `<td class="muted">${shortTime(b.modified)}</td>`;
        tb.appendChild(tr);
    });
    if (!tb.children.length) {
        tb.innerHTML = `<tr><td colspan="3" class="muted">No archives yet.</td></tr>`;
    }
}

// -- connect --------------------------------------------------------------

async function loadConnect() {
    if (!current) return;
    const { status, body: c } = await api(serverPath("connect"));
    if (status !== 200) return;
    const tb = $("connect-body");
    tb.innerHTML = "";
    const secretRow = (key, value) => {
        const tr = document.createElement("tr");
        const k = document.createElement("td"); k.className = "mono"; k.textContent = key;
        const v = document.createElement("td");
        const shown = document.createElement("span"); shown.className = "mono"; shown.textContent = "••••••••";
        const btn = document.createElement("button"); btn.textContent = "Show";
        btn.style.marginLeft = "12px";
        btn.onclick = () => {
            const hidden = btn.textContent === "Show";
            shown.textContent = hidden ? value : "••••••••";
            btn.textContent = hidden ? "Hide" : "Show";
        };
        v.append(shown, btn);
        tr.append(k, v);
        tb.appendChild(tr);
    };
    addRow(tb, "Join address", c.join);
    secretRow("Password", c.password || "");
    if (c.crossplay) {
        addRow(tb, "Crossplay join code", "");
        tb.lastChild.lastChild.id = "join-code";
        tb.lastChild.lastChild.textContent = "not logged yet";
    }
    addRow(tb, "Server name", c.server_name);
    addRow(tb, "World", c.world_name);
    addRow(tb, "Game ports", c.ports);
    addRow(tb, "Listed publicly", c.public ? "yes" : "no");
    addRow(tb, "Hosted on", c.host);
    if (c.ssh) addRow(tb, "SSH", c.ssh);
    const notes = $("connect-notes");
    notes.innerHTML = "";
    if (c.notes.length) {
        const box = document.createElement("div");
        box.className = "note";
        box.style.marginTop = "18px";
        const h = document.createElement("strong");
        h.textContent = "Before players can connect:";
        const ul = document.createElement("ul");
        c.notes.forEach((n) => { const li = document.createElement("li"); li.textContent = n; ul.appendChild(li); });
        box.append(h, ul);
        notes.appendChild(box);
    }
    $("connect-cost").textContent = c.cost;
    $("reach-result").textContent = "";
    $("reach-check").hidden = c.crossplay;
}

async function checkReach() {
    $("reach-result").textContent = "Checking…";
    const { body } = await api(serverPath("reach"));
    $("reach-result").textContent = body.reachable
        ? `Reachable: ${body.players} of ${body.max_players} players on.`
        : "No answer. Check the firewall and port forward. This can fail from inside your own network even when outside players connect fine.";
}

// -- servers --------------------------------------------------------------

let SERVERS = [];
const serverInfo = () => SERVERS.find((s) => s.name === current);

function selectServer(name) {
    current = name;
    localStorage.setItem("server", name);
    const info = serverInfo();
    $("fix-ip").hidden = !(info && info.cloud);
    $("retry-install").hidden = !(info && info.can_retry);
    $("reach-result").textContent = "";
    $("world-name").textContent = "";
    $("offline").hidden = true;
    loadConnect();
    refresh();
    if ($("panel-logs").classList.contains("active")) startLogsPolling();
    if ($("panel-backups").classList.contains("active")) loadBackups();
}

async function loadServers() {
    const { body } = await api("/api/servers");
    const servers = body.servers || [];
    SERVERS = servers;
    const select = $("server-select");
    select.innerHTML = "";
    servers.forEach((s) => {
        const option = document.createElement("option");
        option.value = s.name;
        option.textContent = `${s.server_name} (${s.name}, ${s.provider})`;
        select.appendChild(option);
    });
    $("content").hidden = servers.length === 0;
    select.hidden = servers.length === 0;
    $("status-pill").hidden = servers.length === 0;
    if (!servers.length) {
        current = null;
        $("world-name").textContent = "";
    }
    const busy = servers.find((s) => s.job);
    if (busy) followJob(busy.job, "Finishing " + busy.name);
    if (!servers.length) {
        if (!busy) openWizard();
        return;
    }
    const saved = localStorage.getItem("server");
    const pick = servers.some((s) => s.name === saved) ? saved : servers[0].name;
    select.value = pick;
    selectServer(pick);
}

// -- wiring ---------------------------------------------------------------

document.querySelectorAll(".tab").forEach((tab) => {
    tab.onclick = () => {
        document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
        document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
        tab.classList.add("active");
        $("panel-" + tab.dataset.tab).classList.add("active");
        if (tab.dataset.tab === "logs") {
            startLogsPolling();
        } else {
            stopLogsPolling();
        }
        if (tab.dataset.tab === "backups") loadBackups();
        if (tab.dataset.tab === "connect") loadConnect();
    };
});

document.querySelectorAll("[data-action]").forEach((btn) => {
    btn.onclick = () => doAction(btn.dataset.action);
});

$("server-select").onchange = (e) => selectServer(e.target.value);
$("reach-check").onclick = checkReach;
$("list-name").onchange = () => renderList(LISTS);
$("list-add").onclick = () => {
    const entry = $("list-entry").value.trim();
    if (!entry) return;
    mutateList($("list-name").value, entry, "add").then(() => ($("list-entry").value = ""));
};
$("logs-refresh").onclick = loadLogs;
$("signout").onclick = async () => {
    await api("/api/logout", { method: "POST", body: "{}" });
    window.location.reload();
};

window.addEventListener("load", loadServers);
setInterval(refresh, 5000);
