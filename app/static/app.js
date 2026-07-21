/* Boleto WS-D SPA — vanilla JS, house style (no framework).
   All UI text comes from strings.json; NO hardcoded UI strings live here. The
   language slide switch re-renders the current screen and swaps the TTS voice
   instantly, mid-session. The receipt gate (F4) is enforced server-side — this
   client only reflects it. */
(() => {
  const app = document.getElementById("app");
  const backbtn = document.getElementById("backbtn");
  const langsw = document.getElementById("langswitch");
  const ttsAudio = document.getElementById("tts");

  const S = {
    lang: localStorage.getItem("boleto_lang") || "es",
    strings: null,
    sid: localStorage.getItem("boleto_sid") || null,
    screen: "capture",
    stack: [],
    render: null,        // re-runs current screen on language change
  };
  const t = (k) => (S.strings?.[S.lang]?.[k]) ?? k;
  const api = async (path, opts) => {
    const r = await fetch(path, opts);
    return { ok: r.ok, status: r.status, body: await r.json().catch(() => ({})) };
  };

  // ── language switch ────────────────────────────────────────────────────────
  function applyLang() {
    langsw.setAttribute("aria-checked", S.lang === "en" ? "true" : "false");
    langsw.setAttribute("aria-label", t("lang_toggle_aria"));
    document.documentElement.lang = S.lang;
    document.querySelectorAll("#tabbar .tab").forEach((b) => {
      const key = { capture: "nav_new", history: "nav_history",
                    evals: "nav_evals", trust: "nav_trust" }[b.dataset.screen];
      b.querySelector("span").textContent = t(key);
    });
  }
  langsw.addEventListener("click", () => {
    S.lang = S.lang === "es" ? "en" : "es";
    localStorage.setItem("boleto_lang", S.lang);
    applyLang();
    if (S.render) S.render();          // instant re-render, mid-session, no confirm
  });

  // ── navigation ─────────────────────────────────────────────────────────────
  function go(screen, opts = {}) {
    if (!opts.replace && S.screen) S.stack.push(S.screen);
    S.screen = screen;
    backbtn.hidden = S.stack.length === 0;
    document.querySelectorAll("#tabbar .tab").forEach((b) =>
      b.classList.toggle("active", b.dataset.screen === screen));
    SCREENS[screen]();
  }
  backbtn.addEventListener("click", () => {
    const prev = S.stack.pop();
    if (prev) go(prev, { replace: true });
    backbtn.hidden = S.stack.length === 0;
  });
  document.querySelectorAll("#tabbar .tab").forEach((b) =>
    b.addEventListener("click", () => { S.stack = []; go(b.dataset.screen, { replace: true }); }));

  const h = (html) => { app.innerHTML = `<div class="screen">${html}</div>`; };
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  // ── SCREEN: capture ────────────────────────────────────────────────────────
  const SCREENS = {};
  SCREENS.capture = () => {
    S.render = SCREENS.capture;
    const days = [1, 2, 3, 4, 5].map((i) =>
      `<div class="thumb"><img src="/ticket/day_0${i}.png" alt="">
         <div class="tag">${t("capture_kind_ticket")} ${i}</div></div>`).join("");
    h(`<h1>${t("capture_title")}</h1><p class="help">${t("capture_help")}</p>
       <div class="card"><div class="thumbs">${days}
         <div class="thumb"><img src="/ticket/stub.png" alt="">
           <div class="tag">${t("capture_kind_stub")}</div></div></div></div>
       <button class="btn" id="start">📷 ${t("capture_start")}</button>`);
    document.getElementById("start").onclick = async () => {
      const { body } = await api("/api/session", { method: "POST" });
      S.sid = body.session_id;
      localStorage.setItem("boleto_sid", S.sid);
      go("extract");
    };
  };

  // ── SCREEN: extract (SSE stream, builds line-by-line) ──────────────────────
  SCREENS.extract = () => {
    S.render = SCREENS.extract;
    h(`<h1>${t("extract_title")}</h1><p class="help">${t("extract_help")}</p>
       <div id="stream"></div>`);
    const stream = document.getElementById("stream");
    const es = new EventSource(`/api/session/${S.sid}/events`);
    es.addEventListener("ticket", (e) => {
      const d = JSON.parse(e.data);
      const day = d.index + 1;
      const flag = d.has_flag;
      const row = document.createElement("div");
      row.className = "trow" + (flag ? " flag" : "");
      row.innerHTML = `<div class="num">${day}</div>
        <div class="lbl">${t("ticket_day")} ${day}</div>
        <div class="st">${flag ? "⚠ " + t("ticket_flag") : "✓ " + t("ticket_ok")}</div>`;
      stream.appendChild(row);
    });
    es.addEventListener("done", async () => {
      es.close();
      const { body } = await api(`/api/session/${S.sid}`);
      const anyFlag = (body.flagged || []).length > 0;
      setTimeout(() => go(anyFlag ? "review" : "receipt", { replace: true }), 500);
    });
    es.onerror = () => es.close();
  };

  // ── SCREEN: review (the trust gate) ────────────────────────────────────────
  SCREENS.review = async () => {
    S.render = SCREENS.review;
    const { body } = await api(`/api/session/${S.sid}`);
    const flags = (body.flagged || []).filter((f) => !f.confirmed);
    if (flags.length === 0) return go("receipt", { replace: true });
    const f = flags[0];
    const entry = { val: String(f.value) };
    const reads = [...new Set(f.reads.map(String))];
    const counts = {};
    f.reads.forEach((r) => (counts[r] = (counts[r] || 0) + 1));

    const draw = () => {
      h(`<h1>${t("review_title")}</h1><p class="help">${t("review_help")}</p>
        <div class="card">
          <img class="crop" src="/crop/${f.crop_image}" alt="${t("review_crop_alt")}">
          <p class="muted" style="margin:.6em 0 .2em">${t("review_reads")}</p>
          <div class="reads">${reads.map((r) =>
            `<button class="read${r === entry.val ? " sel" : ""}" data-r="${esc(r)}">${esc(r)}
               <span class="cnt">${counts[r]}×</span></button>`).join("")}</div>
          <p class="muted">${t("review_keypad_help")}</p>
          <div class="entry" id="entry">${esc(entry.val)}</div>
          <div class="keypad">
            ${[1,2,3,4,5,6,7,8,9].map((n)=>`<button class="key" data-k="${n}">${n}</button>`).join("")}
            <button class="key" data-k=".">.</button>
            <button class="key" data-k="0">0</button>
            <button class="key" data-k="del">⌫</button>
          </div>
        </div>
        <button class="btn" id="confirm">✓ ${t("review_confirm")}</button>`);
      document.querySelectorAll(".read").forEach((b) =>
        b.onclick = () => { entry.val = b.dataset.r; draw(); });
      document.querySelectorAll(".key").forEach((b) =>
        b.onclick = () => {
          const k = b.dataset.k;
          if (k === "del") entry.val = entry.val.slice(0, -1);
          else if (entry.val.length < 8) entry.val = (entry.val === "0" ? "" : entry.val) + k;
          document.getElementById("entry").textContent = entry.val || "0";
          document.querySelectorAll(".read").forEach((r) =>
            r.classList.toggle("sel", r.dataset.r === entry.val));
        });
      document.getElementById("confirm").onclick = async () => {
        await api(`/api/session/${S.sid}/confirm`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ field: f.field, value: entry.val || f.value }) });
        SCREENS.review();     // next flag, or → receipt
      };
    };
    draw();
  };

  // ── SCREEN: receipt (+ provenance + TTS) ───────────────────────────────────
  SCREENS.receipt = async () => {
    S.render = SCREENS.receipt;
    const { ok, status, body } = await api(`/api/session/${S.sid}/receipt`);
    if (!ok && status === 409) {
      h(`<div class="banner">⚠ ${t("gate_refused")}</div>
         <button class="btn" id="toreview">${t("review_title")}</button>`);
      document.getElementById("toreview").onclick = () => go("review", { replace: true });
      return;
    }
    const money = (v) => `$${v}`;
    const lines = body.lines.map((ln, i) =>
      `<button class="rline" data-i="${i}">
         <span>${esc(ln.item.replace(/_/g, " "))}</span>
         <span class="amt">${money(ln.amount)}</span></button>
       <div class="prov" id="prov${i}" hidden></div>`).join("");
    const lb = body.lower_bound
      ? `<div class="banner">${t("receipt_lower_bound")} ${body.missing_days.length ?
          t("receipt_missing") + " " + body.missing_days.join(", ") : ""}</div>` : "";
    h(`<h1>${t("receipt_title")}</h1>${lb}
      <div class="card">
        <div class="bignum owed"><span class="k">${t("receipt_owed")}</span><span class="v">${money(body.total_owed)}</span></div>
        <div class="bignum"><span class="k">${t("receipt_paid")}</span><span class="v">${money(body.amount_paid)}</span></div>
        <div class="bignum diff"><span class="k">${t("receipt_diff")}</span><span class="v">${money(body.shortfall)}</span></div>
      </div>
      <p class="help">${t("receipt_line_detail")}</p>
      <div class="card">${lines}</div>
      <div class="card">
        <div class="bignum"><span class="k">${t("receipt_claim")}</span><span class="v muted">${money(body.claim_value.liquidated_damages)}</span></div>
        <p class="muted" style="margin:0">${t("receipt_liquidated")} ${money(body.claim_value.liquidated_damages)} · ${t("receipt_interest")} ${money(body.claim_value.interest)} · <code>${esc(body.claim_value.citation)}</code></p>
      </div>
      <p class="muted">${t("receipt_residual")} <b>${esc(body.residual_error || "")}</b></p>
      <button class="btn" id="play">${t("receipt_play")}</button>
      <div class="disclaimer">${t("disclaimer")}<br><span class="crla">${t("crla")}</span></div>`);

    document.querySelectorAll(".rline").forEach((b) =>
      b.onclick = async () => {
        const i = b.dataset.i, ln = body.lines[i], el = document.getElementById("prov" + i);
        if (!el.hidden) { el.hidden = true; return; }
        const srcImgs = [];
        for (const sid of ln.sources) {
          const r = await api(`/api/session/${S.sid}/field/${encodeURIComponent(sid)}`);
          if (r.body.crop_image)
            srcImgs.push(`<div><b>${esc(sid)}</b> — ${t("receipt_reads_label")}: ${esc((r.body.reads||[]).join(", "))}
              <img src="/crop/${r.body.crop_image}" alt=""></div>`);
          else srcImgs.push(`<div><b>${esc(sid)}</b> = ${esc(r.body.value)}</div>`);
        }
        const sens = Object.entries(ln.dollar_sensitivity || {})
          .map(([k, v]) => `${esc(k)}: ${esc(v)}`).join(" · ");
        el.innerHTML = `<div><b>${t("receipt_arithmetic")}:</b> ${esc(ln.arithmetic)}</div>
          <div><b>${t("receipt_citation")}:</b> <code>${esc(ln.citation)}</code></div>
          <div><b>${t("receipt_sources")}:</b></div>${srcImgs.join("")}
          <div><b>${t("receipt_sensitivity")}:</b> ${sens}</div>`;
        el.hidden = false;
      });

    document.getElementById("play").onclick = async () => {
      const say = S.lang === "es"
        ? `Le deben ${body.total_owed} dólares. Le pagaron ${body.amount_paid}. La diferencia es ${body.shortfall} dólares por descansos no pagados. ${t("disclaimer")}`
        : `You are owed ${body.total_owed} dollars. You were paid ${body.amount_paid}. The difference is ${body.shortfall} dollars for unpaid rest breaks. ${t("disclaimer")}`;
      const r = await fetch("/api/tts", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: say, lang: S.lang }) });
      if (r.ok) { ttsAudio.src = URL.createObjectURL(await r.blob()); ttsAudio.play(); }
    };
  };

  // ── SCREEN: history ────────────────────────────────────────────────────────
  SCREENS.history = async () => {
    S.render = SCREENS.history;
    const { body } = await api("/api/history");
    const p = body.pattern;
    const banner = p.shorted > 0
      ? `<div class="banner">${t("pattern_banner").replace("{n}", p.shorted)
          .replace("{total}", p.total).replace("{amount}", p.amount)}</div>` : "";
    const list = body.receipts.length
      ? body.receipts.map((r) =>
        `<button class="rline"><span>${esc(r.period_start)} → ${esc(r.period_end)}</span>
           <span class="amt">$${esc(r.shortfall)}</span></button>`).join("")
      : `<p class="help">${t("history_empty")}</p>`;
    h(`<h1>${t("history_title")}</h1>${banner}<div class="card">${list}</div>
       <button class="btn danger" id="wipe">🗑 ${t("history_wipe")}</button>`);
    document.getElementById("wipe").onclick = async () => {
      if (!confirm(t("history_wipe_confirm"))) return;
      await api("/api/wipe", { method: "POST" });
      localStorage.removeItem("boleto_sid"); S.sid = null;
      SCREENS.history();
    };
  };

  // ── SCREEN: trust surface ──────────────────────────────────────────────────
  SCREENS.trust = async () => {
    S.render = SCREENS.trust;
    const { body } = await api("/api/trust");
    h(`<h1>${t("trust_title")}</h1>
       <div class="card"><span class="badge-air">✈ ${t("trust_airplane")}</span></div>
       <div class="card"><p>${t("trust_body")}</p>
         <p class="muted">${t("trust_bind")}</p><code>${esc(body.bind)}</code>
         <p class="muted" style="margin-top:14px">${t("trust_verify")}</p>
         <code>${esc(body.verify_cmd)}</code>
         <p class="muted" style="margin-top:14px">${esc(body.note)}</p></div>`);
  };

  // ── SCREEN: eval-results ───────────────────────────────────────────────────
  SCREENS.evals = async () => {
    S.render = SCREENS.evals;
    const { body } = await api("/api/evals");
    const pct = (x) => (x == null || isNaN(x) ? "—" : (x * 100).toFixed(1) + "%");
    const metric = (k, v) => `<div class="metric"><span>${k}</span><span class="v">${v}</span></div>`;
    // WS-C per_severity is {name: {level: {field_exact_match}}}; flatten to rows. Also accept {name:{exact}}.
    const exact = (m) => (m.field_exact_match ?? m.exact);
    const rows = [];
    for (const [name, levels] of Object.entries(body.per_severity || {})) {
      if (levels && ("field_exact_match" in levels || "exact" in levels)) rows.push([name, exact(levels)]);
      else for (const [lvl, m] of Object.entries(levels || {})) rows.push([`${name} ${lvl}`, exact(m)]);
    }
    const sev = rows.map(([k, v]) =>
      `<div class="metric"><span>${esc(k)}</span><span class="v">${pct(v)}</span></div>
       <div class="bar"><i style="width:${(v || 0) * 100}%"></i></div>`).join("");
    const cal = (body.calibration || []).map((c) => {
      const agree = c.vote_agreement != null ? pct(c.vote_agreement) + (c.n ? ` (n=${c.n})` : "") : c.agreement;
      return `<div class="metric"><span>${esc(agree)}</span><span class="v">${pct(c.empirical_accuracy)}</span></div>`;
    }).join("");
    const note = body._source === "mock" ? `<div class="banner">${t("evals_mock_note")}</div>` : "";
    h(`<h1>${t("evals_title")}</h1>${note}
      <div class="card">
        ${metric(t("evals_model"), esc(body.model))}
        ${metric(t("evals_n"), body.n)}
        ${metric(t("evals_exact"), pct(body.field_exact_match))}
        ${metric(t("evals_critical"), pct(body.conclusion_critical_rate))}
        ${metric(t("evals_escaped"), pct(body.escaped_rate))}
        ${metric(t("evals_flag"), pct(body.flag_rate))}
      </div>
      <h1 style="font-size:1.1rem">${t("evals_severity")}</h1><div class="card">${sev}</div>
      <h1 style="font-size:1.1rem">${t("evals_calibration")}</h1><div class="card">${cal}</div>`);
  };

  // ── boot ───────────────────────────────────────────────────────────────────
  (async () => {
    S.strings = await (await fetch("/static/strings.json")).json();
    applyLang();
    go("capture", { replace: true });
  })();
})();
