#!/bin/bash
# Guardian Medical De-identifier — Automated Installer & Bootstrapper
# Uses Charmbracelet's `gum` for premium UI when available, ANSI fallback otherwise.

set -euo pipefail
trap 'echo ""; echo "  👋 Bye!"; exit 0' INT TERM

# ─────────────────────────────────────────────
# ANSI colour palette (zero-dependency fallback)
# ─────────────────────────────────────────────
BOLD='\033[1m'
DIM='\033[2m'
ITALIC='\033[3m'
UNDERLINE='\033[4m'
NC='\033[0m'

# Foreground
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
CYAN='\033[0;36m'
WHITE='\033[1;37m'

# Bright
BR_MAGENTA='\033[1;35m'
BR_CYAN='\033[1;36m'
BR_GREEN='\033[1;32m'
BR_YELLOW='\033[1;33m'
BR_RED='\033[1;31m'

# ─────────────────────────────────────────────
# Detect or install gum
# ─────────────────────────────────────────────
HAS_GUM=false
if command -v gum &>/dev/null; then
    HAS_GUM=true
fi

# ─────────────────────────────────────────────
# UI helper functions
# ─────────────────────────────────────────────
banner() {
    echo ""
    if $HAS_GUM; then
        gum style \
            --border double \
            --border-foreground 99 \
            --foreground 212 \
            --bold \
            --padding "1 4" \
            --align center \
            --width 72 \
            "🛡️  GUARDIAN MEDICAL DE-IDENTIFIER" \
            "" \
            "Automated Installer & Launcher"
    else
        echo -e "${BR_MAGENTA}╔══════════════════════════════════════════════════════════════════════╗${NC}"
        echo -e "${BR_MAGENTA}║                                                                    ║${NC}"
        echo -e "${BR_MAGENTA}║${NC}       ${BOLD}🛡️  GUARDIAN MEDICAL DE-IDENTIFIER${NC}                          ${BR_MAGENTA}║${NC}"
        echo -e "${BR_MAGENTA}║${NC}       ${DIM}Automated Installer & Launcher${NC}                              ${BR_MAGENTA}║${NC}"
        echo -e "${BR_MAGENTA}║                                                                    ║${NC}"
        echo -e "${BR_MAGENTA}╚══════════════════════════════════════════════════════════════════════╝${NC}"
    fi
    echo ""
}

section() {
    # $1 = section title
    echo ""
    if $HAS_GUM; then
        gum style \
            --foreground 99 \
            --bold \
            --border normal \
            --border-foreground 240 \
            --padding "0 2" \
            --width 72 \
            "$1"
    else
        echo -e "${BR_CYAN}┌──────────────────────────────────────────────────────────────────────┐${NC}"
        printf  "${BR_CYAN}│${NC}  ${BOLD}${WHITE}%-66s${NC}  ${BR_CYAN}│${NC}\n" "$1"
        echo -e "${BR_CYAN}└──────────────────────────────────────────────────────────────────────┘${NC}"
    fi
    echo ""
}

step() {
    # $1 = step number, $2 = description
    if $HAS_GUM; then
        gum style --foreground 212 --bold --italic "  ▸ Step $1  $2"
    else
        echo -e "  ${BR_MAGENTA}▸ Step $1${NC}  ${ITALIC}$2${NC}"
    fi
}

success() {
    if $HAS_GUM; then
        gum style --foreground 46 "  ✓ $1"
    else
        echo -e "  ${BR_GREEN}✓${NC} $1"
    fi
}

warn() {
    if $HAS_GUM; then
        gum style --foreground 214 "  ⚠ $1"
    else
        echo -e "  ${BR_YELLOW}⚠${NC} $1"
    fi
}

fail() {
    if $HAS_GUM; then
        gum style --foreground 196 "  ✗ $1"
    else
        echo -e "  ${BR_RED}✗${NC} $1"
    fi
}

info() {
    if $HAS_GUM; then
        gum style --foreground 117 "  ℹ $1"
    else
        echo -e "  ${CYAN}ℹ${NC} $1"
    fi
}

