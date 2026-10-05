#!/usr/bin/env bash
# Create/update the "datalab-api-env" Secret from a local .env, overriding the
# URLs that differ between local development and the cluster.
#
#   deploy/create-env-secret.sh [path/to/.env]
set -euo pipefail

ENV_FILE=${1:-.env}
NAMESPACE=datalab-api
API_URL=https://api.datalab.ifca.es
PORTAL_URL=https://portal.datalab.ifca.es

tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT

grep -vE '^\s*(#|$)' "$ENV_FILE" \
  | grep -vE '^(FRONTEND_URL|CORS_ORIGINS|GITHUB_REDIRECT_URI|KEYCLOAK_CALLBACK_URL|COOKIE_SECURE)=' >"$tmp"
cat >>"$tmp" <<EOF
FRONTEND_URL=$PORTAL_URL
CORS_ORIGINS=$PORTAL_URL
GITHUB_REDIRECT_URI=$API_URL/auth/github/callback
KEYCLOAK_CALLBACK_URL=$API_URL/auth/keycloak/callback
COOKIE_SECURE=true
EOF

kubectl -n "$NAMESPACE" create secret generic datalab-api-env \
  --from-env-file="$tmp" --dry-run=client -o yaml | kubectl apply -f -
