# 02 Data layer playbook: adding a table

Rules live in `docs/data_layer/CONVENTIONS.md`; this is the procedure and the test pattern that found real bugs in steps 7 and 8. Follow every step.

## 1. Model (`app/db/models/<name>.py`)

```python
class Thing(Model):                      # Model = UUID id + created_at + updated_at
    __tablename__ = "things"
    __table_args__ = (
        UniqueConstraint("account_id", "key"),
        CheckConstraint("length(btrim(key)) > 0", name="key_not_blank"),   # ck_things_key_not_blank
        {"schema": SCHEMA, "comment": "What it is (roadmap s11.1 ...)"},
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.accounts.id", ondelete="RESTRICT"), nullable=False
    )
    grade: Mapped[str] = mapped_column(enum_type(Grade), nullable=False)    # ck_things_grade
```

- Every `CheckConstraint` gets a short `name`; the convention prefixes `ck_<table>_`.
- Foreign keys `ondelete="RESTRICT"` (never delete). Same-account integrity across tables uses a composite foreign key to a `UNIQUE (id, account_id)` (see `runs.parent_run_id`).
- An index on an expression or sort order: define it **after the class** on mapped columns (`Index("ix_runs_account_id_created_at", Run.account_id, Run.created_at.desc())`) so it gets a full name and Alembic can compare it.
- A column a lifecycle method reads before the first flush needs its default set in `__init__` (see `Run.__init__`); `default=` only applies at INSERT.
- Validate and normalise on assignment with `@validates` (see `Account.domain`), and keep the database CHECK as the real guarantee.
- Register the module in `app/db/models/__init__.py`.

## 2. Migration (`app/db/migrations/versions/<date>_000N_<slug>.py`)

1. Start a throwaway database at head and autogenerate: `make migration m="create things" rev=000N MIGRATE_DATABASE_URL=postgresql://postgres@127.0.0.1:<port>/postgres`.
2. **Rewrite the output by hand:**
   - Wrap every explicit name in `op.f("...")`, or the convention is applied twice (`ck_things_ck_things_key`).
   - Enum columns become `sa.String(length=64)` plus one `sa.CheckConstraint(_in_list(col, VALUES), name=op.f("ck_things_<enum>"))` with the values written out. Never `sa.Enum(...)`: autogenerate emits duplicate CHECKs, and the migration must be a fixed snapshot.
   - Server defaults and comments match the model exactly (`alembic check` compares them).
   - The downgrade drops indexes, then the table.
3. Apply and inspect the real catalog, not only `alembic check` (it does not compare CHECK names or bodies):
   ```sql
   SELECT conname FROM pg_constraint WHERE conrelid = 'intel.things'::regclass ORDER BY 1;
   SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'things';
   ```
4. `alembic check` must print "No new upgrade operations detected", and `downgrade base` must succeed.

## 3. Tests (`tests/app/db/test_<name>.py`)

Use the `migrated_database_url` fixture and the helpers in `tests/app/db/helpers.py`.

```python
CHECKS = [  # (constraint name, rows that must pass, rows that must fail)
    ("ck_things_key_not_blank", [{"key": ("a", "text")}], [{"key": (" ", "text")}]),
]

@pytest.mark.parametrize(("name", "good", "bad"), CHECKS, ids=[c[0] for c in CHECKS])
def test_each_check_constraint(engine, name, good, bad):
    with engine.connect() as conn:
        for row in good: assert constraint_holds(conn, name, row)
        for row in bad:  assert not constraint_holds(conn, name, row)

def test_every_check_constraint_is_tested(engine):
    with engine.connect() as conn:
        assert check_constraint_names(conn, "intel.things") == {c[0] for c in CHECKS}
```

Why evaluate each CHECK alone: an INSERT only reports the **first** failing constraint, and Postgres checks them in name order, so a broad constraint hides a narrow one. `constraint_holds` reads the definition back from Postgres and evaluates it on one row.

Also write, for every table:
- real INSERTs of bad rows rejected (`CheckViolation`, `NotNullViolation`, `ForeignKeyViolation`, `UniqueViolation` with `diag.constraint_name`);
- a full valid row accepted through the ORM and through raw SQL (server defaults);
- delete of a referenced parent refused;
- indexes present with their exact definition;
- model methods (lifecycle, normalisation) without a database.

The cross-table tests in `tests/app/db/test_migrations.py` (models match migrations, enum CHECKs match code, conventions) cover the new table automatically once it is registered.

## 4. Gates, then commit

`bash .claude/skills/account-intelligence/scripts/gates.sh` with `PG_BIN` set. One commit per step, message describing the table, its rules and the tests. Update `docs/BUILD_LOG.md` and, if a decision moved, `00-reconciliation.md` and `docs/data_layer/PLAN.md`.
