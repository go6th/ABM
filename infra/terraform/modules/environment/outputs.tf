output "manifest" {
  description = "Environment contract consumed by the deploy script and the cloud module."
  value       = terraform_data.environment.output
}
