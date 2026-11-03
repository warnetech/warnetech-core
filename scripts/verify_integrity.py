import sys
from pathlib import Path

REQUIRED_DIRS = ["agents", "config", "core", "memory", "protocols", "tests"]
REQUIRED_FILES = [
    "main.py",
    "requirements.txt",
    "pytest.ini",
    "Dockerfile",
    "docker-compose.yml",
    "config/manifest.yaml",
    "core/temporal_engine.py",
    "core/provider_router.py",
    "core/context_window.py",
    "memory/rag_store.py",
    "agents/orchestrator.py",
    "protocols/mcp_server.py"
]

def verify():
    root = Path(__file__).parent
    errors = 0

    print("[*] Verifying directory structure...")
    for d in REQUIRED_DIRS:
        path = root / d
        if path.is_dir():
            print(f"  [OK] Directory found: {d}")
        else:
            print(f"  [FAIL] Missing directory: {d}")
            errors += 1

    print("\n[*] Verifying required files...")
    for f in REQUIRED_FILES:
        path = root / f
        if path.is_file():
            print(f"  [OK] File found: {f}")
        else:
            print(f"  [FAIL] Missing file: {f}")
            errors += 1

    print("\n[*] Verifying module imports...")
    sys.path.insert(0, str(root))
    
    modules = [
        "core.temporal_engine",
        "core.provider_router",
        "core.context_window",
        "memory.rag_store",
        "agents.orchestrator",
        "protocols.mcp_server"
    ]

    for mod in modules:
        try:
            __import__(mod)
            print(f"  [OK] Successfully imported: {mod}")
        except Exception as e:
            print(f"  [FAIL] Failed to import {mod}: {e}")
            errors += 1

    if errors > 0:
        print(f"\n[!] Integrity check FAILED with {errors} error(s).")
        sys.exit(1)
    else:
        print("\n[*] Integrity check PASSED successfully.")

if __name__ == "__main__":
    verify()
