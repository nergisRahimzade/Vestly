// Start the Vestly backend for the browser tests: a fresh database file, the company world, the scripted model,
// and no exchange-rate job (tests never call the rate provider).
import { spawn } from "node:child_process";
import { mkdirSync, rmSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const backend = resolve(here, "..", "..", "backend");
const dbDir = join(here, ".db");
const db = join(dbDir, "vestly-e2e.db");
mkdirSync(dbDir, { recursive: true });
for (const suffix of ["", "-wal", "-shm", "-journal"]) rmSync(db + suffix, { force: true });

const python = process.platform === "win32" ? join(backend, ".venv", "Scripts", "python.exe") : join(backend, ".venv", "bin", "python");
const port = process.env.VESTLY_E2E_API_PORT ?? "8765";
const child = spawn(python, ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", port], {
  cwd: backend,
  stdio: "inherit",
  env: {
    ...process.env,
    DATABASE_URL: `sqlite+pysqlite:///${db.split("\\").join("/")}`,
    VESTLY_WORLD: "company",
    VESTLY_MODEL_MODE: "scripted",
    OPENEXCHANGERATES_APP_ID: "",
    OPENEXCHANGERATES_API_KEY: "",
    OXR_APP_ID: "",
  },
});
const stop = () => child.kill();
process.on("SIGINT", stop);
process.on("SIGTERM", stop);
process.on("exit", stop);
child.on("exit", (code) => process.exit(code ?? 0));
