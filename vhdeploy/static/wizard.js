// The browser setup wizard and long-running jobs. Plain DOM, like app.js. It
// only collects answers: every rule is enforced on the server by the same code
// the terminal wizard runs.

const STEPS = ["Before you start", "Where", "Host details", "Your server", "Review"];
const answers = {};
let wizardStep = 0, options = null, hostOptions = null;
// Where each host's account walk-through is: "ask", "signup" or "credentials".
// Kept apart from answers, so credentials are never part of the create request.
const guideAt = {};
let reconnect = false, madeKey = null;

const el = (tag, props = {}, ...kids) => {
    const node = Object.assign(document.createElement(tag), props);
    node.append(...kids);
    return node;
};

function field(label, input, help) {
    const wrap = el("label", { className: "field" }, el("span", { textContent: label }), input);
    if (help) wrap.append(el("small", { textContent: help }));
    return wrap;
}

// A text box bound to one answer.
function text(key, placeholder = "", type = "text") {
    return el("input", {
        type, placeholder, value: answers[key] ?? "", autocomplete: "off",
        oninput: (e) => { answers[key] = e.target.value; },
    });
}

function pick(key, choices) {
    const select = el("select", { onchange: (e) => { answers[key] = e.target.value; } });
    choices.forEach((c) => select.append(el("option", { value: c.value, textContent: c.label })));
    if (choices.some((c) => c.value === answers[key])) select.value = answers[key];
    else answers[key] = choices.length ? choices[0].value : "";
    return select;
}

function tick(key, label) {
    return el("label", { className: "check" }, el("input", {
        type: "checkbox", checked: !!answers[key],
        onchange: (e) => { answers[key] = e.target.checked; },
    }), el("span", { textContent: label }));
}

function showOnly(id) {
    ["wizard", "job", "content", "confirm-delete"].forEach((x) => { $(x).hidden = x !== id; });
    $("offline").hidden = true;
}

async function openWizard() {
    Object.keys(answers).forEach((k) => delete answers[k]);
    Object.keys(guideAt).forEach((k) => delete guideAt[k]);
    wizardStep = 0;
    reconnect = false;
    madeKey = null;
    showOnly("wizard");
    $("wizard-cancel").hidden = SERVERS.length === 0;
    $("wizard-body").textContent = "Checking this computer…";
    const { body } = await api("/api/wizard/options");
    options = body;
    answers.password = body.suggested_password;
    answers.players = "4";
    answers.world_name = "Dedicated";
    if (body.hosts.length === 1) answers.host = body.hosts[0].key;  // the Windows app: this PC only
    drawStep();
}

function drawStep() {
    const shown = STEPS.map((s, i) => [s, i]).filter(([, i]) => !(i === 1 && options.hosts.length === 1));
    $("wizard-steps").replaceChildren(...shown.map(([s, i], n) =>
        el("li", { textContent: `${n + 1}. ${i === 2 && options.hosts.length === 1 ? "This PC" : s}`,
                   className: i === wizardStep ? "active" : "" })));
    $("wizard-error").textContent = "";
    $("wizard-back").hidden = wizardStep === 0;
    $("wizard-next").textContent = wizardStep === STEPS.length - 1 ? "Create server" : "Continue";
    const body = $("wizard-body");
    body.replaceChildren();
    [stepNotice, stepHost, stepDetails, stepServer, stepReview][wizardStep](body);
}

function stepNotice(body) {
    body.append(el("div", { className: "note bad notice", textContent: options.notice }),
                tick("understood", "I understand and want to continue"));
}

function stepHost(body) {
    options.hosts.forEach((h) => {
        const card = el("button", { className: "card" + (answers.host === h.key ? " chosen" : ""), textContent: h.label });
        if (h.problem) card.append(el("small", { className: "problem", textContent: h.problem }));
        h.warnings.forEach((w) => card.append(el("small", { textContent: w })));
        if (h.needs_network_setup) card.append(el("small", { textContent: "You set up port forwarding and firewall rules yourself." }));
        card.disabled = !!h.problem;
        card.onclick = () => { answers.host = h.key; hostOptions = null; reconnect = false; drawStep(); };
        body.append(card);
    });
}

async function loadHostOptions(extra = "") {
    const players = encodeURIComponent(answers.players || "4");
    const { status, body } = await api(`/api/wizard/options?host=${answers.host}&players=${players}${extra}`);
    if (status !== 200) throw new Error(body.error || "Could not load the choices for this host.");
    hostOptions = body;
}

