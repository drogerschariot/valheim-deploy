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
    const load = vm.load_average;
    $("t-cpu-sub").textContent = !vm.cpu_cores ? ""
        : load ? `${vm.cpu_cores} cores · load ${load["1m"] ?? "—"}/${load["5m"] ?? "—"}/${load["15m"] ?? "—"}`
        : `${vm.cpu_cores} cores`;

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

// -- network (servers on this PC) ------------------------------------------

function netItem(state, title, ...body) {
    const item = document.createElement("div");
    item.className = `net-item ${state}`;
    const mark = { ok: "✓", todo: "!", bad: "✕", unknown: "?" }[state];
    const head = document.createElement("div");
    head.className = "net-head";
    head.innerHTML = `<span class="net-mark">${mark}</span>`;
    const h = document.createElement("strong");
    h.textContent = title;
    head.appendChild(h);
    item.appendChild(head);
    body.forEach((b) => item.appendChild(typeof b === "string" ? Object.assign(document.createElement("p"), { textContent: b }) : b));
    return item;
}

function netButton(label, action, note) {
    const row = document.createElement("div");
    row.className = "row";
    const b = document.createElement("button");
    b.textContent = label;
    const result = document.createElement("span");
    result.className = "muted";
    if (note) result.textContent = note;
    b.onclick = async () => {
        b.disabled = true;
        result.textContent = "Working…";
        const { body } = await api(serverPath("network"), { method: "POST", body: JSON.stringify({ action }) });
        b.disabled = false;
        result.textContent = body.detail || body.error || "";
        toast(body.detail || body.error, !body.ok);
        if (body.ok) loadNetwork(true);
    };
    row.append(b, result);
    return row;
}

function steps(lines) {
    const ol = document.createElement("ol");
    lines.forEach((l) => { const li = document.createElement("li"); li.innerHTML = l; ol.appendChild(li); });
    return ol;
}

async function loadNetwork(refresh = false) {
    if (!current) return;
    const asked = current;
    const box = $("net-body");
    if (!box.children.length || refresh) box.innerHTML = `<p class="muted">Checking your network… this takes a few seconds.</p>`;
    const { status, body: n } = await api(serverPath("network") + (refresh ? "?refresh=1" : ""));
    if (asked !== current) return;
    if (status !== 200) { box.innerHTML = `<p class="error-line">${escapeHtml(n.error || "Could not check.")}</p>`; return; }
    box.innerHTML = "";
    const ports = `UDP ${n.ports[0]}-${n.ports[1]}`;
    const lan = escapeHtml(n.lan_ip || "this PC's address");

    if (n.public_ip_changed) {
        box.appendChild(netItem("todo", "Your public IP address changed",
            `It was ${n.previous_public_ip} and is now ${n.public_ip}. Send friends the new join address below. ` +
            "A dynamic DNS name, which many routers offer, gives them one that never changes."));
    }

    // 1. Windows Firewall
    const fw = n.firewall;
    if (!fw.supported) {
        box.appendChild(netItem("unknown", "Windows Firewall", "Only checked on Windows."));
    } else if (fw.rule) {
        box.appendChild(netItem("ok", "Windows Firewall lets the game in",
            `A rule allows ${ports} in for the Valheim server.`, netButton("Remove rule", "firewall-remove")));
    } else {
        box.appendChild(netItem("todo", "Windows Firewall may block players",
            `Add a rule that lets ${ports} in for the Valheim server only. Windows asks for permission: choose Yes.`,
            netButton("Add firewall rule", "firewall-add")));
    }

    // 2. Router port forwarding
    const manual = steps([
        n.router_page ? `Open your router's page: <a href="${escapeHtml(n.router_page)}" target="_blank" rel="noopener">${escapeHtml(n.router_page)}</a>, and sign in. The password is often on a sticker on the router.`
                      : "Open your router's settings page and sign in. Its address is often on a sticker on the router.",
        `Find the port forwarding section. It may be called Port Forwarding, Virtual Server, NAT, Applications or Gaming.`,
        `Add a rule: protocol <strong>UDP</strong>, ports <strong>${n.ports[0]}-${n.ports[1]}</strong>, to <strong>${lan}</strong>.`,
        `Also reserve ${lan} for this PC (often under DHCP or LAN settings), so the address, and the rule, keep working.`,
    ]);
    if (n.upnp.forwarded) {
        box.appendChild(netItem("ok", "Your router forwards the game to this PC",
            `${ports} goes to ${n.lan_ip}.`, netButton("Remove the forward", "close-ports")));
    } else if (n.upnp.available) {
        const parts = [`Your router can do this for you. It will send ${ports} to this PC (${n.lan_ip}).`];
        if (n.upnp.elsewhere.length) parts.push(`Right now these ports go to another device (${n.upnp.elsewhere.join(", ")}), perhaps another server.`);
        box.appendChild(netItem("todo", "Forward the game ports on your router", ...parts,
            netButton("Open ports automatically", "open-ports")));
    } else {
        const details = document.createElement("details");
        details.innerHTML = "<summary>How to do it by hand</summary>";
        details.open = true;
        details.appendChild(manual);
        box.appendChild(netItem("todo", "Forward the game ports on your router",
            "Your router didn't answer the automatic request (UPnP), so this needs doing in its settings. " +
            "If you already did, this can stay unticked: the internet check below is the real test.", details));
    }

    // 3. Public IP and CGNAT
    const pub = escapeHtml(n.public_ip || "unknown");
    if (n.nat === "ok") {
        box.appendChild(netItem("ok", "Your internet connection accepts incoming players",
            `Your router holds your public IP address (${n.public_ip}), so port forwarding works.`));
    } else if (n.nat === "cgnat") {
        box.appendChild(netItem("bad", "Your internet provider uses CGNAT",
            `Your router's internet address (${n.router_wan_ip}) isn't your public IP (${n.public_ip}). ` +
            "Your provider shares one address between customers, so no setting at home can let friends in.",
            steps(["Ask your provider for a public IPv4 address, or to take you off CGNAT. Many do this free or for a small fee.",
                   "Until then, friends on your own network can still join with the LAN address below."])));
    } else if (n.nat === "double") {
        box.appendChild(netItem("bad", "There's a second router in front of yours",
            `Your router's internet address (${n.router_wan_ip}) is a private one, so another device, often the provider's modem, sits in front. ` +
            "Either forward the ports on that device too, to your router's address, or set it to bridge mode."));
    } else {
        box.appendChild(netItem("unknown", "Couldn't check for CGNAT",
            `Your public IP is ${n.public_ip || "unknown"}. On your router's status page, find its internet (WAN) address. ` +
            "If it differs from your public IP, or starts with 100.64 to 100.127, your provider uses CGNAT and port forwarding can't work."));
    }

    // 4. What to send friends
    const share = document.createElement("table");
    const tb = document.createElement("tbody");
    addRow(tb, "Friends on the internet", n.join.internet || "unknown");
    addRow(tb, "Players on your home network", n.join.lan || "unknown");
    share.appendChild(tb);
    box.appendChild(netItem("ok", "What to send friends", share,
        "Plus the password from the Connect tab. In Valheim: Join Game, Add server, then the address."));
    $("net-checked").textContent = `Checked ${new Date().toLocaleTimeString()}.`;
}

