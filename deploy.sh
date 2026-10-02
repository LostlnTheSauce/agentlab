#!/bin/sh
# Auto-deploy, run by cron every 5 minutes on the server.
# If GitHub has a newer commit than the server's copy, pull it, copy the app into the live
# folder, install any new packages, and tell Passenger to restart. data/ and .env are never touched.
REPO="$HOME/repositories/agentlab"
APP="$HOME/agentlab"
GIT=$(command -v git || echo /usr/local/cpanel/3rdparty/bin/git)

cd "$REPO" || exit 1
"$GIT" fetch -q origin main || exit 1
[ "$("$GIT" rev-parse HEAD)" = "$("$GIT" rev-parse origin/main)" ] && exit 0
"$GIT" merge -q --ff-only origin/main || { echo "$(date -u +%FT%TZ) could not fast-forward; deploy manually"; exit 1; }

/bin/cp -R lab web tests passenger_wsgi.py requirements.txt README.md .env.example "$APP/" || exit 1
"$HOME/virtualenv/agentlab/3.13/bin/pip" install -q -r "$APP/requirements.txt"
mkdir -p "$APP/tmp" && touch "$APP/tmp/restart.txt"
echo "$(date -u +%FT%TZ) deployed $("$GIT" rev-parse --short HEAD)"
