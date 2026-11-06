// The one cross-site write the /v1 proxy accepts (fix prompt B, task 2): the SAML HTTP-POST
// binding. A customer's IdP answers a SAML sign-in with an auto-submitted form posted from the
// IdP's own page, so the request's Origin is the IdP (or "null"), not this site, and it carries no
// CSRF token. The API protects it on its own terms: the RelayState must name an unexpired,
// unconsumed login state bound to this browser's pre-auth cookie, and every assertion id is
// accepted once (identity/broker.complete_saml). Nothing else is widened: every other write still
// needs a same-origin request.

/** The ACS path, exactly (no trailing slash, no sub-path). */
export const SAML_ACS_PATH = "/v1/sso/saml/acs";
/** A SAML response is a few KB to some tens of KB; 256 KB is the outer bound the proxy streams. */
export const SAML_ACS_BODY_LIMIT = 256 * 1024;

/** True for the IdP's form post to the ACS: POST on exactly the ACS path. */
export function isSamlAcsPost(method: string, pathname: string): boolean {
  return method === "POST" && pathname === SAML_ACS_PATH;
}

/** The HTTP-POST binding's body is always form-encoded. */
export function isFormEncoded(contentType: string | null): boolean {
  const media = (contentType ?? "").split(";", 1)[0]!.trim().toLowerCase();
  return media === "application/x-www-form-urlencoded";
}
