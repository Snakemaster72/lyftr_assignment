#!/bin/sh
# Runs from the nginx image's entrypoint. Writes /config.js so one image can point at any backend.
set -e
if [ -n "${API_BASE_URL:-}" ]; then
  printf 'window.__APP_CONFIG__ = { API_BASE_URL: "%s" };\n' "$API_BASE_URL" > /usr/share/nginx/html/config.js
  echo "runtime config: API_BASE_URL=$API_BASE_URL"
fi
