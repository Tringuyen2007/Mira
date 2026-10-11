// Test data loader. Samples another branch regenerates (wire-tri rewrites rest_get_session.json with a finished
// 4-rep session) are pinned as copies under ./fixtures/, so the dashboard's assertions don't change when the
// server samples do. Everything else is read straight from server/samples/.
import { readFileSync } from "node:fs";

export const PINNED = new Set(["rest_get_session.json", "rest_get_patient.json"]);

export function sample(name) {
  const rel = PINNED.has(name) ? `./fixtures/${name}` : `../../../server/samples/${name}`;
  return JSON.parse(readFileSync(new URL(rel, import.meta.url), "utf8"));
}
