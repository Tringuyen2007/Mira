// Capability probe: GET /openapi.json (no token needed) lists which planned routes the running server has.

export async function loadCapabilities(fetchImpl) {
  let paths = {};
  try {
    const res = await fetchImpl("/openapi.json", { credentials: "omit", cache: "no-store" });
    if (res.ok) {
      const data = await res.json();
      if (data && typeof data.paths === "object" && data.paths) paths = data.paths;
    }
  } catch {
    // Any failure (network, parse, non-ok) leaves `paths` empty: has() is always false.
  }
  return { has: (template) => Object.prototype.hasOwnProperty.call(paths, template) };
}
