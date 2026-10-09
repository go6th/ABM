# Identity Platform: the issuer of tenant API bearer tokens (JWKS_URL, JWT_ISSUER, JWT_AUDIENCE).
# Multi-tenancy lets each customer tenant federate its own IdP; the application still re-checks
# membership on every request (the token alone never grants access). Email/password sign-up is off.

variable "project_id" {
  type = string
}
variable "authorized_domains" {
  type = list(string)
}

resource "google_identity_platform_config" "main" {
  project                    = var.project_id
  authorized_domains         = var.authorized_domains
  autodelete_anonymous_users = true
  multi_tenant {
    allow_tenants = true
  }
  sign_in {
    allow_duplicate_emails = false
    email {
      enabled           = false
      password_required = false
    }
  }
}

output "jwks_url" {
  value = "https://www.googleapis.com/service_accounts/v1/jwk/securetoken@system.gserviceaccount.com"
}
output "issuer" {
  value = "https://securetoken.google.com/${var.project_id}"
}
output "audience" {
  value = var.project_id
}