spin() {
    # $1 = title, remaining args = command
    local title="$1"; shift
    if $HAS_GUM; then
        gum spin --spinner dot --title "$title" -- "$@"
    else
        echo -en "  ${DIM}⏳ $title${NC}"
        "$@" &>/dev/null
        echo -e "\r  ${BR_GREEN}✓${NC} $title"
    fi
}

choose() {
    # Prints chosen value to stdout
    # Usage: result=$(choose "Option A" "Option B" "Option C")
    if $HAS_GUM; then
        gum choose --cursor.foreground 212 --selected.foreground 46 "$@"
    else
        # Numbered fallback
        local i=1
        for opt in "$@"; do
            echo -e "    ${BR_MAGENTA}${i})${NC} $opt" >&2
        done
        echo "" >&2
        while true; do
            read -rp "    Enter choice [1-$#]: " num
            if [[ "$num" =~ ^[0-9]+$ ]] && [ "$num" -ge 1 ] && [ "$num" -le $# ]; then
                # Return the chosen option text
                local j=0
                for opt in "$@"; do
                    j=$((j + 1))
                    if [ "$j" -eq "$num" ]; then
                        echo "$opt"
                        return
                    fi
                done
            fi
            echo -e "    ${BR_YELLOW}Please enter a number between 1 and $#.${NC}" >&2
        done
    fi
}

confirm() {
    # $1 = prompt text. Returns 0 (yes) or 1 (no).
    if $HAS_GUM; then
        gum confirm --prompt.foreground 212 "$1"
    else
        read -rp "  $1 [Y/n]: " ans
        [[ ! "$ans" =~ ^[Nn] ]]
    fi
}

input_secret() {
    # $1 = placeholder. Prints value to stdout.
    if $HAS_GUM; then
        gum input --password --placeholder "$1" --prompt.foreground 212
    else
        read -rsp "  $1: " val
        echo "" >&2
        echo "$val"
    fi
}

input_text() {
    # $1 = placeholder. Prints value to stdout.
    if $HAS_GUM; then
        gum input --placeholder "$1" --prompt.foreground 212
    else
        read -rp "  $1: " val
        echo "$val"
    fi
}

divider() {
    if $HAS_GUM; then
        gum style --foreground 240 "  ─────────────────────────────────────────────"
    else
        echo -e "  ${DIM}─────────────────────────────────────────────${NC}"
    fi
}


# ═══════════════════════════════════════════════
# MAIN SCRIPT
# ═══════════════════════════════════════════════

banner

# ─────────────────────────────────────────────
# 0. Pre-flight checks
# ─────────────────────────────────────────────
section "⚡ Pre-flight Checks"

missing=()

if command -v git &>/dev/null; then
    success "git found"
else
    fail "git not found"
    missing+=("git")
fi

if command -v python3.12 &>/dev/null; then
    success "python3.12 found"
else
    fail "python3.12 not found"
    missing+=("python3.12")
fi

# Tesseract is needed by ocrmypdf for scanned PDF support
if command -v tesseract &>/dev/null; then
    success "tesseract found (OCR support)"
else
    warn "tesseract not found (needed for scanned PDF support)"
    if command -v brew &>/dev/null; then
        if confirm "Install tesseract via Homebrew?"; then
            spin "Installing tesseract..." brew install tesseract
            success "tesseract installed"
        else
            warn "Scanned PDFs will not be de-identified without tesseract"
        fi
    else
        warn "Install tesseract manually for scanned PDF support"
    fi
fi

# Offer to install gum if not present
if ! $HAS_GUM; then
    info "Optional: install 'gum' for a premium installer experience"
    if command -v brew &>/dev/null; then
        if confirm "Install gum (Charmbracelet) via Homebrew?"; then
            spin "Installing gum..." brew install gum
            HAS_GUM=true
            success "gum installed — UI upgraded ✨"
        fi
    fi
fi

if [ ${#missing[@]} -ne 0 ]; then
    echo ""
    fail "Missing required tools: ${missing[*]}"
    echo ""
    echo -e "  ${BOLD}Please install them before running this script:${NC}"
    echo ""
    echo -e "  ${UNDERLINE}macOS (Homebrew):${NC}"
    [[ " ${missing[*]} " == *" git "* ]]       && echo -e "    ${DIM}\$ brew install git${NC}"
    [[ " ${missing[*]} " == *" python3.12 "* ]] && echo -e "    ${DIM}\$ brew install python@3.12${NC}"
    echo ""
    echo -e "  ${UNDERLINE}Ubuntu / Debian:${NC}"
    [[ " ${missing[*]} " == *" git "* ]]       && echo -e "    ${DIM}\$ sudo apt update && sudo apt install -y git${NC}"
    [[ " ${missing[*]} " == *" python3.12 "* ]] && echo -e "    ${DIM}\$ sudo apt update && sudo apt install -y python3.12 python3.12-venv${NC}"
    echo ""
    exit 1
fi

# ─────────────────────────────────────────────
# 1. Clone repository
# ─────────────────────────────────────────────
section "📦 Step 1 — Repository"

if [ ! -d "medical-report-deidentifier" ] && [ ! -f "app.py" ]; then
    step 1 "Cloning repository..."
    spin "Downloading project..." git clone --quiet https://github.com/litescale-ai/medical-report-deidentifier.git
    cd medical-report-deidentifier
    success "Repository cloned"
elif [ -d "medical-report-deidentifier" ]; then
    cd medical-report-deidentifier
    step 1 "Updating repository..."
    spin "Pulling latest changes..." git pull --quiet
    success "Repository updated"
else
    step 1 "Updating repository..."
    spin "Pulling latest changes..." git pull --quiet
    success "Repository updated"
fi

# ─────────────────────────────────────────────
# 2. Python environment
# ─────────────────────────────────────────────
section "🐍 Step 2 — Python Environment"

if [ ! -d ".venv" ]; then
    step 2 "Creating virtual environment..."
    spin "Setting up Python 3.12 venv..." python3.12 -m venv .venv
    success "Virtual environment created"
else
    success "Virtual environment exists"
fi

source .venv/bin/activate
success "Activated .venv"

# ─────────────────────────────────────────────
# 3. Install dependencies
# ─────────────────────────────────────────────
section "📚 Step 3 — Dependencies"

step 3 "Installing Python packages..."
spin "Upgrading pip..." pip install -q --upgrade pip
spin "Installing requirements..." pip install -q -r requirements.txt
success "All dependencies installed"

# ─────────────────────────────────────────────
# 4. Choose backend
# ─────────────────────────────────────────────
section "⚙️  Step 4 — AI Backend Configuration"

info "How would you like to run the AI pipeline?"
echo ""

BACKEND_CHOICE=$(choose \
    "🌟  Gemini API (Cloud) — Requires an API key" \
    "🏠  Local Ollama (Gemma 4) — Runs on your machine" \
    "🧪  Mock / Dry-Run — Demo mode, no model needed"
)

# Preserve existing APP_DATA_DIR
ENV_FILE=".env"
APP_DATA_DIR_LINE=""
if [ -f "$ENV_FILE" ]; then
    APP_DATA_DIR_LINE=$(grep '^APP_DATA_DIR=' "$ENV_FILE" 2>/dev/null || true)
fi

divider

# --- Gemini API ---
if [[ "$BACKEND_CHOICE" == *"Gemini"* ]]; then
    echo ""
    info "Gemini API (Cloud) selected"
    echo ""

    # Check for existing key
    existing_key=""
    if [ -f "$ENV_FILE" ]; then
        existing_key=$(grep '^GEMINI_API_KEY=' "$ENV_FILE" 2>/dev/null | head -1 | sed 's/^GEMINI_API_KEY=//' | tr -d '"')
    fi
    [ -z "$existing_key" ] && existing_key="${GEMINI_API_KEY:-}"

    if [ -n "$existing_key" ]; then
        masked="${existing_key:0:4}····${existing_key: -4}"
        success "Existing API key found: $masked"
        if ! confirm "Use this key?"; then
            existing_key=""
        fi
    fi

    if [ -z "$existing_key" ]; then
        echo ""
        info "Get a free key at: ${UNDERLINE}https://aistudio.google.com/apikey${NC}"
        echo ""
        api_key=$(input_text "Paste your Gemini API key")
        if [ -z "$api_key" ]; then
            fail "No API key provided. Exiting."
            exit 1
        fi
    else
        api_key="$existing_key"
    fi

    cat > "$ENV_FILE" <<EOF
GEMINI_API_KEY="${api_key}"
AGENT_BACKEND="gemini"
EOF
    [ -n "$APP_DATA_DIR_LINE" ] && echo "$APP_DATA_DIR_LINE" >> "$ENV_FILE"
    success "Configuration saved to .env"

# --- Local Ollama ---
elif [[ "$BACKEND_CHOICE" == *"Ollama"* ]]; then
    echo ""
    info "Local Ollama (Gemma 4) selected"
    echo ""

    # Check / install Ollama
    if ! command -v ollama &>/dev/null; then
        warn "Ollama is not installed"
        if confirm "Install Ollama now?"; then
            if command -v brew &>/dev/null; then
                spin "Installing Ollama via Homebrew..." brew install ollama
            elif command -v curl &>/dev/null; then
                spin "Installing Ollama..." bash -c "curl -fsSL https://ollama.com/install.sh | sh"
            else
                fail "Cannot auto-install. Visit: https://ollama.com"
                exit 1
            fi
            if ! command -v ollama &>/dev/null; then
                fail "Installation failed. Visit: https://ollama.com"
                exit 1
            fi
            success "Ollama installed"
        else
            fail "Ollama is required for local mode"
            exit 1
        fi
    else
        success "Ollama is installed"
    fi

    # Version check — ensure Ollama is up-to-date and no stale server is running
    MIN_OLLAMA_VERSION="0.30.0"
    ollama_version_output=$(ollama --version 2>&1)
    ollama_server_ver=$(echo "$ollama_version_output" | grep -oE 'version is [0-9]+\.[0-9]+\.[0-9]+' | grep -oE '[0-9]+\.[0-9]+\.[0-9]+')
    ollama_client_ver=$(echo "$ollama_version_output" | grep -oE 'client version is [0-9]+\.[0-9]+\.[0-9]+' | grep -oE '[0-9]+\.[0-9]+\.[0-9]+')

    # Use client version if available, otherwise server version
    effective_ver="${ollama_client_ver:-$ollama_server_ver}"
    info "Ollama version: ${effective_ver:-unknown}"

    # If client is newer than server, a stale Ollama.app process may be running
    if [ -n "$ollama_client_ver" ] && [ -n "$ollama_server_ver" ] && [ "$ollama_client_ver" != "$ollama_server_ver" ]; then
        warn "Server ($ollama_server_ver) is older than client ($ollama_client_ver)"
        info "A stale Ollama.app process may be running — restarting..."
        pkill -9 -f "Ollama.app" 2>/dev/null || true
        pkill -9 -f "ollama serve" 2>/dev/null || true
        sleep 2
        ollama serve &>/dev/null &
        sleep 2
        success "Ollama server restarted ($(ollama --version 2>&1 | grep -oE 'version is [0-9.]+' | grep -oE '[0-9.]+'))"
    fi

    # Check if the binary itself is too old
    ver_to_compare="${ollama_client_ver:-$ollama_server_ver}"
    if [ -n "$ver_to_compare" ]; then
        # Simple numeric version comparison
        ver_major=$(echo "$ver_to_compare" | cut -d. -f1)
        ver_minor=$(echo "$ver_to_compare" | cut -d. -f2)
        min_major=$(echo "$MIN_OLLAMA_VERSION" | cut -d. -f1)
        min_minor=$(echo "$MIN_OLLAMA_VERSION" | cut -d. -f2)
        if [ "$ver_major" -lt "$min_major" ] || { [ "$ver_major" -eq "$min_major" ] && [ "$ver_minor" -lt "$min_minor" ]; }; then
            warn "Ollama $ver_to_compare is too old for Gemma 4 (need >= $MIN_OLLAMA_VERSION)"
            if command -v brew &>/dev/null && confirm "Upgrade Ollama via Homebrew?"; then
                spin "Upgrading Ollama..." brew upgrade ollama
                pkill -9 -f "ollama serve" 2>/dev/null || true
                sleep 1
                ollama serve &>/dev/null &
                sleep 2
                success "Ollama upgraded"
            else
                fail "Please update Ollama manually: https://ollama.com/download"
                exit 1
            fi
        fi
    fi

    # Choose model
    echo ""
    info "Select a Gemma 4 model variant:"
    echo ""

    MODEL_CHOICE=$(choose \
        "gemma4:e4b  — Sweet spot for laptops (~3 GB) ★ Recommended" \
        "gemma4:e2b  — Lightweight edge model (~1.5 GB)" \
        "gemma4:12b  — Mid-range workstation (~7 GB)" \
        "gemma4:26b  — MoE, 4B active params (~15 GB)"
    )
    OLLAMA_MODEL="${MODEL_CHOICE%%  *}"  # Extract model name before the double space

    divider
    success "Selected: $OLLAMA_MODEL"
    echo ""

    # Pull model if needed
    if ollama list 2>/dev/null | grep -q "${OLLAMA_MODEL%%:*}.*${OLLAMA_MODEL##*:}"; then
        success "Model $OLLAMA_MODEL is already available"
    else
        if confirm "Download $OLLAMA_MODEL now? (this may take a few minutes)"; then
            echo ""
            info "Pulling model — Ollama will show download progress:"
            echo ""
            ollama pull "$OLLAMA_MODEL"
            echo ""
            success "Model downloaded"
        else
            warn "Skipped. Run 'ollama pull $OLLAMA_MODEL' before using the app."
        fi
    fi

    cat > "$ENV_FILE" <<EOF
AGENT_BACKEND="ollama"
OLLAMA_MODEL="${OLLAMA_MODEL}"
OLLAMA_BASE_URL="http://localhost:11434/v1"
EOF
    [ -n "$APP_DATA_DIR_LINE" ] && echo "$APP_DATA_DIR_LINE" >> "$ENV_FILE"
    success "Configuration saved to .env"

# --- Mock ---
else
    echo ""
    info "Mock / Dry-Run mode — no model setup needed"

    cat > "$ENV_FILE" <<EOF
AGENT_BACKEND="mock"
EOF
    [ -n "$APP_DATA_DIR_LINE" ] && echo "$APP_DATA_DIR_LINE" >> "$ENV_FILE"
    success "Configuration saved to .env"
fi

# ─────────────────────────────────────────────
# 5. Launch
# ─────────────────────────────────────────────
echo ""
if $HAS_GUM; then
    gum style \
        --border rounded \
        --border-foreground 46 \
        --foreground 46 \
        --bold \
        --padding "1 3" \
        --align center \
        --width 72 \
        "🚀  Launching Guardian Medical De-identifier..." \
        "" \
        "The web application will open in your browser."
else
    echo -e "${BR_GREEN}┌──────────────────────────────────────────────────────────────────────┐${NC}"
    echo -e "${BR_GREEN}│${NC}                                                                    ${BR_GREEN}│${NC}"
    echo -e "${BR_GREEN}│${NC}    ${BOLD}🚀  Launching Guardian Medical De-identifier...${NC}                  ${BR_GREEN}│${NC}"
    echo -e "${BR_GREEN}│${NC}    ${DIM}The web application will open in your browser.${NC}                   ${BR_GREEN}│${NC}"
    echo -e "${BR_GREEN}│${NC}                                                                    ${BR_GREEN}│${NC}"
    echo -e "${BR_GREEN}└──────────────────────────────────────────────────────────────────────┘${NC}"
fi
echo ""

streamlit run app.py
