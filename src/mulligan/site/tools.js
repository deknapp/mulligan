// Format tools: card ratings, pick helper, color pairs.
// Everything comes from data/<set>.json (the latest simulated draft) and, once a
// set is out, data/<set>-17lands.json (real players' results, fetched daily).
// Where 17Lands has a card's win rate from enough games, it replaces the
// simulator's: real data beats simulated data as soon as it exists.
"use strict";

const REAL_MIN_GAMES = 500;   // trust a 17Lands card win rate from this many games
const COLORS = { W: "White", U: "Blue", B: "Black", R: "Red", G: "Green" };
const PAIR_NAMES = { WU: "Azorius", UB: "Dimir", BR: "Rakdos", RG: "Gruul", WG: "Selesnya",
  WB: "Orzhov", UR: "Izzet", BG: "Golgari", WR: "Boros", UG: "Simic" };
const RARITY = { c: "Common", u: "Uncommon", r: "Rare", m: "Mythic" };
const GRADES = [[2, "A+"], [1.5, "A"], [1, "A-"], [0.6, "B+"], [0.25, "B"], [-0.1, "B-"],
  [-0.45, "C+"], [-0.8, "C"], [-1.2, "C-"], [-1.6, "D"], [-Infinity, "F"]];

const $ = (sel, root = document) => root.querySelector(sel);
const el = (tag, attrs = {}, ...kids) => {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const kid of kids.flat()) if (kid != null) node.append(kid);
  return node;
};
const pct = (x, digits = 1) => (100 * x).toFixed(digits) + "%";
const pts = (x) => (x >= 0 ? "+" : "−") + Math.abs(100 * x).toFixed(1);

function wilson(w, n, z = 1.96) {
  if (!n) return [0, 1];
  const p = w / n, d = 1 + z * z / n;
  const mid = (p + z * z / (2 * n)) / d;
  const half = z * Math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d;
  return [mid - half, mid + half];
}

async function load() {
  const set = document.body.dataset.set;
  const sim = await (await fetch(`../data/${set}.json`)).json();
  let real = null;
  try {
    const r = await fetch(`../data/${set}-17lands.json`);
    if (r.ok) real = await r.json();
  } catch (_) { /* before release there is no file */ }
  return prepare(sim, real);
}

// Each card gets one working estimate: the simulator's win rate shrunk toward
// the set average (a card seen in few games can't look like a bomb by luck),
// replaced by 17Lands' when real data has enough games.
function prepare(sim, real) {
  const byName = new Map();
  const rated = [];
  for (const c of sim.cards) {
    c.sim = c.g ? c.w / c.g : null;
    c.est = c.g ? (c.w + sim.prior * sim.mean) / (c.g + sim.prior) : null;
    c.ci = c.g ? wilson(c.w, c.g) : null;
    const r = real && real.cards[c.n];
    c.real = r && r.gih != null && r.gih_n >= REAL_MIN_GAMES ? r : null;
    if (r) c.realAta = r.ata;
    c.src = "sim";
    byName.set(c.n, c);
  }
  // Real win rates run higher than simulated ones (17Lands users are better
  // than average), so real numbers are put on the simulator's scale before
  // the two are mixed: shift by the difference in averages over shared cards.
  let shift = 0;
  const both = sim.cards.filter((c) => c.real && c.g);
  if (both.length >= 20) {
    shift = both.reduce((s, c) => s + c.real.gih - c.est, 0) / both.length;
    for (const c of both) { c.est = c.real.gih - shift; c.src = "17lands"; }
    for (const c of sim.cards) if (c.real && !c.g) { c.est = c.real.gih - shift; c.src = "17lands"; }
  }
  for (const c of sim.cards) if (c.est != null && !c.un && !/Land/.test(c.t)) rated.push(c.est);
  const mean = rated.reduce((a, b) => a + b, 0) / rated.length;
  const sd = Math.sqrt(rated.reduce((a, b) => a + (b - mean) ** 2, 0) / rated.length);
  for (const c of sim.cards) {
    if (c.est == null) { c.grade = null; continue; }
    const z = (c.est - mean) / sd;
    c.z = z;
    c.grade = GRADES.find(([cut]) => z >= cut)[1];
  }
  return { sim, real, byName, mean, sd, shift, live: both.length >= 20 };
}

