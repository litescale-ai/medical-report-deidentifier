#!/bin/bash
# Guardian Medical De-identifier — Quick Install & Update
# Usage: curl -fsSL https://raw.githubusercontent.com/litescale-ai/medical-report-deidentifier/main/install.sh | bash
#
# This script:
#   1. Downloads the latest bootstrap.sh from GitHub
#   2. bootstrap.sh handles: clone/pull, venv, deps, config, and launch
#
# Safe to re-run — it will pull latest changes and upgrade dependencies.

set -euo pipefail

REPO="litescale-ai/medical-report-deidentifier"
BRANCH="main"
SCRIPT_URL="https://raw.githubusercontent.com/${REPO}/${BRANCH}/bootstrap.sh"

echo ""
echo "⬇️  Fetching Guardian Medical De-identifier installer..."
echo ""

# Always fetch the latest bootstrap.sh from GitHub so updates take effect
exec bash -c "$(curl -fsSL "$SCRIPT_URL")"
