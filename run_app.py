import os
import sys
import streamlit.web.cli as stcli

def resolve_path(path):
    """Resolve absolute path, accounting for PyInstaller's _MEIPASS."""
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, path)
    return os.path.join(os.path.abspath(os.path.dirname(__file__)), path)

if __name__ == "__main__":
    # Point to the actual Streamlit app
    app_path = resolve_path("app.py")
    
    # We run in headless mode because Tauri will provide the window
    sys.argv = [
        "streamlit",
        "run",
        app_path,
        "--server.headless=true",
        "--server.port=8501",
        "--global.developmentMode=false"
    ]
    
    sys.exit(stcli.main())
