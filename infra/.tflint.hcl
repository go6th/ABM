# tflint for every root and module under infra/ (run with --recursive from infra/):
#   docker run --rm -v "$PWD/infra:/data" -w /data ghcr.io/terraform-linters/tflint --init
#   docker run --rm -v "$PWD/infra:/data" -w /data ghcr.io/terraform-linters/tflint --recursive
config {
  call_module_type = "local"
}

plugin "terraform" {
  enabled = true
  preset  = "recommended"
}

plugin "google" {
  enabled = true
  version = "0.30.0"
  source  = "github.com/terraform-linters/tflint-ruleset-google"
}
