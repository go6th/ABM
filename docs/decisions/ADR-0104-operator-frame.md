# ADR-0104: Separate operator frame (D-94)

Status: proposal

Keep staff administration in a separate route group with its own login, persistent operator bar, navigation, session cookie and backend verified identity. The operator UI may show internal operational cost only where the operator API returns it. Tenant sessions cannot authorize operator routes.

The operator route group, same-origin proxy, Google Workspace authorization-code flow and separate cookie are implemented. Deployment still needs a registered OAuth client, Workspace domain, exact admin email allowlist and enabled Google MFA claims.
