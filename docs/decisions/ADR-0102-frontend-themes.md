# ADR-0102: Frontend themes (D-92)

Status: proposal

Provide pastel based light and dark themes. Use the operating-system color preference as the initial choice, and save an explicit user choice server side with a cookie fallback. Keep text colors in accessible semantic roles; accents and surfaces must not be used as body text.

Current implementation is partial: role-based tokens and a system preference are present. A persisted per-user theme preference and full screen parity checks remain open.