// ---- small pieces -------------------------------------------------------

function pips(colors) {
  const span = el("span", { class: "pips", "aria-label": colors ? colors.split("").map((c) => COLORS[c]).join("-") : "Colorless" });
  for (const c of colors || "C") span.append(el("i", { class: `pip pip-${c.toLowerCase()}` }));
  return span;
}

function cost(text) {
  return el("span", { class: "cost" }, text.replace(/[{}]/g, (m) => (m === "{" ? "" : " ")).trim());
}

function gradeBadge(c) {
  if (!c.grade) return el("span", { class: "grade none", title: "Not simulated" }, "–");
  const thin = c.src === "sim" && c.g < 300;
  return el("span", { class: `grade g-${c.grade[0].toLowerCase()}${thin ? " thin" : ""}`,
    title: thin ? `Only ${c.g} games: treat as a guess` : c.src === "17lands" ? "From 17Lands data" : "From simulated games" }, c.grade);
}

// A win-rate interval as a thin bar on a fixed 35–70% scale.
function intervalBar(lo, hi, mid, avg) {
  const x = (v) => Math.max(0, Math.min(100, ((v - 0.35) / 0.35) * 100));
  return el("span", { class: "ibar", "aria-hidden": "true" },
    el("span", { class: "ibar-avg", style: `left:${x(avg)}%` }),
    el("span", { class: "ibar-range", style: `left:${x(lo)}%;width:${Math.max(1, x(hi) - x(lo))}%` }),
    el("span", { class: "ibar-mid", style: `left:${x(mid)}%` }));
}

function tooltip() {
  let tip = $("#tip");
  if (!tip) { tip = el("div", { id: "tip", role: "tooltip" }); document.body.append(tip); }
  return tip;
}

function cardImage(state, c) {
  return `https://api.scryfall.com/cards/${state.sim.set}/${encodeURIComponent(c.cn)}?format=image&version=normal`;
}

// Hovering a card name shows its image and rules text.
function hoverCard(node, state, c) {
  node.classList.add("cardname");
  const show = (ev) => {
    const tip = tooltip();
    tip.replaceChildren(el("img", { src: cardImage(state, c), alt: "", width: "220", loading: "lazy" }),
      el("div", { class: "oracle" }, c.o));
    tip.style.display = "block";
    const r = node.getBoundingClientRect();
    const left = Math.min(window.innerWidth - 250, r.left);
    tip.style.left = `${Math.max(8, left) + window.scrollX}px`;
    tip.style.top = `${r.bottom + window.scrollY + 6}px`;
    if (ev.type === "focus") tip.dataset.focus = "1";
  };
  const hide = () => { tooltip().style.display = "none"; };
  node.addEventListener("mouseenter", show);
  node.addEventListener("mouseleave", hide);
  node.tabIndex = 0;
  node.addEventListener("focus", show);
  node.addEventListener("blur", hide);
  return node;
}

function freshness(state) {
  const s = state.sim;
  const bits = [`Simulated: ${s.decks.toLocaleString()} bot-drafted decks, ${s.games.toLocaleString()} games (run of ${s.run}; ${s.spoiler}).`];
  if (state.real) {
    const n = Object.values(state.real.cards).filter((r) => r.gih != null && r.gih_n >= REAL_MIN_GAMES).length;
    bits.push(n ? `17Lands: ${n} cards with real win rates (updated ${state.real.fetched.slice(0, 10)}); those replace the simulated numbers.`
      : `17Lands: no real data yet (it starts ${state.real.start || "after release"}); every number here is simulated.`);
  } else bits.push("17Lands: no real data yet; every number here is simulated.");
  return el("p", { class: "fresh" }, bits.join(" "));
}

// ---- card ratings -------------------------------------------------------