function stepDetails(body) {
    const players = text("players", "4", "number");
    players.onchange = () => { hostOptions = null; drawStep(); };
    body.append(field("How many players at once?", players, "Sets the size of machine we recommend."));
    if (answers.host === "local" || answers.host === "windows") return detailsLocal(body);
    if (!hostOptions) {
        body.append(el("p", { className: "muted", textContent: "Loading choices…" }));
        loadHostOptions().then(drawStep).catch((e) => { $("wizard-error").textContent = e.message; });
        return;
    }
    if (answers.host === "existing") return detailsExisting(body);
    if (hostOptions.needs_connect || reconnect) return connectAccount(body);
    body.append(el("div", { className: "row" },
        el("span", { className: "muted", textContent: `Connected to ${hostOptions.connected}.` }),
        el("button", { textContent: "Use a different account", onclick: () => {
            reconnect = true; guideAt[answers.host] = "credentials"; drawStep(); } })));
    if (answers.host === "hetzner") detailsHetzner(body);
    if (answers.host === "aws") detailsAws(body);
}

// Numbered steps, each with an optional link that opens in a new tab.
function steps(list) {
    const ol = el("ol", { className: "guide" });
    list.forEach((step) => {
        const li = el("li", { textContent: step.text });
        if (step.link) li.append(" ", el("a", { href: step.link, target: "_blank", rel: "noopener noreferrer", textContent: "Open ↗" }));
        ol.append(li);
    });
    return ol;
}

function button(label, onclick) { return el("button", { textContent: label, onclick }); }

// Asks whether the user has an account, walks them through making one if
// not, then through getting the key or token this app needs.
function connectAccount(body) {
    const g = hostOptions.guide, host = answers.host;
    const go = (where) => { guideAt[host] = where; drawStep(); };
    const at = guideAt[host] || "ask";
    if (at === "ask") {
        body.append(el("p", { textContent: `To create servers for you, this app needs access to your ${g.service} account.` }),
                    el("h3", { textContent: `Do you already have an account with ${g.service}?` }),
                    el("div", { className: "row" },
                       button("Yes, I have an account", () => go("credentials")),
                       button("No, walk me through making one", () => go("signup"))));
        return;
    }
    if (at === "signup") {
        body.append(el("h3", { textContent: `Create your ${g.service} account` }), steps(g.signup),
                    el("div", { className: "row" }, button("My account is ready", () => go("credentials"))));
        return;
    }
    body.append(el("h3", { textContent: `Connect this app to ${g.service}` }), steps(g.credentials));
    const values = {};
    g.fields.forEach((f) => {
        const input = el("input", { type: f.secret ? "password" : "text", autocomplete: "off", spellcheck: false,
                                    oninput: (e) => { values[f.key] = e.target.value.trim(); } });
        body.append(field(f.label, input));
    });
    const result = el("span", { className: "muted" });
    const connect = button("Connect", async () => {
        connect.disabled = true;
        result.textContent = `Checking with ${g.service}…`;
        const res = await api("/api/wizard/connect", { method: "POST", body: JSON.stringify({ host, ...values }) });
        connect.disabled = false;
        if (res.status !== 200) { result.textContent = ""; $("wizard-error").textContent = res.body.error; return; }
        reconnect = false;
        hostOptions = null;
        drawStep();
    });
    body.append(el("p", { className: "muted", textContent: g.stored }),
                el("div", { className: "row" }, connect, result,
                   button("I don't have an account yet", () => go("signup"))));
}

function detailsHetzner(body) {
    const where = pick("hcloud_location", hostOptions.locations);
    where.value = hostOptions.location;
    answers.hcloud_location = hostOptions.location;
    where.onchange = async (e) => {
        answers.hcloud_location = e.target.value;
        await loadHostOptions(`&location=${encodeURIComponent(e.target.value)}`);
        drawStep();
    };
    body.append(field("Location (closest to your players)", where));
    if (!hostOptions.plans.length) {
        body.append(el("p", { className: "error-line", textContent: "No suitable plan here right now. Try another location." }));
        return;
    }
    body.append(field(`Plan (${hostOptions.recommended_gb} GB or more recommended)`, pick("hcloud_plan", hostOptions.plans),
                      "Live prices from Hetzner. Billed until the server is deleted."));
}

function detailsAws(body) {
    answers.aws_region = answers.aws_region || hostOptions.default_region;
    body.append(field("AWS region (closest to your players)", pick("aws_region", hostOptions.regions)),
                field(`Instance (${hostOptions.recommended_gb} GB recommended)`, pick("aws_instance_type", hostOptions.instance_types),
                      "Rough us-east-1 on-demand prices. Billed until the server is deleted."));
}

