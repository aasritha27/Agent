// University Room Scheduler — frontend.
// Displays data from the API; never generates schedules itself.
import { h, render } from "https://esm.sh/preact@10.19.3";
import { useState, useEffect } from "https://esm.sh/preact@10.19.3/hooks";
import htm from "https://esm.sh/htm@3.1.1";

const html = htm.bind(h);
const CFG = window.CONFIG;

// ---- local session (self-hosted auth tokens from the API) ----
const TOKEN_KEY = "urs_token";
const EMAIL_KEY = "urs_email";
const getToken = () => localStorage.getItem(TOKEN_KEY);
const setSession = (token, email) => {
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(EMAIL_KEY, email);
};
const clearSession = () => { localStorage.removeItem(TOKEN_KEY); localStorage.removeItem(EMAIL_KEY); };

const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
const PERIODS = ["8:15-9:05", "9:05-9:55", "10:10-11:00", "11:00-11:50", "11:50-12:40",
  "1:40-2:30", "2:30-3:20", "3:20-4:05"];
const BREAK_AFTER = { 1: "Short break", 4: "Lunch" }; // column index after which a break renders

async function api(path, token, opts = {}) {
  // Timeout for long requests (/api/solve and /api/chat can take several seconds).
  // AbortController prevents the bare "Failed to fetch" from a dropped mid-flight
  // request; a caller-provided opts.signal is honored and extends the ceiling.
  const timeoutMs = (opts.signal ? 120000 : 60000);
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  // Chain the caller's signal (if any) onto ours.
  const callerSig = opts.signal;
  const onAbort = () => ctrl.abort();
  if (callerSig && callerSig.addEventListener) callerSig.addEventListener("abort", onAbort);
  let res;
  try {
    res = await fetch(CFG.apiUrl + path, {
      ...opts,
      signal: ctrl.signal,
      headers: {
        ...(opts.body && !(opts.body instanceof FormData) ? { "Content-Type": "application/json" } : {}),
        ...(token ? { Authorization: "Bearer " + token } : {}),
        ...(opts.headers || {}),
      },
    });
  } catch (e) {
    if (ctrl.signal.aborted && !(callerSig && callerSig.aborted)) {
      throw new Error("Request timed out after " + (timeoutMs / 1000) + "s — the server may still be working. Try again, or wait and refresh.");
    }
    throw e;
  } finally {
    clearTimeout(timer);
    if (callerSig && callerSig.removeEventListener) callerSig.removeEventListener("abort", onAbort);
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || res.statusText);
  return data;
}

