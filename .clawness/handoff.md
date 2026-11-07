# Handoff — Package rename + Python constraint

**Done:**
- Renamed `src/warnetech_core/` → `src/warnetech-core/`
- Updated pyproject.toml scripts & packages
- Committed (3978a89)

**BLOCKER:** User on Python 3.8.10. Warnetech-core requires 3.10+.
- Must upgrade Python to proceed

**Still needed:**
1. Update all imports: `warnetech_core` → `warnetech-core` (all .py files, tests, scripts)
2. Test: `python -m warnetech-core ask "test"`
3. Research task: clarify app scope (purpose, features, users)

**Open questions:**
- Will user upgrade Python to 3.10+?
- What app is being built? (for research)

**Autostart:** no
