# Versioning and changelog policy

* Inside v1 only additive changes: new optional fields, new enum values for readers that ignore
  unknown ones. `opendoor.events.breaking_changes` fails CI on a removed field, a narrowed type or
  constraint, or a field that becomes required.
* A breaking change is a new major version (`signal_event.v2`) served beside v1, with `Sunset` and
  `Deprecation` headers on v1 for at least the announced period (TODO: product owner).

## Changelog
| Version | Date | Change |
|---|---|---|
| v1 | 2026-10-07 | initial contract |