function cardsTool(state) {
  const root = $("#tool");
  const f = { q: "", colors: new Set(), rarity: "", pair: "", sort: "grade", sim: true };
  const colorBtns = el("div", { class: "chips", role: "group", "aria-label": "Colors" });
  for (const c of "WUBRGM") {
    const b = el("button", { type: "button", class: "chip", "aria-pressed": "false",
      onclick: () => { f.colors.has(c) ? f.colors.delete(c) : f.colors.add(c); b.setAttribute("aria-pressed", f.colors.has(c)); draw(); } },
      c === "M" ? "Multi" : COLORS[c]);
    colorBtns.append(b);
  }
  const search = el("input", { type: "search", placeholder: "Search cards or rules text", "aria-label": "Search",
    oninput: (e) => { f.q = e.target.value.toLowerCase(); draw(); } });
  const rarity = el("select", { "aria-label": "Rarity", onchange: (e) => { f.rarity = e.target.value; draw(); } },
    el("option", { value: "" }, "All rarities"), ...Object.entries(RARITY).map(([k, v]) => el("option", { value: k }, v)));
  const pair = el("select", { "aria-label": "Color pair", onchange: (e) => { f.pair = e.target.value; draw(); } },
    el("option", { value: "" }, "In any deck"),
    ...Object.keys(PAIR_NAMES).map((p) => el("option", { value: p }, `In ${PAIR_NAMES[p]} (${p}) decks`)));
  const sort = el("select", { "aria-label": "Sort", onchange: (e) => { f.sort = e.target.value; draw(); } },
    el("option", { value: "grade" }, "Best first"), el("option", { value: "value" }, "Beats its pick most"),
    el("option", { value: "ata" }, "Picked earliest"), el("option", { value: "name" }, "Name"));
  const count = el("span", { class: "count" });
  const table = el("table", { class: "cards" });
  root.append(freshness(state), el("div", { class: "filters" }, search, colorBtns, rarity, pair, sort, count), el("div", { class: "table scroll" }, table),
    el("p", { class: "caption" }, "Grade: the card's win rate when drawn against every other card in the set (A+ is about the top 3%). Bar: 95% interval, dashed line = set average. Faded grades come from fewer than 300 games. † = simplified in the simulator; hover the name for what's left out. Simplifications only make a card weaker."));

  // Beats-its-pick: win rate above what its average pick predicts (a line fit).
  const fit = (() => {
    const xs = state.sim.cards.filter((c) => c.g >= 300 && c.ata);
    const mx = xs.reduce((s, c) => s + c.ata, 0) / xs.length, my = xs.reduce((s, c) => s + c.est, 0) / xs.length;
    const slope = xs.reduce((s, c) => s + (c.ata - mx) * (c.est - my), 0) / xs.reduce((s, c) => s + (c.ata - mx) ** 2, 0);
    return (c) => (c.est == null || !c.ata ? -1 : c.est - (my + slope * (c.ata - mx)));
  })();

  function row(c) {
    let w = c.w, g = c.g;
    if (f.pair) [w, g] = (state.sim.pc[c.n] || {})[f.pair] || [0, 0];
    const rate = g ? w / g : null;
    const [lo, hi] = g ? wilson(w, g) : [0, 0];
    const name = hoverCard(el("span", {}, c.n), state, c);
    const note = c.ap && c.ap.length ? el("span", { class: "dag", title: c.ap.join("; ") }, " †") : null;
    return el("tr", {},
      el("td", {}, gradeBadge(c)),
      el("td", { class: "namecell" }, pips(c.c), " ", name, note),
      el("td", { class: "muted" }, cost(c.cost)),
      el("td", { class: "muted" }, c.r.toUpperCase()),
      el("td", { class: "num" }, rate == null ? (c.un ? el("span", { class: "muted", title: c.un }, "not simulated") : "–") : pct(rate)),
      el("td", { class: "barcell" }, rate == null ? "" : intervalBar(lo, hi, rate, state.sim.mean)),
      el("td", { class: "num" }, g ? g.toLocaleString() : ""),
      el("td", { class: "num" }, c.ata ? c.ata.toFixed(1) : ""),
      state.live ? el("td", { class: "num" }, c.real ? pct(c.real.gih) : "") : null);
  }

  function draw() {
    let cards = state.sim.cards.filter((c) => !/Land/.test(c.t) || c.c);
    if (f.q) cards = cards.filter((c) => c.n.toLowerCase().includes(f.q) || c.o.toLowerCase().includes(f.q));
    if (f.colors.size) cards = cards.filter((c) => [...f.colors].some((k) => (k === "M" ? c.c.length > 1 : c.c.includes(k))));
    if (f.rarity) cards = cards.filter((c) => c.r === f.rarity);
    if (f.pair) cards = cards.filter((c) => (state.sim.pc[c.n] || {})[f.pair]);
    const pairRate = (c) => { const r = (state.sim.pc[c.n] || {})[f.pair]; return r ? (r[0] + 100 * state.sim.mean) / (r[1] + 100) : -1; };
    const key = { grade: (c) => -(f.pair ? pairRate(c) : c.est ?? -1), value: (c) => -fit(c),
      ata: (c) => c.ata || 99, name: (c) => c.n }[f.sort];
    cards.sort((a, b) => (key(a) < key(b) ? -1 : key(a) > key(b) ? 1 : 0));
    const head = el("tr", {}, el("th", {}, "Grade"), el("th", {}, "Card"), el("th", {}, "Cost"), el("th", {}, "Rar."),
      el("th", { class: "num" }, f.pair ? `Win rate in ${f.pair}` : "Win rate when drawn"), el("th", {}, ""),
      el("th", { class: "num" }, "Games"), el("th", { class: "num" }, "Avg pick"),
      state.live ? el("th", { class: "num" }, "17Lands") : null);
    table.replaceChildren(el("thead", {}, head), el("tbody", {}, cards.map(row)));
    count.textContent = `${cards.length} cards`;
  }
  draw();
}