// ---------- XLSX writer (self-contained; 32-bit constants as hex) ----------
const CRC_TABLE = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1;
    t[n] = c >>> 0;
  }
  return t;
})();
function crc32(bytes) {
  let c = 0xFFFFFFFF;
  for (let i = 0; i < bytes.length; i++) c = CRC_TABLE[(c ^ bytes[i]) & 0xFF] ^ (c >>> 8);
  return (c ^ 0xFFFFFFFF) >>> 0;
}
function zipStore(files) {
  const enc = new TextEncoder();
  const parts = [], central = [];
  let offset = 0;
  for (const [name, content] of files) {
    const nb = enc.encode(name), cb = typeof content === "string" ? enc.encode(content) : content;
    const crc = crc32(cb);
    const head = new Uint8Array(30 + nb.length);
    const dv = new DataView(head.buffer);
    head.set([0x50, 0x4b, 3, 4]);
    dv.setUint16(4, 20, true); dv.setUint16(6, 0, true); dv.setUint16(8, 0, true);
    dv.setUint32(14, crc, true);
    dv.setUint32(18, cb.length, true); dv.setUint32(22, cb.length, true);
    dv.setUint16(26, nb.length, true);
    head.set(nb, 30);
    parts.push(head, cb);
    const cen = new Uint8Array(46 + nb.length);
    const cv = new DataView(cen.buffer);
    cen.set([0x50, 0x4b, 1, 2]);
    cv.setUint16(4, 20, true); cv.setUint16(6, 20, true);
    cv.setUint32(16, crc, true);
    cv.setUint32(20, cb.length, true); cv.setUint32(24, cb.length, true);
    cv.setUint16(28, nb.length, true);
    cv.setUint32(42, offset, true);
    cen.set(nb, 46);
    central.push(cen);
    offset += head.length + cb.length;
  }
  const centralLen = central.reduce((a, c) => a + c.length, 0);
  const end = new Uint8Array(22);
  const ev = new DataView(end.buffer);
  end.set([0x50, 0x4b, 5, 6]);
  ev.setUint16(8, central.length, true); ev.setUint16(10, central.length, true);
  ev.setUint32(12, centralLen, true); ev.setUint32(16, offset, true);
  return new Blob([...parts, ...central, end], { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" });
}
const esc = (s) => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
function colName(i) {
  let s = "";
  i += 1;
  while (i > 0) { s = String.fromCharCode(64 + ((i - 1) % 26) + 1) + s; i = Math.floor((i - 1) / 26); }
  return s;
}
function sheetXml(rows, merges = []) {
  const body = rows.map((r, ri) =>
    "<row r=\"" + (ri + 1) + "\">" + r.map((cell, ci) => {
      if (cell === null || cell === undefined) return "";
      const ref = colName(ci) + (ri + 1);
      if (cell instanceof Object) {
        return "<c r=\"" + ref + "\" s=\"1\" t=\"inlineStr\"><is><t>" + esc(cell.v) + "</t></is></c>";
      }
      return "<c r=\"" + ref + "\" t=\"inlineStr\"><is><t>" + esc(cell) + "</t></is></c>";
    }).join("") + "</row>").join("");
  const mg = merges.map(m => "<mergeCell ref=\"" + m + "\"/>").join("");
  return "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>" +
    "<worksheet><sheetData>" + body + "</sheetData>" +
    (mg ? "<mergeCells count=\"" + merges.length + "\">" + mg + "</mergeCells>" : "") +
    "</worksheet>";
}
function stylesXml() {
  return "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>" +
    "<styleSheet><fonts count=\"3\">" +
    "<font><sz val=\"11\"/><name val=\"Calibri\"/></font>" +
    "<font><b/><sz val=\"11\"/><color rgb=\"FFFFFFFF\"/><name val=\"Calibri\"/></font>" +
    "<font><b/><sz val=\"11\"/><name val=\"Calibri\"/></font>" +
    "</fonts><fills count=\"3\">" +
    "<fill><patternFill patternType=\"none\"/></fill>" +
    "<fill><patternFill patternType=\"gray125\"/></fill>" +
    "<fill><patternFill patternType=\"solid\"><fgColor rgb=\"FF25352B\"/><bgColor indexed=\"64\"/></patternFill></fill>" +
    "</fills><cellXfs count=\"2\">" +
    "<xf fontId=\"0\" fillId=\"0\"/><xf fontId=\"1\" fillId=\"2\"/>" +
    "</cellXfs></styleSheet>";
}
function downloadXlsx(sheets, filename) {
  const sheetFiles = sheets.map(([name, rows, merges], i) => {
    const n = i + 1;
    return [
      [`xl/worksheets/sheet${n}.xml`, sheetXml(rows, merges || [])],
    ].concat([[`xl/worksheets/_rels/sheet${n}.xml.rels`,
      `<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="/xl/styles.xml"/></Relationships>`]]);
  }).flat();
  const wb = `<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>` +
    sheets.map(([name], i) => `<sheet name="${esc(name).slice(0, 31)}" sheetId="${i + 1}" r:id="rId${i + 1}"/>`).join("") +
    `</sheets></workbook>`;
  const wbRels = `<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">` +
    sheets.map((_, i) => `<Relationship Id="rId${i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet${i + 1}.xml"/>`).join("") +
    `</Relationships>`;
  const files = [
    ["[Content_Types].xml",
      `<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>${sheets.map((_, i) => `<Override PartName="/xl/worksheets/sheet${i + 1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>`).join("")}<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>`],
    ["_rels/.rels",
      `<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>`],
    ["xl/workbook.xml", wb],
    ["xl/_rels/workbook.xml.rels", wbRels],
    ["xl/styles.xml", stylesXml()],
    ...sheetFiles,
  ];
  const blob = zipStore(files);
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}

// ---------- app ----------
function App() {
  const [session, setSession] = useState(null); // { email }
  const [role, setRole] = useState(null);
  const [tab, setTab] = useState("timetable");
  const [uni, setUni] = useState(null);
  const [schedule, setSchedule] = useState(null);

  useEffect(() => {
    const t = getToken();
    if (t) loadMe(t);
  }, []);

  const loadMe = async (token) => {
    try {
      const me = await api("/api/me", token);
      setSession({ email: me.email });
      setRole(me.role);
      const u = await api("/api/university", token);
      setUni(u);
      const sch = await api("/api/schedule", token);
      setSchedule(sch.status === "ok" ? sch : null);
    } catch (e) {
      clearSession();
      setSession(null);
      setRole(null);
    }
  };

  const signOut = () => { clearSession(); setSession(null); setRole(null); };
  if (!session) return html`<${Login} onSignedIn=${loadMe} />`;
  const token = getToken();
  const tabs = [
    ["timetable", "Timetable"],
    ["data", "University data"],
    ...(role === "coordinator" ? [["generate", "Generate"]] : []),
    ["requests", "Room requests"],
    ["letter", "Workshop letter"],
    ["askai", "Ask AI"],
  ];
  const ICONS = { timetable: "▦", data: "⛁", generate: "✦", requests: "✉", letter: "🗎", askai: "✳" };
  const TITLES = { timetable: "Weekly allocation board", data: "University data", generate: "Generate timetable", requests: "Room requests", letter: "Workshop letter", askai: "Ask Roomline" };
  const initials = (session.email || "?").slice(0, 2).toUpperCase();
  return html`
    <div class="shell">
      <aside class="side">
        <div class="brand">
          <span class="mark">◆</span>
          <div class="brand-text"><small>Academic Operations</small><h3>Roomline</h3></div>
        </div>
        <nav class="side-nav">
          ${tabs.map(([id, label]) => html`<button key=${id} class=${tab === id ? "on" : ""} onClick=${() => setTab(id)}><span class="ic">${ICONS[id]}</span>${label}</button>`)}
        </nav>
        <div class="side-foot">
          <div class="policy">
            <small>Active policy</small>
            <b>Hard constraints only</b>
            <p>Times, faculty, and room rules are enforced exactly; infeasibility is reported, never papered over.</p>
          </div>
          <div class="who">
            <span class="avatar">${initials}</span>
            <span class="who-text">${session.email}<br/><b>${role}</b> · <a href="#" onClick=${(e) => { e.preventDefault(); signOut(); }}>Sign out</a></span>
          </div>
        </div>
      </aside>
      <main class="board">
        <div class="topbar">
          <div>
            <small class="crumb">ACSE Department · Timetable Prototype</small>
            <h1 class="page-title">${TITLES[tab]}</h1>
          </div>
        </div>
        ${tab === "timetable" && html`<${Timetable} token=${token} uni=${uni} schedule=${schedule} setSchedule=${setSchedule} role=${role} />`}
        ${tab === "data" && html`<${Data} token=${token} uni=${uni} reload=${() => api("/api/university", token).then(setUni)} role=${role} />`}
        ${tab === "generate" && html`<${Generate} token=${token} setSchedule=${setSchedule} />`}
        ${tab === "requests" && html`<${Requests} token=${token} uni=${uni} role=${role} />`}
        ${tab === "letter" && html`<${Letter} />`}
        ${tab === "askai" && html`<${AskAI} token=${token} role=${role} />`}
      </main>
    </div>
  `;
}

function Login({ onSignedIn }) {
  const [email, setEmail] = useState("");
  const [pass, setPass] = useState("");
  const [mode, setMode] = useState("signin");
  const [err, setErr] = useState("");
  const [note, setNote] = useState("");
  const submit = async (e) => {
    e.preventDefault();
    setErr(""); setNote("");
    try {
      const fd = new FormData();
      fd.append("email", email);
      fd.append("password", pass);
      const res = await fetch(CFG.apiUrl + (mode === "signin" ? "/api/auth/login" : "/api/auth/signup"), { method: "POST", body: fd });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) { setErr(data.detail || "Sign in failed"); return; }
      setSession(data.token, data.user.email);
      if (mode === "signup") {
        try { await api("/api/claim-coordinator", data.token, { method: "POST" }); setNote("You are the first user — you are now the Timetable Coordinator."); } catch (_) {}
      }
      onSignedIn(data.token);
    } catch (ex) { setErr(ex.message); }
  };
  return html`
    <div class="card auth">
      <h2>${mode === "signin" ? "Sign in" : "Create account"}</h2>
      ${err && html`<div class="msg err">${err}</div>`}
      ${note && html`<div class="msg ok">${note}</div>`}
      <form onSubmit=${submit}>
        <label class="f"><span>Email</span><input type="email" required value=${email} onInput=${(e) => setEmail(e.target.value)} /></label>
        <label class="f"><span>Password</span><input type="password" required minlength="6" value=${pass} onInput=${(e) => setPass(e.target.value)} /></label>
        <div class="row">
          <button class="primary" type="submit">${mode === "signin" ? "Sign in" : "Sign up"}</button>
          <button class="subtle" type="button" onClick=${() => setMode(mode === "signin" ? "signup" : "signin")}>
            ${mode === "signin" ? "Need an account?" : "Have an account?"}
          </button>
        </div>
      </form>
      <p class="statline">Coordinators generate and approve schedules; faculty view timetables and submit room requests. The first account created becomes the Coordinator.</p>
    </div>`;
}

function Timetable({ token, uni, schedule, setSchedule, role }) {
  const [mode, setMode] = useState("section");
  const [entity, setEntity] = useState("");
  useEffect(() => { if (schedule && !entity) setEntity(defaultEntity(mode, uni, schedule)); }, [schedule, mode]);
  if (!schedule) return html`<div class="card"><h2>Timetable</h2><div class="msg info">No schedule yet. ${role === "coordinator" ? "Use the Generate tab." : "Ask the coordinator to generate one."}</div></div>`;

  const ents = entities(mode, uni, schedule);
  const cur = ents.find((e) => e.id === entity) || ents[0];
  const grid = buildGrid(mode, cur, uni, schedule);

  const dl = () => {
    const rows = [[cur.name + " — weekly timetable"], ["Period", ...DAYS]];
    const merges = [];
    for (let p = 0; p < PERIODS.length; p++) {
      if (BREAK_AFTER[p]) rows.push([BREAK_AFTER[p] === "Lunch" ? "Lunch 12:40-1:40" : "Short break 9:55-10:10", "", "", "", "", "", ""]);
      const line = [PERIODS[p]];
      for (let d = 0; d < DAYS.length; d++) {
        const cell = grid.cells[d][p];
        line.push(cell ? { v: cell } : "");
      }
      rows.push(line);
    }
    downloadXlsx([[cur.name.slice(0, 28), rows, merges]], cur.name.replace(/[^\w-]+/g, "_") + ".xlsx");
  };

  return html`
    <div class="stats-row">
      <div class="tile"><small>Teaching hours</small><b>${schedule.stats?.weeklyHours || 0}<span class="of">/850</span></b><p>allocated · required weekly</p></div>
      <div class="tile"><small>Conflicts</small><b>0</b><p>hard constraints</p></div>
      <div class="tile"><small>Rooms</small><b>${uni?.rooms?.length || 0}</b><p>${uni?.rooms?.filter((r) => r.type === "Lab").length || 0} labs · ${uni?.rooms?.filter((r) => r.type !== "Lab").length || 0} classrooms</p></div>
      <div class="tile"><small>Faculty</small><b>${uni?.faculty?.length || 0}</b><p>sections: ${uni?.sections?.length || 0}</p></div>
    </div>
    <div class="card">
      <h2>Timetable</h2>
      <div class="row">
        <select value=${mode} onChange=${(e) => { setMode(e.target.value); setEntity(""); }} style="width:auto">
          <option value="section">By Section</option>
          <option value="room">By Room</option>
          <option value="faculty">By Faculty</option>
        </select>
        <select value=${cur?.id || ""} onChange=${(e) => setEntity(e.target.value)} style="width:auto">
          ${ents.map((x) => html`<option key=${x.id} value=${x.id}>${x.name}</option>`)}
        </select>
        <button class="subtle" onClick=${dl}>Download XLSX</button>
        <span class="statline">${schedule.stats?.weeklyHours} periods/week · generated ${new Date(schedule.createdAt).toLocaleString()}</span>
      </div>
      <${GridTable} grid=${grid} />
    </div>`;
}

function defaultEntity(mode, uni, schedule) {
  if (mode === "section") return uni.sections[0]?.id;
  if (mode === "room") return uni.rooms[0]?.id;
  return uni.faculty[0]?.id;
}
function entities(mode, uni, schedule) {
  if (mode === "section") return uni.sections.map((s) => ({ id: s.id, name: s.name }));
  if (mode === "room") return uni.rooms.map((r) => ({ id: r.id, name: r.name }));
  return uni.faculty.map((f) => ({ id: f.id, name: f.name }));
}
function buildGrid(mode, ent, uni, schedule) {
  const look = {
    section: Object.fromEntries(uni.sections.map((s) => [s.id, s])),
    room: Object.fromEntries(uni.rooms.map((r) => [r.id, r])),
    faculty: Object.fromEntries(uni.faculty.map((f) => [f.id, f])),
  };
  const subjById = Object.fromEntries(uni.subjects.map((s) => [s.id, s]));
  const linkById = Object.fromEntries(uni.section_subjects.map((g) => [g.section_id + "|" + g.subject_id, g]));
  const facById = Object.fromEntries(uni.faculty.map((f) => [f.id, f]));
  const roomById = Object.fromEntries(uni.rooms.map((r) => [r.id, r]));
  const secById = Object.fromEntries(uni.sections.map((s) => [s.id, s]));
  const cells = DAYS.map(() => Array(PERIODS.length).fill(""));
  const tones = DAYS.map(() => Array(PERIODS.length).fill(""));
  for (const sess of schedule.sessions) {
    const match =
      (mode === "section" && sess.sectionId === ent.id) ||
      (mode === "room" && sess.roomId === ent.id) ||
      (mode === "faculty" && sess.facultyId === ent.id);
    if (!match) continue;
    const sub = linkById[sess.sectionId + "|" + sess.courseId.replace(/-practical|-lecture/, "")];
    const title = sub ? `${sub.subject_id ? "" : ""}${(uni.subjects.find((s) => s.id === sub.subject_id) || {}).name || sess.courseId}` : sess.courseId;
    const isPrac = sess.courseId.endsWith("-practical");
    for (let w = 0; w < sess.duration; w++) {
      const d = DAYS.indexOf(sess.day), p = sess.slot + w;
      if (d < 0 || p >= PERIODS.length) continue;
      let line = title;
      if (mode !== "faculty") line += "\n" + (facById[sess.facultyId]?.name || "");
      if (mode !== "room") line += "\n" + (roomById[sess.roomId]?.name || sess.roomId);
      if (mode !== "section") line += "\n" + (secById[sess.sectionId]?.name || "");
      const toneKey = sub ? sub.subject_id : sess.courseId;
      let h = 0;
      for (let i = 0; i < toneKey.length; i++) h = (h * 31 + toneKey.charCodeAt(i)) >>> 0;
      const tone = "tone" + (h % 8);
      for (let w = 0; w < sess.duration; w++) {
        const d = DAYS.indexOf(sess.day), p = sess.slot + w;
        if (d < 0 || p >= PERIODS.length) continue;
        tones[d][p] = tone;
        cells[d][p] = line;
      }
    }
  }
  return { cells, tones, isPrac: cells.map((row, d) => row.map((c, p) => schedule.sessions.some((s) => s.duration === 2 && DAYS[d] === s.day && s.slot === p))) };
}

function GridTable({ grid }) {
  const head = html`<tr><th>Period</th>${DAYS.map((d) => html`<th key=${d}>${d}</th>`)}</tr>`;
  const body = [];
  for (let p = 0; p < PERIODS.length; p++) {
    if (BREAK_AFTER[p]) body.push(html`<tr key=${"b" + p}><td class="brk" colspan="2">🞄</td><td class="brk" colspan="6">${BREAK_AFTER[p]}</td></tr>`);
    body.push(html`<tr key=${p}>
      <th>${"P" + (p + 1)}<br/><small>${PERIODS[p]}</small></th>
      ${DAYS.map((d, di) => html`<td key=${d} class=${"cell " + (grid.tones[di][p] || "tone-empty") + (grid.isPrac[di][p] ? " prac" : "")}>${grid.cells[di][p] ? grid.cells[di][p].split("\n").map((l, i) => i === 0 ? html`<b>${l}</b>` : html`<small>${l}<br/></small>`) : ""}</td>`)}
    </tr>`);
  }
  return html`<table class="grid">${head}${body}</table>`;
}

function Data({ token, uni, reload, role }) {
  const [busy, setBusy] = useState("");
  const [result, setResult] = useState(null);
  const [err, setErr] = useState("");
  const upload = async (kind, file) => {
    setBusy(kind); setErr(""); setResult(null);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const res = await api("/api/import/" + kind, token, { method: "POST", body: fd });
      setResult(res);
      reload();
    } catch (e) { setErr(e.message); }
    setBusy("");
  };
  const seed = async () => {
    setBusy("seed"); setErr("");
    try { setResult(await api("/api/seed", token, { method: "POST" })); reload(); }
    catch (e) { setErr(e.message); }
    setBusy("");
  };
  if (!uni) return html`<div class="card"><div class="msg info">Loading…</div></div>`;
  const counts = { Faculty: uni.faculty.length, Rooms: uni.rooms.length, Sections: uni.sections.length, Subjects: uni.subjects.length, Assignments: uni.section_subjects.length };
  return html`
    <div class="card">
      <h2>University data</h2>
      <div class="statline">${Object.entries(counts).map(([k, v]) => k + ": " + v).join(" · ")}</div>
      ${err && html`<div class="msg err">${err}</div>`}
      ${result && html`<div class="msg ok">Imported ${result.imported} rows.${result.errors?.length ? html`<div class="errlist">${result.errors.map((e2) => "Row " + e2.row + ": " + e2.reason).map((x) => html`<div>${x}</div>`)}</div>` : ""}</div>`}
      ${role === "coordinator" && html`
        <div class="row">
          ${["faculty", "rooms", "sections", "subjects", "assignments"].map((k) => html`
            <label class="f" key=${k}><span>Upload ${k} (CSV/XLSX)</span>
              <input type="file" accept=".csv,.xlsx,.xls" disabled=${busy === k}
                onChange=${(e) => e.target.files[0] && upload(k, e.target.files[0])} />
            </label>`)}
        </div>
        <div class="row">
          <button class="subtle" onClick=${seed} disabled=${busy === "seed"}>Load bundled department data (seed)</button>
        </div>`}
      <${UniTable} uni=${uni} />
    </div>`;
}

function UniTable({ uni }) {
  return html`
    <table class="grid">
      <tr><th>Section</th><th>Students</th><th>Subjects (code · name · faculty · lecture+practical h)</th></tr>
      ${uni.sections.map((s) => {
        const subs = uni.section_subjects.filter((g) => g.section_id === s.id);
        const subName = (g) => { const x = uni.subjects.find((y) => y.id === g.subject_id); return x ? x.code + " · " + x.name : g.subject_id; };
        const facName = (g) => (uni.faculty.find((f) => f.id === g.faculty_id) || {}).name || g.faculty_id;
        return html`<tr key=${s.id}><td>${s.name}</td><td>${s.students}</td>
          <td>${subs.map((g) => html`<div key=${g.subject_id}>${subName(g)} — ${facName(g)} — ${g.lecture_hours}+${g.practical_hours}h</div>`)}</td></tr>`;
      })}
    </table>`;
}

function Generate({ token, setSchedule }) {
  const [busy, setBusy] = useState(false);
  const [out, setOut] = useState(null);
  const run = async () => {
    setBusy(true); setOut(null);
    try { setOut(await api("/api/solve", token, { method: "POST" })); }
    catch (e) { setOut({ status: "error", detail: e.message }); }
    setBusy(false);
  };
  useEffect(() => { if (out?.status === "ok") api("/api/schedule", token).then(setSchedule); }, [out]);
  return html`
    <div class="card">
      <h2>Generate timetable</h2>
      <p class="statline">The backend models every hard constraint with the CP-SAT solver: no section/faculty/room double-booking, one practical per section per day, room stability within each teaching block, labs only for practicals, room capacity, faculty daily caps, one free day per section. If the data makes a valid timetable impossible, it says so explicitly instead of failing silently.</p>
      <button class="primary" onClick=${run} disabled=${busy}>${busy ? "Solving…" : "Generate schedule"}</button>
      ${out && out.status === "ok" && html`<div class="msg ok">Schedule generated: ${JSON.stringify(out.stats)}</div>`}
      ${out && out.status === "infeasible" && html`<div class="msg err">No valid timetable exists for the current data. ${out.detail || ""}</div>`}
      ${out && out.status === "unknown" && html`<div class="msg err">${out.detail}</div>`}
      ${out && out.status === "error" && html`<div class="msg err">${out.detail}</div>`}
    </div>`;
}

function Requests({ token, uni, role }) {
  const [list, setList] = useState([]);
  const [err, setErr] = useState("");
  const [form, setForm] = useState({ section_id: "", preferred_room: "", reason: "", letter: null });
  const load = () => api("/api/requests", token).then((r) => setList(r.requests || [])).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, []);
  const submit = async (e) => {
    e.preventDefault(); setErr("");
    try {
      const fd = new FormData();
      fd.append("section_id", form.section_id);
      fd.append("preferred_room", form.preferred_room);
      fd.append("reason", form.reason);
      if (form.letter) fd.append("letter", form.letter);
      await api("/api/requests", token, { method: "POST", body: fd });
      setForm({ section_id: "", preferred_room: "", reason: "", letter: null });
      load();
    } catch (ex) { setErr(ex.message); }
  };
  const decide = async (id, status) => {
    const fd = new FormData(); fd.append("status", status);
    await api(`/api/requests/${id}/decision`, token, { method: "POST", body: fd });
    load();
  };
  const letterUrl = async (id) => {
    try {
      const res = await fetch(`${CFG.apiUrl}/api/requests/${id}/letter`, { headers: { Authorization: "Bearer " + getToken() } });
      if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error(e.detail || "Download failed"); }
      const blob = await res.blob();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = "request-letter";
      a.click();
      URL.revokeObjectURL(a.href);
    } catch (e) { setErr(e.message); }
  };
  return html`
    ${role !== "coordinator" && html`
    <div class="card">
      <h2>Submit a room request</h2>
      ${err && html`<div class="msg err">${err}</div>`}
      <form onSubmit=${submit}>
        <label class="f"><span>Section</span>
          <select required value=${form.section_id} onChange=${(e) => setForm({ ...form, section_id: e.target.value })}>
            <option value="">Choose…</option>
            ${(uni?.sections || []).map((s) => html`<option key=${s.id} value=${s.id}>${s.name}</option>`)}
          </select></label>
        <label class="f"><span>Preferred room (optional)</span>
          <select value=${form.preferred_room} onChange=${(e) => setForm({ ...form, preferred_room: e.target.value })}>
            <option value="">No preference</option>
            ${(uni?.rooms || []).map((r) => html`<option key=${r.id} value=${r.id}>${r.name} (${r.type}, seats ${r.capacity})</option>`)}
          </select></label>
        <label class="f"><span>Reason</span><textarea required value=${form.reason} onInput=${(e) => setForm({ ...form, reason: e.target.value })} /></label>
        <label class="f"><span>Request letter (PDF/image)</span><input type="file" onChange=${(e) => setForm({ ...form, letter: e.target.files[0] })} /></label>
        <button class="primary" type="submit">Submit request</button>
      </form>
    </div>`}
    <div class="card">
      <h2>${role === "coordinator" ? "All room requests" : "My requests"}</h2>
      ${err && html`<div class="msg err">${err}</div>`}
      <table class="grid">
        <tr><th>When</th>${role === "coordinator" ? html`<th>From</th>` : ""}<th>Section</th><th>Preferred room</th><th>Reason</th><th>Letter</th><th>Status</th>${role === "coordinator" ? html`<th>Decision</th>` : ""}</tr>
        ${list.map((r) => html`<tr key=${r.id}>
          <td>${new Date(r.created_at).toLocaleDateString()}</td>
          ${role === "coordinator" ? html`<td>${r.user_name || r.user_email || r.user_id.slice(0, 8)}</td>` : ""}
          <td>${(uni?.sections || []).find((s) => s.id === r.section_id)?.name || r.section_id}</td>
          <td>${(uni?.rooms || []).find((x) => x.id === r.preferred_room)?.name || "—"}</td>
          <td>${r.reason}</td>
          <td>${r.letter_path ? html`<button class="mini subtle" onClick=${() => letterUrl(r.id)}>Open</button>` : "—"}</td>
          <td><span class="badge ${r.status}">${r.status}</span></td>
          ${role === "coordinator" && r.status === "pending" ? html`<td><button class="mini approve" onClick=${() => decide(r.id, "approved")}>Approve</button> <button class="mini reject" onClick=${() => decide(r.id, "rejected")}>Reject</button></td>` : role === "coordinator" ? html`<td></td>` : ""}
        </tr>`)}
        ${!list.length && html`<tr><td colspan="7" class="statline">No requests yet.</td></tr>`}
      </table>
    </div>`;
}

