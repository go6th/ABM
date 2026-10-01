# 03 Environment, commands and their limits

## This machine (as found on 1 October 2026)

| Fact | Consequence | Workaround |
| --- | --- | --- |
| The user is not in the `docker` group, and the Docker Compose plugin is not installed | `make up`, `make temporal-up`, image builds cannot run here | Say so; never claim they ran. The user can run `sudo apt-get install docker-compose-plugin && sudo usermod -aG docker $USER` |
| No system Postgres | Database tests skip without help | Embedded Postgres 16 binaries (below) via `PG_BIN` |
| Local venv is Python 3.14; CI and the worker image use 3.12 | `uuid.uuid7` exists locally only; 3.12 gets uuid4 | Write 3.12-compatible code; never rely on id order |
| `terraform` not installed | `make tf-validate` cannot run | Download a pinned binary to the scratchpad and pass `TERRAFORM=...` |
| Official `minio/minio` images are no longer publicly pullable | Compose uses `pgsty/minio:RELEASE.2026-08-04T00-00-00Z` (community build, same server, ships `curl` and `mc`) | Check tags with the registry API before pinning any image |
| Network access works | Temporal's time-skipping test server downloads on first use | CI downloads it too |

### Postgres 16 without docker

```bash
S=<scratch dir>
curl -sSfL -o $S/pg.jar https://repo1.maven.org/maven2/io/zonky/test/postgres/embedded-postgres-binaries-linux-amd64/16.4.0/embedded-postgres-binaries-linux-amd64-16.4.0.jar
python -c "import zipfile,tarfile,io,lzma; z=zipfile.ZipFile('$S/pg.jar'); n=[x for x in z.namelist() if x.endswith('.txz')][0]; tarfile.open(fileobj=io.BytesIO(lzma.decompress(z.read(n)))).extractall('$S/pg16', filter='fully_trusted')"
chmod +x $S/pg16/bin/*
export PG_BIN=$S/pg16/bin      # tests/app/db start a throwaway cluster from it
```
These binaries have no `pg_isready` and no pgvector. The compose service uses the full image.

## Commands

| Need | Command | Notes |
| --- | --- | --- |
| Every gate, honestly reported | `bash .claude/skills/account-intelligence/scripts/gates.sh` | PASS / FAIL / SKIP per gate; set `PG_BIN` and `TERRAFORM` to run more |
| Lint, format, types, offline tests | `make app-check` | What CI runs first |
| Database tests | `PG_BIN=... .venv/bin/pytest tests/app/db -q` | or `TEST_DATABASE_URL=<admin url>`; a fresh database per test |
| Migrate a database | `DATABASE_URL=... .venv/bin/alembic -c app/db/alembic.ini upgrade head` | `make migrate` also runs the legacy chain first |
| New migration | `make migration m="..." rev=000N MIGRATE_DATABASE_URL=...` | Then edit by hand (`02-data-layer-playbook.md`) |
| Tenancy report | `DATABASE_URL=... python .claude/skills/account-intelligence/scripts/check_tenancy.py [--strict]` | Decision R-01 |
| Skill and config alignment | `python .claude/skills/account-intelligence/scripts/check_alignment.py` | Errors fail; notes are for a person |
| Platform skill self-test | `bash .claude/skills/account-intelligence-platform/scripts/selftest.sh` | |
| Client-output leak scan | `python .claude/skills/account-intelligence-platform/scripts/scan_client_output.py <files>` | Run on every export once exports exist |
| Legacy suite | `.venv/bin/pytest tests/unit -q` | Must stay green |
| Offline benchmarks | `make bench-model-split bench-people` | Output is NOT EVIDENCE |

## Shell gotchas met in this project

- `pkill -f <pattern>` and `pgrep -f` match the shell running them when the pattern is in the command line: track the server PID (`$!`) instead.
- Database fixtures and scripts: give each throwaway cluster its own socket directory (`-k`) and a free port.
- `git status` may show files the person added (for example a README rewrite or a skill folder); never commit or delete them without saying so.