// ---- pick helper --------------------------------------------------------

function nameInput(state, placeholder, onadd) {
  const listId = "names-" + Math.random().toString(36).slice(2);
  const list = el("datalist", { id: listId }, state.sim.cards.map((c) => el("option", { value: c.n })));
  const input = el("input", { type: "text", list: listId, placeholder, "aria-label": placeholder, autocomplete: "off" });
  const add = () => {
    // Several names can be pasted at once, one per line or comma-separated.
    const names = input.value.split(/\n|,(?![^(]*\))/).map((s) => s.trim()).filter(Boolean);
    const found = names.map((n) => state.sim.cards.find((c) => c.n.toLowerCase() === n.toLowerCase()));
    found.filter(Boolean).forEach((c) => onadd(c));
    input.value = found.every(Boolean) ? "" : names.filter((_, i) => !found[i]).join(", ");
  };
  input.addEventListener("change", add);
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); add(); } });
  return el("span", { class: "nameinput" }, input, list);
}

// Your lane: the two colors your picks so far point at, weighted by quality.
function lane(state, picks) {
  const weight = { W: 0, U: 0, B: 0, R: 0, G: 0 };
  for (const c of picks) {
    const q = Math.max(0.2, 1 + (c.z ?? 0));   // good cards pull harder
    for (const k of c.c) weight[k] += q / c.c.length;
  }
  const order = Object.keys(weight).sort((a, b) => weight[b] - weight[a]);
  return { weight, top: order.slice(0, 2).filter((k) => weight[k] > 0) };
}

