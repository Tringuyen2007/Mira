// Tiny store: shallow-merge set(), synchronous notify.
export function createStore(initial) {
  let state = initial;
  const subs = new Set();
  return {
    get() { return state; },
    set(patch) {
      state = { ...state, ...(typeof patch === "function" ? patch(state) : patch) };
      for (const fn of subs) fn(state);
    },
    subscribe(fn) {
      subs.add(fn);
      return () => subs.delete(fn);
    },
  };
}
