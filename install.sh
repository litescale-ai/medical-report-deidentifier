#!/bin/bash
# Guardian Medical De-identifier — Quick Install
# Usage: curl -fsSL https://get.litescale.ai/guardian | bash

set -euo pipefail

REPO="litescale-ai/medical-report-deidentifier"
BRANCH="main"
SCRIPT_URL="https://raw.githubusercontent.com/${REPO}/${BRANCH}/bootstrap.sh"

echo ""
echo "⬇️  Fetching Guardian Medical De-identifier installer..."
echo ""

exec bash -c "$(curl -fsSL "$SCRIPT_URL")"
