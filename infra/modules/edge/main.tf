# Public edge for the web app: global external HTTPS load balancer -> serverless NEG -> Cloud Run
# web, a Google-managed certificate for the domain, HTTP redirected to HTTPS, and a Cloud Armor
# policy (preconfigured OWASP rules and a per-client rate limit). The api is not exposed here: the
# web server reaches it privately.

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

resource "google_compute_security_policy" "web" {
  project = var.project_id
  name    = "${var.name}-web-armor"
  rule {
    action      = "deny(403)"
    priority    = 1000
    description = "OWASP: SQL injection and XSS"
    match {
      expr {
        expression = "evaluatePreconfiguredWaf('sqli-v33-stable') || evaluatePreconfiguredWaf('xss-v33-stable')"
      }
    }
  }
  rule {
    action      = "rate_based_ban"
    priority    = 1500
    description = "Stricter per-client rate limit for authentication endpoints"
    match {
      expr {
        expression = "request.path.matches('^/v1/auth/(login|email/(resend|verify)|password/enroll)$')"
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
