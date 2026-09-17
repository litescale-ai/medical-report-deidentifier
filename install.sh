#!/bin/bash
# Public entry point: curl -fsSL https://raw.githubusercontent.com/litescale-ai/medical-report-deidentifier/main/install.sh | bash
set -euo pipefail
installer=$(mktemp "${TMPDIR:-/tmp}/guardian-install.XXXXXX")
trap 'rm -f "$installer"' EXIT
echo "Downloading Guardian setup..."
curl -fsSL https://raw.githubusercontent.com/litescale-ai/medical-report-deidentifier/main/bootstrap.sh -o "$installer"
/bin/bash "$installer"
