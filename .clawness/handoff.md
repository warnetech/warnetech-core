# Handoff — Package rename in progress

**Done this session:**
- Renamed `src/warnetech_core/` → `src/warnetech-core/`
- Updated pyproject.toml: script entry points, packages path (lines 35-36, 39)

**Still needed:**
1. Update all imports in codebase: `from warnetech_core` → `from warnetech-core` and `import warnetech_core` → `import warnetech-core`
2. Update test files similarly
3. Update CLI references in docs/README
4. Test: `python -m warnetech-core ask "test"`
5. Commit and push

**Files to update imports:**
- `src/warnetech-core/**/*.py` (every file with `warnetech_core` import)
- `tests/**/*.py`
- `core/engine.py`, scripts, examples
- `warnetech.sh`, `warnetech.bat`

**Uncommitted:** 
- Directory rename
- pyproject.toml updates

**Open questions:**
- Python version: 3.10+ or 3.8.10?
- App scope: what are you building? (for research task)

**Autostart:** no
