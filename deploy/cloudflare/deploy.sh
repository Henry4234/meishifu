#!/usr/bin/env bash
set -euo pipefail

WRANGLER_BIN="${WRANGLER_BIN:-wrangler}"

"${WRANGLER_BIN}" deploy \
  --config deploy/cloudflare/wrangler.jsonc
