#!/usr/bin/env bash
set -euo pipefail

echo "[*] Creating repository directory tree..."
mkdir -p agents config core memory protocols tests .github/workflows data/rag_memory models

echo "[*] Creating package initialization files..."
touch agents/__init__.py core/__init__.py memory/__init__.py protocols/__init__.py tests/__init__.py

echo "[*] Creating .gitignore..."
cat << 'EOF' > .gitignore
__pycache__/
*.py[cod]
*.so
.Python
build/
dist/
venv/
.venv/
.pytest_cache/
data/
models/
*.db
.DS_Store
EOF

echo "[*] Creating pytest.ini..."
cat << 'EOF' > pytest.ini
[pytest]
asyncio_mode = auto
testpaths = tests
python_files = test_*.py
EOF

echo "[*] Repository structure and boilerplate files created successfully."
