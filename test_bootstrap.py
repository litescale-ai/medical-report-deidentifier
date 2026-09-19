"""Exercise the real shell scripts with isolated tool shims; never install software."""

import os
import json
import tarfile
import zipfile
import errno
import fcntl
import pty
import select
import termios
import time
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent


class InstallAndLaunchTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="guardian setup ")
        self.root = Path(self.temp.name)
        self.checkout = self.root / "installed app"
        self.prototype = self.root / "prototype"
        self.prototype.mkdir()
        for name in ("run_app.sh", "requirements.txt"):
            shutil.copy(ROOT / name, self.prototype / name)
        (self.prototype / "app.py").write_text("# fixture\n")
        self.python = self.root / "fake-python"
        self.python.write_text('''#!/bin/bash
if [ "${1:-}" = -m ]; then
    echo "python $*" >> "$TEST_ROOT/calls"
    if [ "$2" = pip ]; then
        echo package-download-progress
        echo package-diagnostic >&2
        exit "${PIP_STATUS:-0}"
    fi
    if [ "$2" = streamlit ]; then echo app-started; exit 0; fi
fi
exec "$REAL_PYTHON" "$@"
''')
        self.python.chmod(0o755)
        self.ollama = self.root / "fake-ollama"
        self.ollama.write_text('''#!/bin/bash
echo serve >> "$TEST_ROOT/calls"
echo $$ > "$TEST_ROOT/server.pid"
if [ "${SCENARIO:-}" = crash ]; then echo startup-failed >&2; exit 17; fi
if [ "${SCENARIO:-}" != timeout ]; then touch "$TEST_ROOT/ready"; fi
exec /bin/sleep 60
''')
        self.ollama.chmod(0o755)
        self.shims = self.root / "shims.sh"
        self.shims.write_text('''
uname() { echo Darwin; }
command() {
    if [ "${1:-}" = -v ]; then
        case "${2:-}" in
            brew) if [ "${SCENARIO:-}" = fresh ] && [ ! -e "$TEST_ROOT/brew-ready" ]; then return 1; fi ;;
            python3.12|tesseract|ollama|git|gs)
                if [ "${SCENARIO:-}" = fresh ] && [ ! -e "$TEST_ROOT/tools-ready" ]; then return 1; fi ;;
        esac
    fi
    builtin command "$@"
}
brew() {
    echo "brew $*" >> "$TEST_ROOT/calls"
    if [ "$1" = upgrade ]; then
        if [ "${SCENARIO:-}" = upgrade-failed ]; then return 32; fi
        touch "$TEST_ROOT/upgraded"
    fi
    touch "$TEST_ROOT/tools-ready"
}
tesseract() { :; }
gs() { :; }
git() {
    echo "git $*" >> "$TEST_ROOT/calls"
    case "$1" in
        clone) mkdir -p "$3/.git"; cp "$TEST_ROOT/prototype/"* "$3/" ;;
        remote) echo https://github.com/litescale-ai/medical-report-deidentifier.git ;;
        branch) echo main ;;
        status) if [ "${SCENARIO:-}" = dirty ]; then echo ' M app.py'; fi ;;
        pull) return "${PULL_STATUS:-0}" ;;
    esac
}
python3.12() { mkdir -p "$3/bin"; cp "$TEST_ROOT/fake-python" "$3/bin/python"; }
curl() {
    case "$*" in
        *Homebrew*)
            echo 'touch "$TEST_ROOT/brew-ready"'
            if [ "${NEED_PROMPT:-}" = 1 ]; then
                echo 'read -rp "Mac setup confirmation: " answer; test "$answer" = continue'
            fi ;;
        *api/tags*)
            if [ "${SCENARIO:-}" = timeout ] && [ -e "$TEST_ROOT/server.pid" ]; then SECONDS=$((SECONDS + 31)); fi
            if [ -e "$TEST_ROOT/server.pid" ] && ! kill -0 "$(cat "$TEST_ROOT/server.pid")" 2>/dev/null; then return 7; fi
            test -e "$TEST_ROOT/ready" ;;
        *bootstrap.sh*)
            if [ "${SCENARIO:-}" = download-failed ]; then return 22; fi
            cp "$REAL_BOOTSTRAP" "${@: -1}" ;;
        *) return 99 ;;
    esac
}
ollama() {
    echo "ollama $*" >> "$TEST_ROOT/calls"
    case "$1" in
        show) test -e "$TEST_ROOT/model" ;;
        pull)
            test -e "$TEST_ROOT/ready" || return 77
            if [ "${SCENARIO:-}" = network-failed ]; then echo 'network unavailable' >&2; return 1; fi
            if [[ "${SCENARIO:-}" = old-* ]] || [ "${SCENARIO:-}" = upgrade-failed ]; then
                if [ ! -e "$TEST_ROOT/upgraded" ] || [ "${SCENARIO:-}" = old-still-incompatible ]; then
                    echo 'Error: pull model manifest: 412: The model requires a newer version of Ollama.' >&2
                    return 1
                fi
            fi
            touch "$TEST_ROOT/model" ;;
    esac
}
nohup() { exec "$TEST_ROOT/fake-ollama"; }
sleep() { /bin/sleep 0.02; }
lsof() { if [ "${SCENARIO:-}" = already-open ]; then echo 1234; else return 1; fi; }
ps() { echo "python -m streamlit run $GUARDIAN_INSTALL_DIR/app.py"; }
open() { echo "open $*" >> "$TEST_ROOT/calls"; }
osascript() { cat >/dev/null; echo "icon $*" >> "$TEST_ROOT/calls"; }
export -f uname command brew tesseract gs git python3.12 curl ollama nohup sleep lsof ps open osascript
''')
        self.env = {**os.environ, "TEST_ROOT": str(self.root), "BASH_ENV": str(self.shims),
                    "HOME": str(self.root / "home"), "TMPDIR": str(self.root),
                    "GUARDIAN_INSTALL_DIR": str(self.checkout), "REAL_BOOTSTRAP": str(ROOT / "bootstrap.sh"),
                    "REAL_PYTHON": sys.executable, "OLLAMA_MODEL": "gemma4:e4b"}

    def tearDown(self):
        pid_file = self.root / "server.pid"
        if pid_file.exists():
            try:
                os.kill(int(pid_file.read_text()), signal.SIGTERM)
            except ProcessLookupError:
                pass
        self.temp.cleanup()

    def run_script(self, name, **environment):
        path = ROOT / name if name != "run_app.sh" else self.checkout / name
        return subprocess.run(["/bin/bash", str(path)], cwd=self.root,
                              env={**self.env, **environment}, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=10)

    def prepare_existing(self):
        shutil.copytree(self.prototype, self.checkout)
        (self.checkout / ".git").mkdir()
        (self.checkout / ".venv/bin").mkdir(parents=True)
        shutil.copy(self.python, self.checkout / ".venv/bin/python")
        (self.checkout / ".env").write_text('GEMINI_API_KEY="keep-me"\nOLLAMA_MODEL="gemma4:e2b"\n')

    def calls(self):
        return (self.root / "calls").read_text() if (self.root / "calls").exists() else ""

    def test_fresh_install_from_public_wrapper_launches_and_creates_working_shortcut(self):
        result = self.run_script("install.sh", SCENARIO="fresh", OLLAMA_MODEL="")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("brew install", self.calls())
        self.assertIn("package-download-progress", result.stdout)
        self.assertIn("package-diagnostic", result.stderr)
        self.assertLess(self.calls().index("serve"), self.calls().index("ollama pull"))
        self.assertIn("app-started", result.stdout)
        shortcut = self.root / "home/Desktop/Guardian.command"
        self.assertTrue(os.access(shortcut, os.X_OK))
        self.assertIn("icon -l JavaScript", self.calls())
        self.assertIn("AGENT_BACKEND='ollama'", (self.checkout / ".env").read_text())
        self.assertIn("OLLAMA_MODEL='qwen3.5:2b'", (self.checkout / ".env").read_text())
        self.assertIn("ollama pull qwen3.5:2b", self.calls())
        (self.root / "calls").write_text("")
        launch = subprocess.run(["/bin/bash", str(shortcut)], env=self.env,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(launch.returncode, 0, launch.stderr)
        self.assertIn("app-started", launch.stdout)
        self.assertNotIn("pip", self.calls())
        self.assertNotIn("ollama pull", self.calls())
        self.assertNotIn("serve", self.calls().splitlines())

    def test_piped_install_keeps_password_and_confirmation_prompts_interactive(self):
        (self.root / "ready").touch()
        (self.root / "model").touch()
        master, terminal = pty.openpty()
        def controlling_terminal():
            os.setsid()
            fcntl.ioctl(0, termios.TIOCSCTTY, 0)
        process = subprocess.Popen(
            ["/bin/bash", "-c", 'cat "$REAL_INSTALL" | /bin/bash'],
            cwd=self.root, env={**self.env, "SCENARIO": "fresh", "NEED_PROMPT": "1",
                                "REAL_INSTALL": str(ROOT / "install.sh")},
            stdin=terminal, stdout=terminal, stderr=terminal, preexec_fn=controlling_terminal,
        )
        os.close(terminal)
        output, answered = b"", False
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if not select.select([master], [], [], 0.1)[0]:
                    continue
                try:
                    chunk = os.read(master, 8192)
                except OSError as error:
                    if error.errno != errno.EIO:
                        raise
                    break
                if not chunk:
                    break
                output += chunk
                if b"Mac setup confirmation:" in output and not answered:
                    os.write(master, b"continue\n")
                    answered = True
            self.assertTrue(answered, output.decode(errors="replace"))
            self.assertEqual(process.wait(timeout=1), 0, output.decode(errors="replace"))
            self.assertIn(b"app-started", output)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            os.close(master)

    def test_pdf_dependency_chooses_available_wheel_over_newer_source_release(self):
        # Offline package index: an older usable wheel and a newer source-only
        # release reproduce pip's choice on Intel Macs with older macOS.
        index = self.root / "package-index"
        index.mkdir()
        with zipfile.ZipFile(index / "pikepdf-1.0-py3-none-any.whl", "w") as wheel:
            wheel.writestr("pikepdf-1.0.dist-info/METADATA", "Metadata-Version: 2.1\nName: pikepdf\nVersion: 1.0\n")
            wheel.writestr("pikepdf-1.0.dist-info/WHEEL", "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
            wheel.writestr("pikepdf-1.0.dist-info/RECORD", "")
        source = self.root / "pikepdf-2.0"
        source.mkdir()
        (source / "pyproject.toml").write_text(
            '[build-system]\nrequires=[]\nbuild-backend="builder"\nbackend-path=["."]\n')
        (source / "builder.py").write_text(
            'def build_wheel(*args, **kwargs):\n    raise RuntimeError("Unexpected native source build")\n')
        with tarfile.open(index / "pikepdf-2.0.tar.gz", "w:gz") as archive:
            archive.add(source, arcname=source.name)
        # Apply the real requirements-file install policy to this minimal case.
        policy = self.root / "policy.txt"
        policy.write_text("\n".join(line for line in (ROOT / "requirements.txt").read_text().splitlines()
                                    if line.startswith("--")) + "\npikepdf\n")
        report = self.root / "resolved.json"
        result = subprocess.run(
            [self.env["REAL_PYTHON"], "-m", "pip", "install", "--dry-run", "--ignore-installed",
             "--no-index", "--no-deps", "--no-build-isolation", "--find-links", str(index),
             "--report", str(report), "-r", str(policy)],
            capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        installed = json.loads(report.read_text())["install"]
        self.assertEqual(installed[0]["metadata"]["version"], "1.0")
        self.assertTrue(installed[0]["download_info"]["url"].endswith(".whl"))

    def test_old_ollama_started_by_launcher_upgrades_restarts_and_retries_once(self):
        self.prepare_existing()
        result = self.run_script("run_app.sh", SCENARIO="old-owned")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = self.calls().splitlines()
        self.assertEqual(calls.count("brew upgrade ollama"), 1)
        self.assertEqual(calls.count("serve"), 2)
        self.assertEqual(calls.count("ollama pull gemma4:e4b"), 2)
        self.assertIn("app-started", result.stdout)

    def test_old_external_server_is_preserved_with_restart_instruction(self):
        self.prepare_existing()
        (self.root / "ready").touch()
        result = self.run_script("run_app.sh", SCENARIO="old-external")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("brew upgrade ollama", self.calls())
        self.assertIn("Restart your Mac", result.stdout + result.stderr)
        self.assertNotIn("serve", self.calls().splitlines())
        self.assertNotIn("streamlit", self.calls())

    def test_ollama_upgrade_is_bounded_and_other_errors_do_not_upgrade(self):
        self.prepare_existing()
        (self.root / "ready").touch()
        for scenario in ("old-still-incompatible", "network-failed", "upgrade-failed"):
            with self.subTest(scenario=scenario):
                (self.root / "calls").write_text("")
                result = self.run_script("run_app.sh", SCENARIO=scenario,
                                         GUARDIAN_OLLAMA_UPGRADE_ATTEMPTED="1" if scenario == "old-still-incompatible" else "")
                self.assertNotEqual(result.returncode, 0)
                if scenario != "upgrade-failed":
                    self.assertNotIn("brew upgrade", self.calls())
                self.assertNotIn("streamlit", self.calls())

    def test_update_preserves_settings_and_existing_model(self):
        self.prepare_existing()
        (self.root / "ready").touch()
        (self.root / "model").touch()
        result = self.run_script("bootstrap.sh", OLLAMA_MODEL="")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("git pull --ff-only origin main", self.calls())
        self.assertIn('GEMINI_API_KEY="keep-me"', (self.checkout / ".env").read_text())
        self.assertIn("ollama show gemma4:e2b", self.calls())
        self.assertNotIn("ollama pull", self.calls())

    def test_launcher_defaults_to_qwen_without_model_settings(self):
        self.prepare_existing()
        (self.checkout / ".env").write_text("")
        (self.root / "ready").touch()
        result = self.run_script("run_app.sh", OLLAMA_MODEL="")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("ollama pull qwen3.5:2b", self.calls())

    def test_package_failure_is_visible_and_does_not_launch(self):
        result = self.run_script("bootstrap.sh", PIP_STATUS="23")
        self.assertEqual(result.returncode, 23, result.stdout + result.stderr)
        self.assertIn("package-diagnostic", result.stderr)
        self.assertIn("Setup stopped", result.stderr)
        self.assertNotIn("streamlit", self.calls())

    def test_download_failure_stops_before_setup(self):
        result = self.run_script("install.sh", SCENARIO="download-failed")
        self.assertEqual(result.returncode, 22)
        self.assertFalse(self.checkout.exists())

    def test_dirty_checkout_is_not_updated(self):
        self.prepare_existing()
        result = self.run_script("bootstrap.sh", SCENARIO="dirty")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("git pull", self.calls())
        self.assertNotIn("pip", self.calls())

    def test_ollama_failures_stop_before_download_or_app(self):
        self.prepare_existing()
        for scenario in ("crash", "timeout"):
            with self.subTest(scenario=scenario):
                (self.root / "server.pid").unlink(missing_ok=True)
                result = self.run_script("run_app.sh", SCENARIO=scenario)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Ollama could not start", result.stderr)
                self.assertNotIn("ollama pull", self.calls())
                self.assertNotIn("streamlit", self.calls())
                with self.assertRaises(ProcessLookupError):
                    os.kill(int((self.root / "server.pid").read_text()), 0)

    def test_reopening_existing_app_does_not_start_another_server(self):
        self.prepare_existing()
        result = self.run_script("run_app.sh", SCENARIO="already-open")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("open http://localhost:8501", self.calls())
        self.assertNotIn("streamlit", self.calls())
        self.assertNotIn("serve", self.calls().splitlines())


if __name__ == "__main__":
    unittest.main()