function detailsLocal(body) {
    const notes = options.hosts.find((h) => h.key === answers.host).warnings;
    notes.forEach((n) => body.append(el("div", { className: "note", textContent: n })));
    if (notes.length > 1) body.append(tick("arm_ok", "Continue anyway"));
}

function rentAServer(body) {
    body.append(el("h3", { textContent: "Rent a server" }), steps(hostOptions.vps_steps));
    if (!madeKey) {
        body.append(el("div", { className: "row" }, button("Make a key", async () => {
            const res = await api("/api/wizard/ssh-key", { method: "POST", body: "{}" });
            if (res.status !== 200) { $("wizard-error").textContent = res.body.error || "Could not make a key."; return; }
            madeKey = res.body;
            answers.ssh_key = madeKey.path;
            drawStep();
        })));
    } else {
        const key = el("textarea", { className: "key", readOnly: true, rows: 3, value: madeKey.public_key });
        body.append(field("Public key: paste this into the provider", key, `The private half stays here: ${madeKey.path}`),
                    el("div", { className: "row" }, button("Copy", () => navigator.clipboard.writeText(madeKey.public_key).then(() => toast("Copied")))));
    }
    body.append(el("div", { className: "row" }, button("My server is running", () => { guideAt.existing = "details"; drawStep(); })));
}

function detailsExisting(body) {
    const at = guideAt.existing || "ask";
    if (at === "ask") {
        body.append(el("h3", { textContent: "Do you already have a Linux server for this?" }),
                    el("div", { className: "row" },
                       button("Yes, I have one", () => { guideAt.existing = "details"; drawStep(); }),
                       button("No, help me rent one", () => { guideAt.existing = "rent"; drawStep(); })));
        return;
    }
    if (at === "rent") return rentAServer(body);
    answers.ssh_user = answers.ssh_user || "root";
    answers.ssh_port = answers.ssh_port || "22";
    const result = el("span", { className: "muted" });
    const test = el("button", { textContent: "Test the login" });
    test.onclick = async () => {
        result.textContent = "Testing…";
        const { body: r } = await api("/api/wizard/check-ssh", { method: "POST", body: JSON.stringify(answers) });
        result.textContent = r.problem || "SSH login works.";
    };
    body.append(
        el("div", { className: "note", textContent: "Needs x86-64 Ubuntu 20.04 or newer, 4 GB of RAM, 10 GB of free disk, and an SSH login that is root or has passwordless sudo." }),
        field("SSH address (IP or hostname)", text("ssh_host")),
        field("SSH user", text("ssh_user")),
        field("SSH port", text("ssh_port")),
        field("SSH private key path", text("ssh_key"), "Leave blank to use your default key."),
        field("Public IP or DNS name players will join", text("public_address"), "Leave blank to use the SSH address."),
        tick("manage_firewall", "Let the installer manage ufw (blocks everything except SSH and the game ports)"),
        el("div", { className: "row" }, test, result));
}

function stepServer(body) {
    body.append(
        field("Short name", text("name", "myworld"), "Lowercase letters, numbers and dashes."),
        field("Server name (shown to players)", text("server_name")),
        field("World name", text("world_name")),
        field("Join password", text("password"), "At least 5 characters, and not part of the server name."),
        field("Difficulty", pick("preset", [{ value: "", label: "Valheim default" }, ...options.presets.map((p) => ({ value: p, label: p }))])),
        field("Admin Steam IDs (optional)", text("admins", "Steam_76561198000000000"), "Comma separated."),
        field("Daily backup time on the server (24-hour)", text("backup_time", "05:30")),
        tick("public", "List it in the public community browser. Anyone can then find it, and the password is the only thing keeping them out."),
        tick("crossplay", "Allow Xbox and Game Pass players (crossplay). They join with a code."));
}

function stepReview(body) {
    const host = options.hosts.find((h) => h.key === answers.host);
    const rows = [["Host", host.label], ["Short name", answers.name], ["Server name", answers.server_name || answers.name],
                  ["World", answers.world_name], ["Difficulty", answers.preset || "Valheim default"],
                  ["Listed publicly", answers.public ? "yes" : "no"], ["Crossplay", answers.crossplay ? "yes" : "no"]];
    if (answers.hcloud_plan && answers.host === "hetzner") rows.push(["Plan", hostOptions.plans.find((p) => p.value === answers.hcloud_plan).label]);
    if (answers.host === "aws") rows.push(["Instance", answers.aws_instance_type], ["Region", answers.aws_region]);
    const tb = el("tbody");
    rows.forEach(([k, v]) => addRow(tb, k, String(v)));
    body.append(el("table", {}, tb));
    if (answers.host === "hetzner" || answers.host === "aws") {
        body.append(el("div", { className: "note bad", style: "margin-top:16px", textContent: "Creating this starts billing until you delete the server." }));
    }
}

