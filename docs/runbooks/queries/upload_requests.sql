-- Provider credits exhausted: first check (read only, operator role, identifiers and counts only).
SELECT event, count(*) AS n FROM spend_limit_events WHERE event IN ('exhausted', 'upload_requested', 'upload_accepted', 'upload_rejected') GROUP BY event ORDER BY event;
