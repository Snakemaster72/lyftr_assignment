#!/bin/sh
set -e
# Platform volumes (e.g. Fly.io) are mounted root-owned. When started as root, take
# ownership of /data for the app user, then run the server without root privileges.
if [ "$(id -u)" = "0" ]; then
  mkdir -p /data
  chown -R app:app /data
  exec setpriv --reuid=app --regid=app --init-groups "$@"
fi
exec "$@"
