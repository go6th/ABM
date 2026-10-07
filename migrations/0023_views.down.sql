-- Rollback of 0023_views.sql (development only; production never contracts in the same release).
DROP TABLE IF EXISTS branding_assets;
DROP TABLE IF EXISTS export_packages;
DROP TABLE IF EXISTS saved_views;
DROP TABLE IF EXISTS view_materializations;
DROP TABLE IF EXISTS view_rows;
