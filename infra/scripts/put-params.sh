#!/usr/bin/env bash
# Creates the /thalweg/* SSM parameters that CloudFormation cannot hold. Run once per account,
# before the first deploy. Parameters that already exist are left untouched; values are never
# printed.
#
#   VENICE_API_KEY=...  THALWEG_NOTIFY_EMAIL=ops@example.org  infra/scripts/put-params.sh --profile <p>
#
#   /thalweg/venice_api_key      SecureString  from $VENICE_API_KEY
#   /thalweg/reviewer_passcode   SecureString  random, 8 chars [a-z0-9]
#   /thalweg/mcp_token           SecureString  random, 32 hex
#   /thalweg/notify_email        String        from $THALWEG_NOTIFY_EMAIL (budget + alarm mail)
#
# Read one back:  aws ssm get-parameter --with-decryption --name /thalweg/reviewer_passcode \
#                   --query Parameter.Value --output text
set -euo pipefail
export MSYS_NO_PATHCONV=1  # keep /thalweg/... intact under Git Bash

AWS_ARGS=("$@")
REGION_SET=false
for a in "$@"; do [[ "$a" == "--region" ]] && REGION_SET=true; done
$REGION_SET || AWS_ARGS+=(--region "${AWS_REGION:-us-east-1}")

exists() { aws ssm get-parameter --name "$1" "${AWS_ARGS[@]}" >/dev/null 2>&1; }

put() {  # name type value
  if exists "$1"; then
    echo "kept    $1"
  elif [[ -z "$3" ]]; then
    echo "skipped $1 (no value given)" >&2
  else
    aws ssm put-parameter --name "$1" --type "$2" --value "$3" \
      --tags Key=project,Value=thalweg "${AWS_ARGS[@]}" >/dev/null
    echo "created $1"
  fi
}

# `|| true`: tr exits on SIGPIPE once head has enough characters.
random_lower_digits() { { LC_ALL=C tr -dc 'a-z0-9' </dev/urandom || true; } | head -c "$1"; }
random_hex() { { LC_ALL=C tr -dc 'a-f0-9' </dev/urandom || true; } | head -c "$1"; }

put /thalweg/venice_api_key SecureString "${VENICE_API_KEY:-}"
put /thalweg/reviewer_passcode SecureString "$(random_lower_digits 8)"
put /thalweg/mcp_token SecureString "$(random_hex 32)"
put /thalweg/notify_email String "${THALWEG_NOTIFY_EMAIL:-}"