function pickTool(state) {
  const root = $("#tool");
  const pack = [], picks = [];
  const out = el("div", { class: "pickout" });
  const packList = el("div", { class: "tags" }), pickList = el("div", { class: "tags" });
  const tags = (arr, node) => node.replaceChildren(...arr.map((c, i) =>
    el("span", { class: "tag" }, pips(c.c), " ", c.n, el("button", { type: "button", "aria-label": `Remove ${c.n}`, onclick: () => { arr.splice(i, 1); draw(); } }, "×"))));
  root.append(freshness(state),
    el("div", { class: "pickcols" },
      el("section", {}, el("h2", {}, "The pack"), el("p", { class: "muted" }, "Add the cards you're choosing between."),
        nameInput(state, "Card in the pack", (c) => { pack.push(c); draw(); }), packList),
      el("section", {}, el("h2", {}, "Your picks so far"), el("p", { class: "muted" }, "Optional. Leave empty for pack 1, pick 1."),
        nameInput(state, "Card you've taken", (c) => { picks.push(c); draw(); }), pickList,
        el("button", { type: "button", class: "linkish", onclick: () => { picks.splice(0); pack.splice(0); draw(); } }, "Start over"))),
    out);

  // The pack and picks live in the URL, so a pick can be bookmarked or shared.
  const params = new URLSearchParams(location.hash.slice(1));
  for (const [key, arr] of [["pack", pack], ["picks", picks]]) {
    for (const n of (params.get(key) || "").split("|")) if (state.byName.has(n)) arr.push(state.byName.get(n));
  }

  function draw() {
    tags(pack, packList); tags(picks, pickList);
    const hash = new URLSearchParams();
    if (pack.length) hash.set("pack", pack.map((c) => c.n).join("|"));
    if (picks.length) hash.set("picks", picks.map((c) => c.n).join("|"));
    history.replaceState(null, "", hash.toString() ? "#" + hash : location.pathname);
    if (!pack.length) { out.replaceChildren(el("p", { class: "muted" }, "Add at least two cards from the pack.")); return; }
    const { top, weight } = lane(state, picks);
    // How much color matters grows over the draft: nothing at pick 1, most
    // by the middle of pack two. This mirrors how the simulator's bots draft.
    const commit = Math.min(1, picks.length / 14);
    const scored = pack.map((c) => {
      const base = c.est == null ? null : c.est - state.mean;
      const off = top.length === 2 ? c.c.split("").filter((k) => !top.includes(k)).length : 0;
      const inLane = c.c && top.length && c.c.split("").every((k) => top.includes(k));
      const penalty = off * commit * 0.035;
      const why = [];
      if (base == null) why.push(c.un ? `Not simulated (${c.un}).` : "No data.");
      else why.push(`${c.src === "17lands" ? "Real" : "Simulated"} win rate when drawn ${pts(base)} pts vs. the average card${c.src === "sim" && c.g < 300 ? ` (only ${c.g} games)` : ""}.`);
      if (off && commit > 0) why.push(`${off === 1 ? "One color" : "Both colors"} outside your ${top.join("")} lane: −${(penalty * 100).toFixed(1)} pts this far into the draft.`);
      else if (inLane && picks.length) why.push(`Fits your ${top.join("")} picks.`);
      else if (!c.c) why.push("Colorless: fits any deck.");
      const best = Object.entries(state.sim.pc[c.n] || {}).filter(([, r]) => r[1] >= 150)
        .map(([p, r]) => [p, (r[0] + 100 * state.sim.mean) / (r[1] + 100)]).sort((a, b) => b[1] - a[1])[0];
      if (best && c.c.length === 1) why.push(`Best in ${PAIR_NAMES[best[0]]} (${best[0]}).`);
      if (c.rm) why.push("Removal.");
      return { c, score: base == null ? -1 : base - penalty, why };
    }).sort((a, b) => b.score - a.score);
    const laneText = picks.length ? (top.length ? `Your picks lean ${top.map((k) => COLORS[k]).join("-")} (${Object.entries(weight).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]).map(([k, v]) => `${k} ${v.toFixed(1)}`).join(", ")}).` : "Your picks are colorless so far.") : "Pack 1, pick 1: color doesn't matter yet, take the best card.";
    out.replaceChildren(el("p", { class: "lane" }, laneText),
      el("ol", { class: "ranked" }, scored.map((s, i) => el("li", { class: i === 0 ? "top" : "" },
        el("div", { class: "rhead" }, gradeBadge(s.c), " ", pips(s.c.c), " ", hoverCard(el("strong", {}, s.c.n), state, s.c),
          el("span", { class: "score" }, s.score === -1 ? "" : `${pts(s.score)} pts`)),
        el("div", { class: "why" }, s.why.join(" "))))),
      el("p", { class: "caption" }, "Score = how much more often you win when this card is drawn than when an average card is, minus a penalty for colors you aren't in, which grows through the draft. It doesn't know about your curve or synergies: if two picks are within a point or two, take the one your deck needs."));
  }
  draw();
}

// ---- color pairs --------------------------------------------------------

