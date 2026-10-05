import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const frontend = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const root = resolve(frontend, "..");
const temporary = mkdtempSync(join(tmpdir(), "finance-openapi-check-"));
const spec = join(temporary, "openapi.json");
const types = join(temporary, "schema.d.ts");

try {
  execFileSync(
    join(root, ".venv/bin/python"),
    ["scripts/export_openapi.py", spec],
    {
      cwd: root,
      env: { ...process.env, PYTHONPATH: "src" },
      stdio: "inherit",
    },
  );
  execFileSync(
    join(frontend, "node_modules/.bin/openapi-typescript"),
    [spec, "-o", types],
    {
      cwd: frontend,
      stdio: "inherit",
    },
  );
  execFileSync(
    join(frontend, "node_modules/.bin/prettier"),
    ["--write", spec, types],
    {
      cwd: frontend,
      stdio: "ignore",
    },
  );
  const comparisons = [
    [join(frontend, "openapi.json"), spec],
    [join(frontend, "src/api/schema.d.ts"), types],
  ];
  for (const [committed, generated] of comparisons) {
    if (!readFileSync(committed).equals(readFileSync(generated))) {
      throw new Error(`Generated API artifact is stale: ${committed}`);
    }
  }
  process.stdout.write("OpenAPI and generated TypeScript types are in sync.\n");
} finally {
  rmSync(temporary, { recursive: true, force: true });
}
