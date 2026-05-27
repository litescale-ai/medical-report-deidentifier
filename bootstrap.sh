#!/bin/bash
# Guardian Medical De-identifier - Automated Installer & Bootstrapper

echo "================================================================================"
echo "🛡️  STARTING GUARDIAN MEDICAL DE-IDENTIFIER INSTALLER & LAUNCHER"
echo "================================================================================"

# 1. Clone the repository if we are not already in it and the folder doesn't exist
if [ ! -d "medical-report-deidentifier" ] && [ ! -f "app.py" ]; then
    echo "[Step 1] Downloading project repository..."
    git clone https://github.com/litescale-ai/medical-report-deidentifier.git
    cd medical-report-deidentifier
elif [ -d "medical-report-deidentifier" ]; then
    cd medical-report-deidentifier
fi

# 2. Setup the Python 3.12 virtual environment
echo "[Step 2] Setting up Python environment..."
if [ ! -d ".venv" ]; then
    python3.12 -m venv .venv
fi

# 3. Activate virtual environment
source .venv/bin/activate

# 4. Install dependencies
echo "[Step 3] Verifying and installing dependencies..."
pip install -q --upgrade pip
pip install -q -r requirements.txt

# 5. Launch the Web UI
echo "[Step 4] Launching the Web Application..."
streamlit run app.py
