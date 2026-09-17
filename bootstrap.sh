#!/bin/bash
# Install/update Guardian, then open it. No backend or model menus.
set -euo pipefail
trap 'echo "Setup stopped. Read the error above, then run the install command again." >&2' ERR

# A piped installer must still allow Homebrew/password prompts at the terminal.
if [ ! -t 0 ] && { true </dev/tty; } 2>/dev/null; then
    exec </dev/tty
fi
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

printf '\nGuardian setup: local processing with Ollama\n'
echo "The first setup downloads software and a model. Keep this window open."

if [ "$(uname -s)" = Darwin ]; then
    if ! command -v brew >/dev/null; then
        echo "Installing Homebrew. Enter your Mac login password if asked."
        brew_installer=$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)
        /bin/bash -c "$brew_installer"
    fi
    missing=()
    command -v git >/dev/null || missing+=(git)
    command -v python3.12 >/dev/null || missing+=(python@3.12)
    command -v tesseract >/dev/null || missing+=(tesseract)
    command -v gs >/dev/null || missing+=(ghostscript)
    command -v ollama >/dev/null || missing+=(ollama)
    if [ ${#missing[@]} -gt 0 ]; then
        echo "Installing required software: ${missing[*]}"
        brew install "${missing[@]}"
    fi
else
    # Keep existing Linux installs usable without choosing a system package manager.
    for tool in git python3.12 tesseract ollama; do
        if ! command -v "$tool" >/dev/null; then
            echo "Automatic setup supports macOS. On Linux, install git, Python 3.12, Tesseract and Ollama first." >&2
            exit 1
        fi
    done
fi

# Reuse an existing checkout when invoked there; new installs have one stable home.
if [ -z "${GUARDIAN_INSTALL_DIR:-}" ]; then
    script_dir=""
    if [ -f "${BASH_SOURCE[0]:-}" ]; then
        script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
    fi
    if [ -n "$script_dir" ] && [ -f "$script_dir/app.py" ]; then
        GUARDIAN_INSTALL_DIR="$script_dir"
    elif [ -f "$PWD/app.py" ] && [ -f "$PWD/run_app.sh" ]; then
        GUARDIAN_INSTALL_DIR="$PWD"
    elif [ -f "$PWD/medical-report-deidentifier/app.py" ]; then
        GUARDIAN_INSTALL_DIR="$PWD/medical-report-deidentifier"
    else
        GUARDIAN_INSTALL_DIR="$HOME/Applications/Guardian"
    fi
fi

if [ -e "$GUARDIAN_INSTALL_DIR" ]; then
    if [ ! -d "$GUARDIAN_INSTALL_DIR/.git" ] && [ ! -f "$GUARDIAN_INSTALL_DIR/.git" ]; then
        echo "The install folder already exists but is not a Guardian checkout: $GUARDIAN_INSTALL_DIR" >&2
        exit 1
    fi
    cd "$GUARDIAN_INSTALL_DIR"
    if [ "$(git remote get-url origin)" != "https://github.com/litescale-ai/medical-report-deidentifier.git" ] ||
       [ "$(git branch --show-current)" != main ] || [ -n "$(git status --porcelain)" ]; then
        echo "This checkout has a different remote, branch, or local changes. Nothing was overwritten." >&2
        echo "To open the installed version, run: bash run_app.sh" >&2
        exit 1
    fi
    echo "Updating Guardian..."
    git pull --ff-only origin main
else
    mkdir -p "$(dirname "$GUARDIAN_INSTALL_DIR")"
    echo "Downloading Guardian to $GUARDIAN_INSTALL_DIR..."
    git clone https://github.com/litescale-ai/medical-report-deidentifier.git "$GUARDIAN_INSTALL_DIR"
    cd "$GUARDIAN_INSTALL_DIR"
fi

if [ ! -x .venv/bin/python ]; then
    echo "Preparing Python..."
    python3.12 -m venv .venv
fi
echo "Installing app packages. Download progress appears below."
.venv/bin/python -m pip install -r requirements.txt

# Update only local backend settings; preserve existing secrets and other settings.
.venv/bin/python - <<'PY'
from dotenv import dotenv_values, set_key
values = dotenv_values('.env')
set_key('.env', 'AGENT_BACKEND', 'ollama')
set_key('.env', 'OLLAMA_BASE_URL', 'http://127.0.0.1:11434/v1')
if not values.get('OLLAMA_MODEL'):
    set_key('.env', 'OLLAMA_MODEL', 'gemma4:e4b')
PY
chmod 600 .env

if [ "$(uname -s)" = Darwin ]; then
    mkdir -p "$HOME/Desktop"
    shortcut="$HOME/Desktop/Guardian.command"
    # Refuse to replace a user's unrelated shortcut with the same name.
    if [ ! -e "$shortcut" ] || grep -q '^# Guardian launcher$' "$shortcut"; then
        {
            printf '#!/bin/bash\n# Guardian launcher\n'
            printf 'if ! /bin/bash %q; then\n' "$PWD/run_app.sh"
            printf '  read -rp "Guardian could not start. Read the error above. Press Return to close."\nfi\n'
        } > "$shortcut"
        chmod +x "$shortcut"
        echo "Ready. Next time, double-click Guardian on your Desktop."
    else
        echo "Existing Desktop shortcut left unchanged. Open with: bash $PWD/run_app.sh"
    fi
fi
exec /bin/bash "$PWD/run_app.sh"
