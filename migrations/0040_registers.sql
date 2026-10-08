CREATE TABLE metric_snapshots (
    id bigserial PRIMARY KEY,
    name text NOT NULL,
    value numeric NOT NULL,
    base integer,
    snapshot_at timestamptz NOT NULL
);
GRANT SELECT, INSERT ON metric_snapshots TO app_rw;
