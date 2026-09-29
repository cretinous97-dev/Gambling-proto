#!/usr/bin/env bash
#
# Build the site once and place it everywhere a consumer looks for it.
#
# Vercel decides where the "output" is and fails the deployment when that
# directory does not exist:
#
#     Error: No Output Directory named "public" found after the Build completed.
#
# In the platform's build source the rule is:
#
#     usedOutputDirectory = outputDirectory || "public"
#
# so an unset output directory means `public/`, while a project setting in the
# dashboard overrides vercel.json. Rather than depend on which of those wins,
# the build writes to every location that could be expected:
#
#   1. public/          the platform's default output directory
#   2. frontend/dist/   Vite's output, used by the local server
#   3. backend/static/  bundled into the API function, which serves the site
#                       (the function's includeFiles pattern is "backend/**",
#                       so the site has to live inside backend/)
#
# All three are build output and are git-ignored. Keeping it in one script
# means the local build and the deploy build cannot drift apart - which is how
# the previous two failures happened.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT/frontend"

echo "==> building the site"
npm run build

echo "==> placing the build output"
rm -rf "$ROOT/public" "$ROOT/backend/static"
mkdir -p "$ROOT/public" "$ROOT/backend/static"

# Vite's output stays in frontend/dist; copy it to the two other locations.
cp -R dist/. "$ROOT/public/"
cp -R dist/. "$ROOT/backend/static/"

for target in "$ROOT/public" "$ROOT/backend/static"; do
  if [ ! -f "$target/index.html" ]; then
    echo "ERROR: $target/index.html is missing - the deployment would fail" >&2
    exit 1
  fi
  echo "    $target  ($(find "$target" -type f | wc -l | tr -d ' ') files)"
done

echo "==> build complete"
