-- Reverse 0098: the scheduled-refresh tick is unbounded again.
ALTER TABLE platform_defaults
   DROP CONSTRAINT IF EXISTS ck_platform_defaults_tick_per_tenant,
   DROP CONSTRAINT IF EXISTS ck_platform_defaults_tick_starts,
   DROP COLUMN IF EXISTS schedule_tick_max_per_tenant,
   DROP COLUMN IF EXISTS schedule_tick_max_starts;