function pairsTool(state) {
  const root = $("#tool");
  const s = state.sim;
  const rows = Object.entries(s.pairs).map(([p, r]) => ({ p, ...r, rate: r.w / r.g, ci: wilson(r.w, r.g),
    real: state.real && state.real.pairs[p] ? state.real.pairs[p][0] / state.real.pairs[p][1] : null }))
    .sort((a, b) => b.rate - a.rate);
  const detail = el("div", { class: "pairdetail" });
  const list = el("table", { class: "pairs" },
    el("thead", {}, el("tr", {}, el("th", {}, "Pair"), el("th", { class: "num" }, "Win rate"), el("th", {}, ""),
      el("th", { class: "num" }, "Share of decks"), state.real && Object.keys(state.real.pairs).length ? el("th", { class: "num" }, "17Lands") : null)),
    el("tbody", {}, rows.map((r) => {
      const tr = el("tr", { class: "clickable", tabindex: "0", onclick: () => show(r), onkeydown: (e) => { if (e.key === "Enter") show(r); } },
        el("td", {}, pips(r.p), " ", `${PAIR_NAMES[r.p]} `, el("span", { class: "muted" }, r.p)),
        el("td", { class: "num" }, pct(r.rate)),
        el("td", { class: "barcell" }, intervalBar(r.ci[0], r.ci[1], r.rate, 0.5)),
        el("td", { class: "num" }, pct(r.share, 0)),
        state.real && Object.keys(state.real.pairs).length ? el("td", { class: "num" }, r.real == null ? "" : pct(r.real)) : null);
      return tr;
    })));
  root.append(freshness(state), el("div", { class: "table" }, list),
    el("p", { class: "caption" }, "Win rate of each pair's decks against the whole simulated field, with 95% intervals; dashed line = 50%. Share = how many bot-drafted decks ended up in the pair. Click a pair for its best cards and a sample deck."),
    detail);

  function show(r, scroll = true) {
    const prof = r.profile;
    const inPair = s.cards.filter((c) => (s.pc[c.n] || {})[r.p] && c.c && c.c.split("").every((k) => r.p.includes(k)))
      .map((c) => { const [w, g] = s.pc[c.n][r.p]; return { c, rate: (w + 100 * s.mean) / (g + 100), g }; })
      .sort((a, b) => b.rate - a.rate);
    const cardList = (items) => el("ol", { class: "cardlist" }, items.map((x) =>
      el("li", {}, pips(x.c.c), " ", hoverCard(el("span", {}, x.c.n), state, x.c), el("span", { class: "muted" }, ` ${x.c.r.toUpperCase()} · ${pct(x.rate)} in ${r.p} (${x.g} games)`))));
    const commons = inPair.filter((x) => x.c.r === "c").slice(0, 6);
    const top = inPair.filter((x) => x.c.r !== "c").slice(0, 6);
    const sample = r.sample.cards.filter((n) => !/^(Plains|Island|Swamp|Mountain|Forest)$/.test(n));
    const counts = {};
    for (const n of sample) counts[n] = (counts[n] || 0) + 1;
    const lands = r.sample.cards.length - sample.length;
    detail.replaceChildren(el("h2", { id: "detail" }, pips(r.p), ` ${PAIR_NAMES[r.p]} (${r.p})`),
      el("p", {}, `${pct(r.rate)} against the field over ${r.g.toLocaleString()} games; ${r.decks} of ${s.decks} bot decks. The average deck ran ${prof.creatures.toFixed(1)} creatures (${prof.twos.toFixed(1)} of them two-drops or cheaper), ${prof.removal.toFixed(1)} removal spells and an average mana value of ${prof.mv.toFixed(2)}.`),
      el("div", { class: "pickcols" },
        el("section", {}, el("h3", {}, "Best commons in the pair"), cardList(commons)),
        el("section", {}, el("h3", {}, "Best uncommons and rares"), cardList(top))),
      el("h3", {}, `Sample deck${r.sample.record ? ` (won ${pct(r.sample.record[0] / r.sample.record[1], 0)} of ${r.sample.record[1]} games)` : ""}`),
      el("ul", { class: "decklist" }, Object.entries(counts).map(([n, k]) => {
        const c = state.byName.get(n);
        return el("li", {}, `${k} `, c ? hoverCard(el("span", {}, n), state, c) : n);
      }), el("li", { class: "muted" }, `${lands} basic lands`)),
      el("p", { class: "caption" }, "Card win rates here are only from games where the card was in a deck of this pair, shrunk toward the set average so small samples don't top the list."));
    if (scroll) detail.scrollIntoView({ behavior: "smooth", block: "start" });
  }
  show(rows[0], false);
}

load().then((state) => {
  const tool = document.body.dataset.tool;
  ({ cards: cardsTool, pick: pickTool, pairs: pairsTool })[tool](state);
}).catch((err) => {
  $("#tool").append(el("p", {}, `Couldn't load the data: ${err}`));
});
