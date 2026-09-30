// Runs the website pick helper's advise() on a pack, for tests/test_site_pick.py.
// usage: node advise.js tools.js data.json experts.json|- '{"pack": [...], "picks": [...]}'
const fs = require("fs"), vm = require("vm");
const [tools, dataPath, expertsPath, arg] = process.argv.slice(2);
const src = fs.readFileSync(tools, "utf8").replace(/\nload\(\)\.then[\s\S]*$/, "\n");
const ctx = { console, Map, Set, Math, Object, JSON };
vm.createContext(ctx);
vm.runInContext(src + "\nthis.prepare = prepare; this.advise = advise; this.addExperts = addExperts; this.expertState = expertState;", ctx);
const state = ctx.prepare(JSON.parse(fs.readFileSync(dataPath, "utf8")), null);
const experts = expertsPath === "-" ? null : JSON.parse(fs.readFileSync(expertsPath, "utf8"));
ctx.addExperts(state, experts);
const { pack, picks } = JSON.parse(arg);
// With expert grades, rank as the website's pick helper does: experts only.
const used = experts ? ctx.expertState(state) : state;
const get = (n) => used.byName.get(n);
const { scored } = ctx.advise(used, pack.map(get), picks.map(get), false);
console.log(JSON.stringify(scored.map((s) => [s.c.n, s.score])));
