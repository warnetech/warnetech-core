---
title: "Finish warnetech-core: PRoot daemon & CLI (phases 2–5)"
status: planning
size: Large
created: 2026-11-20
---

# Finish warnetech-core: Phases 2–5

## 요구사항

**Scope** [confirmed]: Implement remaining 4 phases sequentially to production-ready state.
- **Phase 2**: Userspace daemon supervisor (daemon.py, process_supervisor.py, logger.py)
- **Phase 3**: CLI command wiring (daemon_commands.py, automation_commands.py, init)
- **Phase 4**: proot-distro bootstrap script + warnetech-llm launcher
- **Phase 5**: Integration tests for daemon lifecycle + CLI

**Success criteria** [confirmed]:
- All 950+ existing tests still pass
- 50+ new tests for daemon/CLI lifecycle
- Zero regressions in existing modules
- `pytest -q` → ~1000 passed, 2 skipped

**Timeline** [confirmed]: Complete before Monday 2026-11-24 evening (Windows validation on hold)

---

## 결정

No P0 ambiguities. Phases 2–5 are fully specified from previous session's plan. Proceeding to implementation planning.

---

## 구현 계획

### Phase 2: Daemon Supervisor (days 1–2)
**Files**: src/warnetech_core/runtime/daemon.py, process_supervisor.py, logger.py
**Work**: Async daemon lifecycle, PID lockfiles, signal handling, rotating logs
**Gate**: `pytest tests/test_daemon_cli.py -q` → all daemon tests pass

### Phase 3: CLI Wiring (day 2–3)
**Files**: src/warnetech_core/daemon_commands.py, automation_commands.py, commands/init.py
**Work**: `warnetech daemon start|stop|status|logs`, `warnetech init --interactive/--non-interactive`
**Gate**: `warnetech init --non-interactive && warnetech daemon start && warnetech daemon status | grep running`

### Phase 4: Bootstrap (day 3–4)
**Files**: scripts/proot_debian_setup.sh, scripts/warnetech-llm
**Work**: Idempotent proot Debian setup, unified entry point
**Gate**: `bash scripts/proot_debian_setup.sh && warnetech doctor`

### Phase 5: Verification (day 4)
**Files**: tests/test_daemon_proot_integration.py
**Work**: Integration tests, edge cases, crash recovery
**Gate**: `pytest tests/ -q` → 1000+ passed, 0 failed, 2 skipped

---

## 완료 조건

**Phase 2 Complete**:
```bash
pytest tests/test_daemon_cli.py -q
# exit 0 ✓
# Output: N passed, 0 failed
```

**Phase 3 Complete**:
```bash
warnetech init --non-interactive
warnetech daemon start && sleep 1 && warnetech daemon status | grep -q running
# exit 0 ✓
```

**Phase 4 Complete**:
```bash
bash scripts/proot_debian_setup.sh && warnetech doctor
# exit 0 ✓
```

**All Phases Complete**:
```bash
pytest tests/ -q
# exit 0 ✓
# 1000+ passed, 0 failed, 2 skipped
```

---

## 차기

Ready for auto-dev execution of Phase 2.
