# ADR-0020: projects, region and egress (decision D-20)

- Status: Proposed (needs the owner's project ids and region confirmation)
- Date: 2026-10-03

## Decision (proposal)

- **One project per environment**: dev, staging, production (`infra/envs/*`, identical stacks,
  values only in `terraform.tfvars`). Isolation between tenants is still the database's job; the
  project split isolates environments.
- **Region `asia-south1`** for every regional resource (default variable). VERIFY(V-REGION-1):
  availability of Cloud Run, Cloud SQL, Cloud Tasks, Memorystore and the chosen Claude models on
  Vertex AI in that region is not checked here.
- **Egress**: Cloud Run uses direct VPC egress for all traffic; outbound provider calls leave
  through Cloud NAT with one reserved address (`nat_ip` output) that providers can allow-list.
  Cloud SQL and Memorystore have private addresses only.
- **No key files**: CI deploys through Workload Identity Federation restricted to one repository
  and its main branch.
