#!/usr/bin/env bash
# PRINTS the commands that release staging. It runs nothing: every command touches the owner's
# Google Cloud project, credentials or billing, so the owner runs them.
#
# Staging is project abmgenerator in us-central1 with three images (api, worker, web) in the
# Artifact Registry ai-staging-images. Releases from feat/final-product are built on Cloud Build
# and rolled out with gcloud (DEPLOYMENT.md section 2.2); the GitHub workflow in section 2.1 runs
# only for pushes to main. The recipe is printed from DEPLOYMENT.md itself so it cannot drift
# from the document (the old one-image, asia-south1 recipe this script printed was wrong).
set -euo pipefail
cd "$(dirname "$0")/.."
echo "# DEPLOYMENT.md section 2.2 (manual Cloud Build + gcloud release). Read the section first."
awk '/^### 2\.2 /{inside=1} inside && /^```bash/{code=1; next} inside && code && /^```/{exit} code{print}' DEPLOYMENT.md
