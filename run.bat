@echo off
setlocal EnableExtensions DisableDelayedExpansion
pushd "%~dp0" || exit /b 1
set "EXIT_CODE=1"
set "VENV_PYTHON=.venv\Scripts\python.exe"

node -e "process.exit(Number(process.versions.node.split('.')[0]) >= 22 ? 0 : 1)" >nul 2>&1
if errorlevel 1 (
    echo Node.js 22 or newer is required. Install it and run run.bat again.
    goto :finish
)

if exist "%VENV_PYTHON%" goto :check_venv

py -3 -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>&1
if not errorlevel 1 goto :create_with_launcher
python -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>&1
if errorlevel 1 (
    echo Python 3.11 or newer is required. Install it and run run.bat again.
    goto :finish
)
echo Creating .venv...
python -m venv .venv
if errorlevel 1 goto :finish
goto :check_venv

:create_with_launcher
echo Creating .venv...
py -3 -m venv .venv
if errorlevel 1 goto :finish

:check_venv
"%VENV_PYTHON%" -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>&1
if errorlevel 1 (
    echo The existing .venv is invalid. Rename it and run run.bat again.
    goto :finish
)
fc /b "pyproject.toml" ".venv\ai-office-deps.toml" >nul 2>&1
if not errorlevel 1 goto :launch

echo Installing Python dependencies...
"%VENV_PYTHON%" -m pip install -e .
if errorlevel 1 goto :finish
copy /y "pyproject.toml" ".venv\ai-office-deps.toml" >nul
if errorlevel 1 goto :finish

:launch
"%VENV_PYTHON%" -c "from pathlib import Path; source = Path('run.bat').read_text(encoding='utf-8'); exec(compile(source.split('\n# PYTHON_LAUNCHER\n', 1)[1], 'run.bat launcher', 'exec'))"
set "EXIT_CODE=%ERRORLEVEL%"

:finish
if not "%EXIT_CODE%"=="0" (
    echo AI Office stopped with an error. Check the message above.
    if not defined CI pause
)
popd
exit /b %EXIT_CODE%

# PYTHON_LAUNCHER
# The batch section exits before this code. Its Python command runs the launcher.
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
from urllib.request import urlopen
import webbrowser


HOST = "127.0.0.1"
LOCAL = Path(".ai-office")


def available_port(port):
    with socket.socket() as listener:
        try:
            listener.bind((HOST, port))
        except OSError as error:
            raise RuntimeError(f"Port {port} is unavailable. Stop the other server or choose another port.") from error


def wait_for_server(process, url, name):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"{name} exited before startup.")
        try:
            with urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.25)
    raise RuntimeError(f"{name} did not start within 30 seconds.")


def local_environment(backend_port):
    env = os.environ.copy()
    if "MCP_SERVERS_JSON" not in env:
        if not env.get("MCP_WORD_WORKSPACE"):
            workspace = LOCAL / "word"
            workspace.mkdir(parents=True, exist_ok=True)
            env["MCP_WORD_WORKSPACE"] = str(workspace.resolve())
        token = env.get("MCP_BRIDGE_TOKEN") or secrets.token_urlsafe(32)
        env["MCP_BRIDGE_TOKEN"] = token
        env["MCP_OFFICE_TOKEN"] = token
        env.setdefault("MCP_ALLOW_LOOPBACK", "true")
        env["MCP_SERVERS_JSON"] = json.dumps([{
            "id": "word",
            "name": "Word (local)",
            "url": f"http://{HOST}:{backend_port}/mcp/word",
            "tokenEnv": "MCP_OFFICE_TOKEN",
            "grants": {"general:0": {
                "word_list": "read",
                "word_read": "read",
                "word_create_draft": "write",
            }},
        }])
    return env


def main():
    processes = []
    try:
        backend_port = int(os.getenv("AI_OFFICE_BACKEND_PORT", "8000"))
        frontend_port = int(os.getenv("WORKFLOW_LAB_PORT", "8080"))
        if backend_port == frontend_port or not all(1 <= port <= 65535 for port in (backend_port, frontend_port)):
            raise ValueError("Choose two different ports in the range 1-65535.")
        available_port(backend_port)
        available_port(frontend_port)
        LOCAL.mkdir(exist_ok=True)
        env = local_environment(backend_port)
        backend_url = f"http://{HOST}:{backend_port}"
        frontend_url = f"http://{HOST}:{frontend_port}/workflow-lab/"
        with (LOCAL / "backend.log").open("w", encoding="utf-8") as log:
            backend = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "backend.app.main:app",
                 "--host", HOST, "--port", str(backend_port)],
                env=env, stdout=log, stderr=subprocess.STDOUT,
            )
        processes.append(backend)
        wait_for_server(backend, backend_url + "/health", "Backend")
        frontend = subprocess.Popen(
            ["node", "frontend/workflow-lab-server/local.mjs"], env=env,
        )
        processes.append(frontend)
        wait_for_server(frontend, frontend_url, "Frontend")
        print(f"AI Office ready: {frontend_url}", flush=True)
        print("Press Ctrl+C to stop both servers.", flush=True)
        if os.getenv("AI_OFFICE_NO_BROWSER") != "1":
            try:
                webbrowser.open(frontend_url)
            except webbrowser.Error:
                print("Open the URL above in your browser.", flush=True)
        while True:
            if backend.poll() is not None:
                raise RuntimeError("Backend stopped.")
            if frontend.poll() is not None:
                return frontend.returncode
            time.sleep(0.5)
    except KeyboardInterrupt:
        return 0
    except (OSError, RuntimeError, ValueError) as error:
        print(f"AI Office: {error}", file=sys.stderr)
        print("Backend log: .ai-office/backend.log", file=sys.stderr)
        return 1
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


sys.exit(main())