// -- servers --------------------------------------------------------------

let SERVERS = [];
const serverInfo = () => SERVERS.find((s) => s.name === current);

function selectServer(name) {
    current = name;
    localStorage.setItem("server", name);
    const info = serverInfo();
    const pc = !!(info && info.this_pc);
    $("fix-ip").hidden = !(info && info.cloud);
    $("retry-install").hidden = !(info && info.can_retry);
    $("network-tab").hidden = !pc;
    $("autostart-wrap").hidden = !pc;
    $("autostart").checked = !!(info && info.autostart);
    $("open-folder").hidden = !pc;
    $("download-backups").hidden = pc;
    $("update-check").hidden = pc;
    $("t-diskio").closest(".tile").hidden = pc;  // Windows gives no disk rate without admin
    $("net-body").innerHTML = "";
    if (!pc && $("panel-network").classList.contains("active")) document.querySelector('[data-tab="connect"]').click();
    if (pc && $("panel-network").classList.contains("active")) loadNetwork();
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
    $("quit-app").hidden = !body.app_mode;
    $("signout").hidden = !!body.app_mode;
    if (body.app_mode) $("footer-where").textContent = "servers run on this PC · closing this window leaves them running";
    const select = $("server-select");
    select.innerHTML = "";
    servers.forEach((s) => {
        const option = document.createElement("option");
        option.value = s.name;
        option.textContent = body.app_mode ? `${s.server_name} (${s.name})` : `${s.server_name} (${s.name}, ${s.provider})`;
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
        if (tab.dataset.tab === "network") loadNetwork();
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
$("net-refresh").onclick = () => loadNetwork(true);
$("autostart").onchange = async (e) => {
    const { body } = await api(serverPath("autostart"), { method: "POST", body: JSON.stringify({ on: e.target.checked }) });
    const info = serverInfo();
    if (info) info.autostart = body.autostart;
    toast(body.autostart ? "Starts when the app opens." : "Won't start until you press Start.");
};
$("open-folder").onclick = async () => {
    const { body } = await api(serverPath("network"), { method: "POST", body: JSON.stringify({ action: "open-folder" }) });
    if (!body.ok) toast(body.error, true);
};
$("quit-app").onclick = async () => {
    if (!window.confirm("Quit Valheim Deploy?\n\nEvery server stops, saving its world first. Players are disconnected.")) return;
    await api("/api/app/quit", { method: "POST", body: "{}" });
    document.body.innerHTML = '<main><h2 style="margin-top:60px">Stopping your servers and saving their worlds…</h2>' +
        '<p class="muted">This can take up to two minutes. You can close this window.</p></main>';
};
$("signout").onclick = async () => {
    await api("/api/logout", { method: "POST", body: "{}" });
    window.location.reload();
};

window.addEventListener("load", loadServers);
setInterval(refresh, 5000);
