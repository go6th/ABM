# Operator sign-in with Google Workspace

The operator console uses a separate Google Workspace OpenID Connect authorization-code flow. It requests only the openid and email scopes; no Google API access is requested. The Next.js server holds the OAuth client secret, exchanges the one-time code, checks the nonce, and asks the backend to verify the signed ID token before setting an HttpOnly session cookie. The browser never receives the ID token.

## Configure Google and the application

Create a Google OAuth web client and register this exact redirect URI:

    https://<operator-host>/api/operator/callback

For local development, Google accepts a localhost callback such as:

    http://localhost:3000/api/operator/callback

Set the same callback URI in web/.env.local. In Google Auth Platform, publish and verify the OAuth app, then enable **Session age claims** (`auth_time`) and **Authentication strength claims** (`amr`) in Advanced Settings. Google's [Security bundle guide](https://developers.google.com/identity/siwg/security-bundle) documents those prerequisites and the returned MFA values. The authorization request asks for both; sign-in is refused if the API does not receive a recent `auth_time` and an `amr` value containing `mfa`.

Set the Next.js server variables from web/.env.example and the API variables from the root .env.example. `STAFF_JWT_AUDIENCE` and `OPERATOR_GOOGLE_CLIENT_ID` must be the same OAuth client ID. Set `STAFF_WORKSPACE_DOMAIN` to the exact Google Workspace domain, then put Rajesh’s exact Workspace email in `STAFF_OPERATOR_EMAILS`. Other approved support staff can be listed in `STAFF_SUPPORT_EMAILS`. These lists are server-side, comma-separated, case-insensitive exact email matches. The API checks the signed token, audience, issuer, expiry, verified email, Workspace `hd` domain and MFA. The stable Google `sub` value is used as the staff ID. Google's [OIDC reference](https://developers.google.com/identity/openid-connect/reference) says to validate the signed `hd` claim for Workspace restrictions; the login-page domain hint alone is not an access control.

Do not put client secrets in source control or browser-exposed NEXT_PUBLIC_* variables. Keep the production redirect URI HTTPS. Google ID tokens are short-lived; staff must complete Workspace sign-in again after the token or MFA freshness window expires.

## Request path

1. /operator/login sends the browser to the server-side login route.
2. The server creates one-time state, nonce and PKCE values in short-lived HttpOnly cookies, then redirects to Google.
3. /api/operator/callback checks state, exchanges the code with PKCE, and checks the ID-token nonce.
4. The backend verifies the ID-token signature and claims, maps the verified email through the configured role allowlist, and requires fresh MFA.
5. The Next.js server stores the ID token in an HttpOnly, same-site cookie. Operator API traffic passes through the same-origin API proxy, which revalidates the token at the backend.

The login flow cannot grant operator access by a person's name. STAFF_OPERATOR_EMAILS must contain the exact verified Workspace email after it is supplied by the deployment owner.
