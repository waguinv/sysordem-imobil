// PostgreSQL embarcado (sem Docker) para desenvolvimento e testes locais.
// Uso: PG_LOCAL_PASSWORD=<senha> npm start  (porta padrão 55432; dados em tools/pg-local/data)
import EmbeddedPostgres from "embedded-postgres";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const dataDir = join(here, "data");
const port = Number(process.env.PG_LOCAL_PORT ?? 55432);
const password = process.env.PG_LOCAL_PASSWORD;
if (!password) {
  console.error("Defina PG_LOCAL_PASSWORD (senha do superusuário local imobil_owner).");
  process.exit(1);
}

// UTF-8 obrigatório (no Windows o padrão do initdb seria WIN1252).
const pg = new EmbeddedPostgres({
  databaseDir: dataDir,
  user: "imobil_owner",
  password,
  port,
  persistent: true,
  initdbFlags: ["--encoding=UTF8", "--locale=C"],
});
if (!existsSync(join(dataDir, "PG_VERSION"))) await pg.initialise();
await pg.start();
console.log(`PostgreSQL local em 127.0.0.1:${port} (superusuário imobil_owner). Ctrl+C para parar.`);
const stop = async () => { await pg.stop(); process.exit(0); };
process.on("SIGINT", stop);
process.on("SIGTERM", stop);
