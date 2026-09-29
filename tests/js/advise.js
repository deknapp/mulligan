// Runs the website pick helper's advise() on a pack, for tests/test_site_pick.py.
// usage: node advise.js tools.js data.json '{"pack": [...], "picks": [...]}'
const fs = require("fs"), vm = require("vm");
const [tools, dataPath, arg] = process.argv.slice(2);
const src = fs.readFileSync(tools, "utf8").replace(/\nload\(\)\.then[\s\S]*$/, "\n");
const ctx = { console, Map, Set, Math, Object, JSON };
vm.createContext(ctx);
vm.runInContext(src + "\nthis.prepare = prepare; this.advise = advise;", ctx);
const state = ctx.prepare(JSON.parse(fs.readFileSync(dataPath, "utf8")), null);
const { pack, picks } = JSON.parse(arg);
const get = (n) => state.byName.get(n);
const { scored } = ctx.advise(state, pack.map(get), picks.map(get), false);
console.log(JSON.stringify(scored.map((s) => [s.c.n, s.score])));