function stepProblem() {
    if (wizardStep === 0 && !answers.understood) return "Tick the box to continue.";
    if (wizardStep === 1 && !answers.host) return "Choose where the server should run.";
    if (wizardStep === 2) {
        if (answers.host === "existing" && guideAt.existing !== "details") return "Answer the question above first.";
        if (answers.host === "existing" && !answers.ssh_host) return "Enter the SSH address.";
        if ((answers.host === "hetzner" || answers.host === "aws") && (!hostOptions || hostOptions.needs_connect || reconnect)) {
            return `Connect your ${answers.host === "aws" ? "AWS" : "Hetzner"} account first.`;
        }
        if (answers.host === "hetzner" && !answers.hcloud_plan) return "Choose a plan.";
        if (answers.host === "local" && options.hosts.find((h) => h.key === "local").warnings.length > 1 && !answers.arm_ok) return "Tick the box to continue.";
    }
    if (wizardStep === 3 && !/^[a-z0-9][a-z0-9-]{0,30}$/.test(answers.name || "")) return "Enter a short name: lowercase letters, numbers and dashes.";
    return "";
}

async function submitWizard() {
    answers.go = true;
    answers.manage_firewall = !!answers.manage_firewall;
    const { status, body } = await api("/api/servers", { method: "POST", body: JSON.stringify(answers) });
    if (status !== 202) { $("wizard-error").textContent = body.error || `Failed (${status})`; return; }
    followJob(body.job, `Creating ${answers.name}`, answers.name, answers.host === "windows" ? "network" : null);
}

// -- jobs ------------------------------------------------------------------

async function followJob(id, title, thenSelect, thenTab) {
    showOnly("job");
    $("job-title").textContent = title;
    $("job-status").textContent = "Working. Keep the app's window open. This can take several minutes.";
    $("job-close").hidden = true;
    const log = $("job-log");
    const poll = async () => {
        const { status, body } = await api(`/api/jobs/${id}`);
        if (status !== 200) return;
        const atBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 40;
        log.textContent = body.lines.join("\n");
        if (atBottom) log.scrollTop = log.scrollHeight;
        if (body.state === "running") { setTimeout(poll, 1500); return; }
        $("job-status").textContent = body.state === "done" ? "Finished." : `It stopped: ${body.error}`;
        $("job-close").hidden = false;
        $("job-close").onclick = async () => {
            if (thenSelect) localStorage.setItem("server", thenSelect);
            showOnly("content");
            await loadServers();
            if (thenTab && body.state === "done") document.querySelector(`[data-tab="${thenTab}"]`).click();
        };
    };
    poll();
}

async function startJob(what, body = {}) {
    const res = await api(serverPath(what), { method: "POST", body: JSON.stringify(body) });
    if (res.status === 202) followJob(res.body.job, `${what.replace(/-/g, " ")}: ${current}`);
    else toast(res.body.error || `Failed (${res.status})`, true);
}

// -- wiring ----------------------------------------------------------------

$("new-server").onclick = openWizard;
$("wizard-cancel").onclick = () => { showOnly("content"); };
// With only one place to host, there is nothing to choose on the "Where" step.
const skipWhere = () => options.hosts.length === 1 && wizardStep === 1;
$("wizard-back").onclick = () => { wizardStep -= 1; if (skipWhere()) wizardStep -= 1; drawStep(); };
$("wizard-next").onclick = () => {
    const problem = stepProblem();
    if (problem) { $("wizard-error").textContent = problem; return; }
    if (wizardStep === STEPS.length - 1) { submitWizard(); return; }
    wizardStep += 1;
    if (skipWhere()) wizardStep += 1;
    drawStep();
};
document.querySelectorAll("[data-job]").forEach((b) => { b.onclick = () => startJob(b.dataset.job); });
$("delete-server").onclick = () => {
    $("delete-title").textContent = `Delete ${current}?`;
    $("delete-name").value = "";
    $("confirm-delete").hidden = false;
};
$("delete-cancel").onclick = () => { $("confirm-delete").hidden = true; };
$("delete-go").onclick = () => {
    startJob("destroy", { confirm_name: $("delete-name").value.trim(), download: $("delete-download").checked });
};
