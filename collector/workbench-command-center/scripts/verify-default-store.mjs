import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";

// Run the actual hook with a tiny React effect harness; no server or marketplace calls.
const source = readFileSync(new URL("../src/hooks/useSearchVisibilityPlan.ts", import.meta.url), "utf8");
const pending = [];
const slots = [];
let cursor = 0;
let effects = [];
const react = {
  useState(initial) {
    const index = cursor++;
    slots[index] ??= { value: initial };
    return [slots[index].value, (value) => { slots[index].value = value; }];
  },
  useRef(initial) {
    const index = cursor++;
    slots[index] ??= { current: initial };
    return slots[index];
  },
  useEffect(callback, dependencies) {
    const index = cursor++;
    const previous = slots[index];
    if (!previous || dependencies.some((value, i) => value !== previous.dependencies[i])) {
      effects.push(() => {
        previous?.cleanup?.();
        slots[index] = { dependencies, cleanup: callback() };
      });
    }
  },
};
const exported = {};
vm.runInNewContext(ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText, {
  exports: exported,
  require(name) {
    if (name === "react") return react;
    if (name.includes("commandCenterConfig")) return { commandCenterConfig: {} };
    return {
      loadSearchVisibilityPlan(store) {
        return new Promise((resolve) => pending.push({ store, resolve }));
      },
      getErrorMessage: String,
    };
  },
  window: { setInterval() {}, clearInterval() {}, addEventListener() {}, removeEventListener() {} },
  document: { addEventListener() {}, removeEventListener() {} },
});
function render(store) {
  cursor = 0;
  effects = [];
  const hook = exported.useSearchVisibilityPlan(store);
  effects.forEach((effect) => effect());
  return hook;
}
render("store-a");
assert.equal(pending[0]?.store, "store-a", "Default store must load its own cached catalog");
render("store-b");
assert.equal(pending[1]?.store, "store-b", "Changing stores must reload the catalog");
pending[1].resolve({ shop_id: "store-b", actions: [{ product_id: "B" }] });
await new Promise(setImmediate);
pending[0].resolve({ shop_id: "store-a", actions: [{ product_id: "A" }] });
await new Promise(setImmediate);
assert.equal(render("store-b").plan.shop_id, "store-b", "Late old-store response must not replace the selected store");
console.log("PASS: default store, store change, and stale initial response");
