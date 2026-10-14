# Billing budget for the environment's project with alert thresholds (notifications go to the
# billing account's budget recipients and the given monitoring channels). A budget alerts; it does
# not stop spend. Model spend is capped in the application (per phase and per run).

variable "billing_account" {
  type = string
}
variable "project_number" {
  type = string
}
variable "name" {
  type = string
}
variable "monthly_budget_usd" {
  type = number
  validation {
    condition     = var.monthly_budget_usd > 0
    error_message = "monthly_budget_usd must be a positive amount (the owner's input)."
  }
}
variable "notification_channels" {
  type = list(string)
}

resource "google_billing_budget" "env" {
  billing_account = var.billing_account
  display_name    = "${var.name} monthly"
  budget_filter {
    projects = ["projects/${var.project_number}"]
  }
  amount {
    specified_amount {
      currency_code = "USD"
      units         = tostring(var.monthly_budget_usd)
    }
  }
  threshold_rules {
    threshold_percent = 0.5
  }
  threshold_rules {
    threshold_percent = 0.9
  }
  threshold_rules {
    threshold_percent = 1.0
  }
  # an early warning: the month is on course to pass the budget
  threshold_rules {
    threshold_percent = 1.0
    spend_basis       = "FORECASTED_SPEND"
  }
  all_updates_rule {
    monitoring_notification_channels = var.notification_channels
    disable_default_iam_recipients   = false
  }
}
