/**
 * Vault-compatible JSON logger.
 * Appends log entries to Vault/Logs/YYYY-MM-DD.json.
 */

import fs from "node:fs";
import path from "node:path";

/**
 * Resolve the vault root. Checks VAULT_ROOT env, then walks up from cwd.
 */
function resolveVaultRoot() {
  if (process.env.VAULT_ROOT) return process.env.VAULT_ROOT;
  let dir = process.cwd();
  while (dir !== path.dirname(dir)) {
    if (fs.existsSync(path.join(dir, "Dashboard.md"))) return dir;
    dir = path.dirname(dir);
  }
  return process.cwd();
}

const VAULT_ROOT = resolveVaultRoot();

export function logToVault(entry) {
  const today = new Date().toISOString().slice(0, 10);
  const logsDir = path.join(VAULT_ROOT, "Logs");
  if (!fs.existsSync(logsDir)) fs.mkdirSync(logsDir, { recursive: true });

  const logFile = path.join(logsDir, `${today}.json`);
  let records = [];
  if (fs.existsSync(logFile)) {
    try {
      records = JSON.parse(fs.readFileSync(logFile, "utf-8"));
    } catch {
      records = [];
    }
  }

  records.push({
    timestamp: new Date().toISOString(),
    source: "odoo-mcp-server",
    ...entry,
  });

  fs.writeFileSync(logFile, JSON.stringify(records, null, 2), "utf-8");
}