function Letter() {
  const [f, setF] = useState({ dept: "", course: "", date: "", faculty: "", topic: "", room: "" });
  const text = `DEPARTMENT OF COMPUTER SCIENCE
Workshop Conduct Request

Date: ${f.date || "________"}

To,
The Head of Department,
Department of Computer Science.

Subject: Permission to conduct a workshop${f.topic ? " on " + f.topic : ""}.

Respected Madam/Sir,

The faculty member ${f.faculty || "________"} of the ${f.dept || "________"} department requests permission to conduct a workshop for ${f.course || "________"} on ${f.date || "________"}${f.room ? " in " + f.room : ""}. The workshop is part of the department's skill-development initiative and will provide hands-on exposure to the participants.

Kindly grant permission for the same.

Thanking you,

Yours faithfully,
${f.faculty || "________"}
${f.dept || "Department of Computer Science"}`;
  return html`
    <div class="card">
      <h2>Workshop letter</h2>
      <div class="row">
        ${["dept|Department", "faculty|Faculty name", "course|Audience / course", "topic|Workshop topic", "date|Date", "room|Venue"].map((p) => {
          const [k, label] = p.split("|");
          return html`<label class="f" key=${k} style="width:200px"><span>${label}</span><input value=${f[k]} onInput=${(e) => setF({ ...f, [k]: e.target.value })} /></label>`;
        })}
      </div>
      <textarea readOnly rows="18" style="font-family:Georgia,serif;white-space:pre-wrap">${text}</textarea>
      <button class="subtle" style="margin-top:8px" onClick=${() => navigator.clipboard.writeText(text)}>Copy letter</button>
    </div>`;
}

