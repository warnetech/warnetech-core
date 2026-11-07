# Handoff — Python package naming & user research

**Done:**
- Verified warnetech-core is ready as CLI (PowerShell/CMD/bash)
- Created `warnetech.bat` for Windows CMD support
- Installed package with dev deps, all tests pass offline

**Current state:**
- Package works but has naming inconsistency: PyPI name is `warnetech-core` (hyphen) but Python module is `warnetech_core` (underscore). All imports use underscore.
- User wants module name to be `warnetech-core` (no underscore anywhere).
- User environment: Python 3.8.10 (repo targets 3.10+; needs confirmation whether downgrade is feasible).
- User preferences: research first, then compile PRD for phased app build (but hasn't clarified what app yet).

**Next:**
1. Ask user: can they upgrade to Python 3.10+, or locked to 3.8.10?
2. Rename package: `src/warnetech_core/` → `src/warnetech-core/`, update all imports, update pyproject.toml, update CLI references. This is a breaking change.
3. Research task: clarify what app user wants to build (core purpose, key features, target user) before researching existing solutions.

**Uncommitted:** None.

**Open questions:** 
- Python version constraint: 3.8.10 or can upgrade?
- App scope: what are you building? (needed before research can start)

**Autostart:** no
