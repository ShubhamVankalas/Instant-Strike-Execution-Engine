import sys
import os
import subprocess
import threading
import time
import argparse

# ANSI color codes for premium console output logging
class Colors:
    BACKEND = "\033[94m"   # Blue
    WORKER = "\033[95m"    # Magenta
    FRONTEND = "\033[92m"  # Green
    MCP = "\033[96m"       # Cyan
    INFO = "\033[93m"      # Yellow
    WARNING = "\033[33m"   # Orange
    ERROR = "\033[91m"     # Red
    RESET = "\033[0m"

# Map to store active subprocesses
processes = {}

def read_output(name, process, color):
    """
    Reads the standard output/error from a subprocess in a non-blocking thread,
    prefixes it with the service name and color, and prints it.
    """
    try:
        for line in iter(process.stdout.readline, ""):
            if line:
                # Print prefixed line
                print(f"{color}[{name}]{Colors.RESET} {line.rstrip()}")
            else:
                break
    except Exception as e:
        print(f"{Colors.ERROR}[Runner Error] Error reading output from {name}: {e}{Colors.RESET}")
    finally:
        process.stdout.close()

def spawn_process(name, cmd, color, env):
    """
    Spawns a subprocess, sets up output streaming threads, and records the process.
    """
    print(f"{Colors.INFO}[Runner] Starting {name} with command: {' '.join(cmd)}{Colors.RESET}")
    
    # Run process. Combine stdout and stderr, set text=True (str), set unbuffered/bufsize=1
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env
    )
    processes[name] = proc

    # Start reader thread
    t = threading.Thread(target=read_output, args=(name, proc, color), daemon=True)
    t.start()
    return proc

def shutdown_all():
    """
    Gracefully terminates all spawned processes.
    """
    print(f"\n{Colors.INFO}[Runner] Initiating shutdown sequence...{Colors.RESET}")
    
    # First, request all processes to terminate
    for name, proc in list(processes.items()):
        if proc.poll() is None:
            print(f"{Colors.INFO}[Runner] Sending termination signal to {name}...{Colors.RESET}")
            proc.terminate()
            
    # Give them a few seconds to clean up, then force kill if necessary
    wait_seconds = 5
    start_time = time.time()
    while time.time() - start_time < wait_seconds:
        alive = [name for name, proc in processes.items() if proc.poll() is None]
        if not alive:
            break
        time.sleep(0.5)

    # Force kill any remaining processes
    for name, proc in processes.items():
        if proc.poll() is None:
            print(f"{Colors.WARNING}[Runner] {name} did not exit in time. Force killing...{Colors.RESET}")
            proc.kill()
            proc.wait()
        else:
            print(f"{Colors.INFO}[Runner] {name} exited with status {proc.returncode}{Colors.RESET}")
            
    print(f"{Colors.INFO}[Runner] All processes stopped.{Colors.RESET}")

def main():
    # Force ANSI escape sequences on Windows Command Prompt if needed
    if os.name == "nt":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
        except Exception:
            pass

    # Command line argument parser
    parser = argparse.ArgumentParser(
        description="Unified launcher for Instant Strike Execution Engine services."
    )
    parser.add_argument("--no-backend", action="store_true", help="Do not start the FastAPI backend server")
    parser.add_argument("--no-worker", action="store_true", help="Do not start the Celery worker process")
    parser.add_argument("--no-frontend", action="store_true", help="Do not start the Streamlit frontend dashboard")
    parser.add_argument("--no-mcp", action="store_true", help="Do not start the FastMCP Trade Intelligence server")
    args = parser.parse_args()

    # Unbuffered python environment setup to prevent output caching in pipes
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    print(f"{Colors.INFO}===================================================================={Colors.RESET}")
    print(f"{Colors.INFO}⚡ Starting Instant Strike Execution Engine Multi-Runner... ⚡{Colors.RESET}")
    print(f"{Colors.INFO}===================================================================={Colors.RESET}")
    print(f"{Colors.INFO}[Runner] Press Ctrl+C to shut down all processes simultaneously.{Colors.RESET}\n")

    try:
        # 1. Start Celery Worker
        if not args.no_worker:
            cmd = [
                sys.executable, "-m", "celery",
                "-A", "tasks.celery_app",
                "worker",
                "--loglevel=info",
                "-P", "solo"
            ]
            spawn_process("Worker", cmd, Colors.WORKER, env)

        # 2. Start FastAPI Backend Server
        if not args.no_backend:
            cmd = [sys.executable, "main.py"]
            spawn_process("Backend", cmd, Colors.BACKEND, env)

        # 3. Start FastMCP Trade Intelligence Server
        if not args.no_mcp:
            cmd = [sys.executable, "main.py", "mcp"]
            spawn_process("MCP", cmd, Colors.MCP, env)

        # 4. Start Streamlit Frontend Dashboard
        if not args.no_frontend:
            cmd = [
                sys.executable, "-m",
                "streamlit", "run",
                "frontend/app.py",
                "--server.port=8501"
            ]
            spawn_process("Frontend", cmd, Colors.FRONTEND, env)

        # Monitor loop to keep main thread alive and check process health
        while True:
            time.sleep(1)
            # Check if any started process has crashed
            for name, proc in list(processes.items()):
                ret = proc.poll()
                if ret is not None:
                    print(f"{Colors.ERROR}[Runner Alert] {name} process exited unexpectedly with code {ret}!{Colors.RESET}")
                    # Remove it from active checks so we only report it once
                    del processes[name]

    except KeyboardInterrupt:
        print(f"\n{Colors.WARNING}[Runner] KeyboardInterrupt detected. Stopping...{Colors.RESET}")
    finally:
        shutdown_all()

if __name__ == "__main__":
    main()