function AskAI({ token, role }) {
  const [msgs, setMsgs] = useState([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const endRef = (el) => { if (el) el.scrollIntoView({ behavior: "smooth" }); };

  const send = async (text) => {
    const message = (text || input).trim();
    if (!message || busy) return;
    setErr(""); setInput(""); setBusy(true);
    const history = msgs.map((m) => ({ role: m.who === "ai" ? "model" : "user", text: m.text }));
    setMsgs((m) => [...m, { who: "you", text: message }]);
    try {
      const res = await fetch(CFG.apiUrl + "/api/chat", { method: "POST", headers: { Authorization: "Bearer " + getToken(), "Content-Type": "application/json" }, body: JSON.stringify({ message, history }) });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || "The assistant is unavailable right now");
      setMsgs((m) => [...m, { who: "ai", text: data.reply }]);
    } catch (e) { setErr(e.message); }
    setBusy(false);
  };

  const examples = role === "coordinator"
    ? ["How many faculty teach more than 3 periods on Monday?", "Show me AI&ML Year 2 Section A's timetable", "Which rooms are free on Wednesday P3?"]
    : ["Show me my section's timetable", "Which faculty teach the most on Monday?", "How many practicals are scheduled this week?"];

  return html`
    <div class="chatwrap">
      <div class="chatlog">
        ${!msgs.length && html`<div class="chat-hello card"><h2>Ask Roomline</h2>
          <p class="statline">I can read the live timetable, the university data, and ${role === "coordinator" ? "edit data and regenerate the schedule for you" : "answer questions (edits and generation are coordinator-only)"}.</p>
          <div class="row" style="margin-top:10px">${examples.map((x) => html`<button class="chip" key=${x} onClick=${() => send(x)}>${x}</button>`)}</div>
        </div>`}
        ${msgs.map((m, i) => html`<div key=${i} class=${"bubble " + (m.who === "ai" ? "ai" : "you")}>${m.text}</div>`)}
        ${busy && html`<div class="bubble ai thinking">Working on it…</div>`}
        <div ref=${endRef}></div>
      </div>
      ${err && html`<div class="msg err">${err}</div>`}
      <form class="chatbar" onSubmit=${(e) => { e.preventDefault(); send(); }}>
        <input placeholder="Ask about the timetable, rooms, faculty…" value=${input} onInput=${(e) => setInput(e.target.value)} />
        <button class="primary" disabled=${busy}>Send</button>
      </form>
    </div>`;
}

render(html`<${App} />`, document.getElementById("app"));
