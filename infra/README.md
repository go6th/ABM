# Infrastructure

## Why Terraform

Terraform over Pulumi, for these reasons:

1. **The cloud is not chosen yet** (roadmap section 16). Terraform providers exist for every candidate, and the environment contract here is cloud-neutral, so the choice adds a module rather than a rewrite.
2. **Reviewable plans.** `terraform plan` output is plain text that a reviewer can read in a pull request without running a program.
3. **No extra runtime or hosted state service.** Pulumi programs run in a language runtime and default to a hosted state backend; Terraform state can sit in a bucket in whichever cloud is chosen.
4. **Hiring and support.** DevOps engineers are more likely to know HCL than Pulumi.

The trade-off: Pulumi would let infrastructure share Python with the app. That matters less than the four points above at this stage.

## Layout

```
terraform/
  modules/environment/   cloud-neutral environment contract (names, buckets, secret names, scaling, protections)
  envs/dev/              terraform.tfvars + root module
  envs/staging/
  envs/production/
```

Cloud and region are variables in each `terraform.tfvars` (`cloud_provider`, `region`), set to `unset` until decided. Production refuses to plan while `cloud_provider` is `unset`. Dev and staging run with `model_mode = "fake"` and `broker_mode = "recorded"`, so nothing spends credits.

## Secrets

The app reads secrets only from environment variables (`app/settings.py`). The module lists the secret names (`secret_names`); values live in the cloud secrets manager and are injected at run time. Nothing secret goes into this repository or Terraform state.

## Deploying

```
make deploy-staging              # full deploy (needs IMAGE_REGISTRY, DEPLOY_CREDENTIALS_REF and a chosen cloud)
DRY_RUN=1 make deploy-staging    # preflight, quality gate, image build if docker is reachable, terraform plan
```

`scripts/deploy.sh` stops with a clear message at the first step that needs something not yet decided.

## What is needed to finish (Needs human)

1. Choose the cloud and region; set them in `envs/*/terraform.tfvars`.
2. Add a remote state backend block to each `envs/*/main.tf`.
3. Add a cloud module that consumes `module.environment.manifest`: container registry, container platform for the worker, managed Postgres, two buckets, secrets manager entries, and Temporal (managed or self-hosted).
4. Add the worker rollout command to the end of `scripts/deploy.sh`.
5. Store `IMAGE_REGISTRY` and `DEPLOY_CREDENTIALS_REF` as CI secrets.
