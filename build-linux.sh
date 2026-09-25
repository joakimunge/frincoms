#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if ! command -v docker >/dev/null 2>&1; then
    printf 'Docker is required to build Frincoms for Linux.\n' >&2
    exit 1
fi

# Keep macOS/Windows outputs untouched; copy the Linux bundle into its own
# directory. Copying the contents avoids nested folders on repeated builds.
docker build --platform linux/amd64 -f Dockerfile.linux -t frincoms-linux .
docker run --rm --platform linux/amd64 \
    -v "$(pwd)/dist-linux:/output" \
    frincoms-linux \
    sh -c 'mkdir -p /output/Frincoms && cp -a /src/dist/Frincoms/. /output/Frincoms/ && chmod +x /output/Frincoms/Frincoms'

printf 'Linux build ready: %s/dist-linux/Frincoms/Frincoms\n' "$(pwd)"
