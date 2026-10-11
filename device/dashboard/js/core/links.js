import { parseRoute } from "./router.js";

// Strip a leading "/dashboard" (exact), then require a hash route that actually resolves.
export function safeInternalLink(link) {
  if (typeof link !== "string" || link === "") return null;
  let hash = link;
  if (hash.startsWith("/dashboard#")) hash = hash.slice("/dashboard".length);
  if (!hash.startsWith("#/")) return null;
  if (parseRoute(hash).name === "notfound") return null;
  return hash;
}
