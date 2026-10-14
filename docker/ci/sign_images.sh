#!/usr/bin/env bash
# Keyless cosign signature (GitHub OIDC, Sigstore) for each image digest given as an argument.
# The cosign container cannot use gcloud's credential helper, so a short-lived access token goes
# into a temporary mode-0700 Docker config that is removed on exit. No key material exists.
# Needs: an authenticated gcloud (Workload Identity Federation), id-token: write, ARTIFACT_REGISTRY.
set -Eeuo pipefail
: "${ARTIFACT_REGISTRY:?}" "${ACTIONS_ID_TOKEN_REQUEST_URL:?}" "${ACTIONS_ID_TOKEN_REQUEST_TOKEN:?}"

docker_config="$(mktemp -d)"
chmod 700 "$docker_config"
trap 'rm -rf "$docker_config"' EXIT
registry_host="${ARTIFACT_REGISTRY%%/*}"
token="$(gcloud auth print-access-token)"
auth="$(printf 'oauth2accesstoken:%s' "$token" | base64 -w0)"
printf '{"auths":{"%s":{"auth":"%s"}}}' "$registry_host" "$auth" > "$docker_config/config.json"
unset token auth

oidc="$(curl --fail --silent --show-error \
  --header "Authorization: Bearer ${ACTIONS_ID_TOKEN_REQUEST_TOKEN}" \
  "${ACTIONS_ID_TOKEN_REQUEST_URL}&audience=sigstore" | jq -er '.value')"
for ref in "$@"; do
  docker run --rm -e SIGSTORE_ID_TOKEN="$oidc" -e DOCKER_CONFIG=/root/.docker \
    -v "$docker_config:/root/.docker:ro" ghcr.io/sigstore/cosign/cosign:v3.1.3 sign --yes "$ref"
done
