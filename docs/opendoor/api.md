# API reference

`GET /v1/signals?cursor=<opaque>`: role `read_public` (every tenant role), tenant-scoped (RLS),
audited, client-safe. Returns `signal_page.v1`: `{"schema", "items": [signal_event.v1...],
"next_cursor"}`, oldest first, 50 per page, keyset cursor (detected_at, id). A malformed cursor is
422. Every response is validated against the schema before it is sent.
