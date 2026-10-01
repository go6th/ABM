# Data layer conventions

Every table in data layer v1 follows these rules. Code: `app/db/base.py` and `app/db/types.py`. Checked by `convention_violations()` in `tests/app/db/test_conventions.py::test_every_real_table_meets_conventions`, which fails as soon as a model breaks a rule.

## Adding a table

1. Subclass `app.db.base.Model` (it brings `id`, `created_at` and `updated_at`) and set `__table_args__ = {"schema": SCHEMA}` or `INTERNAL_SCHEMA`.
2. Import the module in `app/db/models/__init__.py`.
3. Run `make migration m="add <table>"`, read the generated file, and adjust it.
4. Run the tests. The drift test fails if the migration and the model disagree.

## Schemas

| Schema | Holds | Who may read it |
|---|---|---|
| `intel` | Rows that deliverables are built from | The application, exports, later the client app |
| `intel_internal` | Raw object records and run spend | Operators and the pipeline only |

A table in any other schema is a convention violation. The legacy tables in `public` are outside this layer.

## Primary keys

- One column, named `id`, type `uuid`.
- Generated in Python by `new_id()`: **uuid7** (time ordered) when the runtime provides `uuid.uuid7` (Python 3.14 and later), otherwise **uuid4**.
- Server default `gen_random_uuid()` (a v4 UUID), so a raw SQL insert that omits `id` still gets one.

**Consequence: do not rely on ids being time ordered.** CI and the worker image run Python 3.12, where `new_id()` returns uuid4, and raw SQL inserts always get v4. Order rows by `created_at`. If ordered ids become a requirement, either move the runtime to Python 3.14 or Postgres 18 (`uuidv7()`), or add a small uuid7 implementation; any of these is a one-line change in `app/db/types.py`.

## Timestamps

- `created_at` and `updated_at` on every table, `timestamp with time zone`, `NOT NULL`, server default `now()`.
- Python annotations map to column types through `TYPE_ANNOTATION_MAP`: `Mapped[datetime]` is always timezone aware.
- Values come back as timezone-aware datetimes. Store and compare in UTC.

### updated_at limitations

`updated_at` is refreshed by SQLAlchemy (`onupdate=func.now()`), not by a database trigger. This keeps the schema trigger-free, at a cost:

| Update made through | Refreshes `updated_at`? |
|---|---|
| ORM: change an attribute and flush | Yes |
| Core: `update(Model)...` executed through SQLAlchemy | Yes |
| Raw SQL: `text("UPDATE ...")`, `psql`, another service, a data fix in a migration | **No** |

Also:

- `now()` is the start time of the transaction. Two updates to the same row in one transaction leave the same `updated_at`, and on insert `created_at` equals `updated_at`.
- Any raw SQL update must set `updated_at = now()` itself. Review data-fix migrations for this.
- `tests/app/db/test_conventions.py::test_raw_sql_update_does_not_refresh_updated_at` pins the limitation so a change in behaviour is noticed.

If `updated_at` must be exact for every writer, replace `onupdate` with a `BEFORE UPDATE` trigger in a migration.

Rows are rarely updated anyway: the never-overwrite rule (roadmap section 3, principle 5) limits updates to status columns, and corrections are new rows.

## JSON columns

- Annotate `Mapped[dict[str, Any]]` to get `JSONObject`, a `jsonb` column that accepts only a JSON object.
- Checked in Python before the value reaches Postgres. Rejected with `JSONObjectError`:
  - a top-level list or scalar;
  - keys that are not strings;
  - NaN or infinity;
  - values JSON cannot hold (datetime, Decimal, set, UUID and so on). Convert them to strings or numbers first.
- `jsonb` does not keep key order or duplicate keys. Do not depend on either.
- Raw broker responses do **not** go in JSON columns; they go to object storage under neutral keys (`docs/data_layer/PLAN.md` section 6).

## Constraint names

Fixed by `NAMING_CONVENTION`, so migrations can always refer to a constraint by name:

| Kind | Pattern |
|---|---|
| Primary key | `pk_<table>` |
| Foreign key | `fk_<table>_<columns>_<referred table>` |
| Unique | `uq_<table>_<columns>` |
| Check | `ck_<table>_<name>` (give every `CheckConstraint` a `name`) |
| Index | `ix_<table>_<columns>` |
