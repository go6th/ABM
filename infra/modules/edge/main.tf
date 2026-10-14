# Public edge for the web app: global external HTTPS load balancer -> serverless NEG -> Cloud Run
# web, a Google-managed certificate for the domain, TLS 1.2+ (MODERN), HTTP redirected to HTTPS,
# and a Cloud Armor policy (preconfigured OWASP CRS rules, a stricter limit on sign-in routes, a
# per-client rate limit, Adaptive Protection alerts). The api and the worker are not exposed here:
# the web server reaches the api privately, Cloud Tasks reaches the worker. File uploads go from
# the browser straight to Cloud Storage, so large bodies never pass this policy.

variable "project_id" {
  type = string
}
variable "region" {
  type = string
}
variable "name" {
  type = string
}
variable "domain" {
  type = string
}
variable "web_service_name" {
  type = string
}
variable "rate_limit_per_minute" {
  type = number
}

resource "google_compute_global_address" "web" {
  project = var.project_id
  name    = "${var.name}-web-ip"
}

resource "google_compute_region_network_endpoint_group" "web" {
  project               = var.project_id
  name                  = "${var.name}-web-neg"
  region                = var.region
  network_endpoint_type = "SERVERLESS"
  cloud_run {
    service = var.web_service_name
  }
}

locals {
  # OWASP ModSecurity CRS 3.3 rule sets at sensitivity 1 (the fewest false positives); raise
  # after watching preview logs on staging
  waf = {
    1000 = ["sqli-v33-stable", "xss-v33-stable"]
    1010 = ["lfi-v33-stable", "rfi-v33-stable", "rce-v33-stable"]
    1020 = ["protocolattack-v33-stable", "sessionfixation-v33-stable", "scannerdetection-v33-stable"]
    1030 = ["methodenforcement-v33-stable"]
  }
}

resource "google_compute_security_policy" "web" {
  project = var.project_id
  name    = "${var.name}-web-armor"
  advanced_options_config {
    json_parsing = "STANDARD" # JSON bodies are inspected field by field, not as one string
    log_level    = "NORMAL"
  }
  adaptive_protection_config {
    layer_7_ddos_defense_config {
      enable = true
    }
  }
  dynamic "rule" {
    for_each = local.waf
    content {
      action      = "deny(403)"
      priority    = rule.key
      description = "OWASP CRS: ${join(", ", rule.value)}"
      match {
        expr {
          expression = join(" || ", [for r in rule.value : "evaluatePreconfiguredWaf('${r}', {'sensitivity': 1})"])
        }
      }
    }
  }
  rule {
    action      = "deny(403)"
    priority    = 1040
    description = "Log4j message lookup (CVE-2021-44228)"
    match {
      expr {
        expression = "evaluatePreconfiguredExpr('cve-canary')"
      }
    }
  }
  rule {
    action      = "rate_based_ban"
    priority    = 1500
    description = "Stricter per-client limit: sign-in, reset, invite accept, SSO discovery and start"
    match {
      expr {
        # the api skips its own per-IP bucket behind the web server (identity/rate_limit.py), so
        # this is the per-IP limit for these routes in the cloud (security audit M3)
        expression = "request.path.matches('^/v1/(auth/(login|email/(resend|verify)|password/(enroll|reset(/request)?)|invite/accept)|sso/(discover|[a-z0-9-]+/start))$')"
      }
    }
    rate_limit_options {
      conform_action   = "allow"
      exceed_action    = "deny(429)"
      ban_duration_sec = 600
      enforce_on_key   = "IP"
      rate_limit_threshold {
        count        = 30
        interval_sec = 60
      }
    }
  }
  rule {
    action      = "rate_based_ban"
    priority    = 1600
    description = "Looser per-client limit for the staff console's Google sign-in"
    match {
      expr {
        expression = "request.path.matches('^/api/operator/(login|callback|session)$')"
      }
    }
    rate_limit_options {
      conform_action   = "allow"
      exceed_action    = "deny(429)"
      ban_duration_sec = 300
      enforce_on_key   = "IP"
      rate_limit_threshold {
        count        = 60
        interval_sec = 60
      }
    }
  }
  rule {
    action      = "allow"
    priority    = 1900
    description = "Build assets (hashed, immutable) do not count against the per-client limit"
    match {
      expr {
        expression = "request.method == 'GET' && request.path.startsWith('/_next/static/')"
      }
    }
  }
  rule {
    action      = "rate_based_ban"
    priority    = 2000
    description = "Per-client rate limit"
    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = ["*"]
      }
    }
    rate_limit_options {
      conform_action   = "allow"
      exceed_action    = "deny(429)"
      ban_duration_sec = 300
      enforce_on_key   = "IP"
      rate_limit_threshold {
        count        = var.rate_limit_per_minute
        interval_sec = 60
      }
    }
  }
  rule {
    action      = "allow"
    priority    = 2147483647
    description = "Default allow"
    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = ["*"]
      }
    }
  }
}

resource "google_compute_ssl_policy" "web" {
  project         = var.project_id
  name            = "${var.name}-web-tls"
  profile         = "MODERN"
  min_tls_version = "TLS_1_2"
}

resource "google_compute_backend_service" "web" {
  project               = var.project_id
  name                  = "${var.name}-web-be"
  protocol              = "HTTPS"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  security_policy       = google_compute_security_policy.web.id
  backend {
    group = google_compute_region_network_endpoint_group.web.id
  }
  log_config {
    enable      = true
    sample_rate = 1.0
  }
}

resource "google_compute_url_map" "web" {
  project         = var.project_id
  name            = "${var.name}-web"
  default_service = google_compute_backend_service.web.id
}

resource "google_compute_managed_ssl_certificate" "web" {
  project = var.project_id
  name    = "${var.name}-web-cert"
  managed {
    domains = [var.domain]
  }
}

resource "google_compute_target_https_proxy" "web" {
  project          = var.project_id
  name             = "${var.name}-web-https"
  url_map          = google_compute_url_map.web.id
  ssl_certificates = [google_compute_managed_ssl_certificate.web.id]
  ssl_policy       = google_compute_ssl_policy.web.id
}

resource "google_compute_global_forwarding_rule" "https" {
  project               = var.project_id
  name                  = "${var.name}-web-https"
  ip_address            = google_compute_global_address.web.id
  port_range            = "443"
  target                = google_compute_target_https_proxy.web.id
  load_balancing_scheme = "EXTERNAL_MANAGED"
}

resource "google_compute_url_map" "redirect" {
  project = var.project_id
  name    = "${var.name}-web-redirect"
  default_url_redirect {
    https_redirect         = true
    strip_query            = false
    redirect_response_code = "MOVED_PERMANENTLY_DEFAULT"
  }
}

resource "google_compute_target_http_proxy" "redirect" {
  project = var.project_id
  name    = "${var.name}-web-http"
  url_map = google_compute_url_map.redirect.id
}

resource "google_compute_global_forwarding_rule" "http" {
  project               = var.project_id
  name                  = "${var.name}-web-http"
  ip_address            = google_compute_global_address.web.id
  port_range            = "80"
  target                = google_compute_target_http_proxy.redirect.id
  load_balancing_scheme = "EXTERNAL_MANAGED"
}

output "ip_address" {
  value       = google_compute_global_address.web.address
  description = "Point the domain's A record here; the certificate provisions after DNS resolves."
}
