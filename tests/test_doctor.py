"""`warnetech doctor`: every branch is driven with injected inputs, not this machine."""

import json
import os

import pytest

from warnetech_core.cli import main as cli_main
from warnetech_core.runtime.doctor import (
    LOW_DISK_BYTES,
    Check,
    detect_container,
    exit_code,
    format_text,
    redact_proxy,
    run_checks,
    to_dicts,
)

ALL_PRESENT = lambda name: object()  # noqa: E731 - every module "exists"
NONE_PRESENT = lambda name: None  # noqa: E731


def run(env=None, **kw):
    kw.setdefault("find_spec", ALL_PRESENT)
    kw.setdefault("exists", lambda p: True)
    kw.setdefault("read_text", lambda p: "")
    kw.setdefault("access", lambda p, mode: True)
    kw.setdefault("disk_free", lambda p: 50 * 1024**3)
    return {c.name: c for c in run_checks(env=env or {}, **kw)}


# ---- platform -----------------------------------------------------------

def test_supported_python_is_ok():
    assert run(python_version=(3, 10))["python"].status == "ok"


def test_python_below_the_minimum_fails():
    check = run(python_version=(3, 9))["python"]
    assert check.status == "fail" and "3.10" in check.detail


@pytest.mark.parametrize("env,exists,cgroup,expected", [
    ({"KUBERNETES_SERVICE_HOST": "10.0.0.1"}, False, "", "kubernetes"),
    ({}, True, "", "docker"),
    ({}, False, "12:cpu:/kubepods/burstable/pod1", "kubepods"),
    ({}, False, "0::/system.slice/containerd.service", "containerd"),
    ({}, False, "0::/user.slice", None),
])
def test_container_detection(env, exists, cgroup, expected):
    assert detect_container(env, lambda p: exists, lambda p: cgroup) == expected


def test_missing_cgroup_file_means_no_container():
    def boom(path):
        raise FileNotFoundError(path)

    assert detect_container({}, lambda p: False, boom) is None


# ---- dependencies -------------------------------------------------------

def test_missing_required_packages_fail():
    check = run(find_spec=NONE_PRESENT)["dependencies"]
    assert check.status == "fail" and "httpx" in check.detail and "pyyaml" in check.detail


def test_optional_extras_are_reported_but_never_fail():
    only = lambda name: object() if name == "fastapi" else None  # noqa: E731
    wanted = {"httpx", "pydantic", "yaml", "fastapi"}
    checks = run(find_spec=lambda n: object() if n in wanted else None)
    assert checks["dependencies"].status == "ok"
    assert "fastapi" in checks["extras"].detail and "torch" not in checks["extras"].detail
    assert only("fastapi") and only("x") is None


def test_transformers_on_python_310_is_flagged():
    assert run(python_version=(3, 10))["hf"].status == "warn"
    assert "hf" not in run(python_version=(3, 12))


# ---- providers and mode -------------------------------------------------

def test_providers_come_from_the_real_registration_path(monkeypatch):
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "NVIDIA_API_KEY", "OLLAMA_HOST"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "not-a-real-key-for-tests")
    check = run({"OPENAI_API_KEY": "not-a-real-key-for-tests"})["providers"]
    assert "openai" in check.detail and "echo" in check.detail


def test_api_keys_never_appear_in_the_report(monkeypatch):
    secret = "sk-test-do-not-print-this-value"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    report = format_text(run_checks(env={"OPENAI_API_KEY": secret}))
    assert secret not in report


def test_local_only_notes_that_hosted_keys_are_ignored(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "not-a-real-key-for-tests")
    monkeypatch.setenv("WARNETECH_LOCAL_ONLY", "1")
    checks = run({"OPENAI_API_KEY": "x", "WARNETECH_LOCAL_ONLY": "1"})
    assert "openai" not in checks["providers"].detail
    assert "ignored" in checks["local-only"].detail


def test_a_bad_node_setting_is_reported_as_a_failure(monkeypatch):
    monkeypatch.setenv("OLLAMA_NODES", "not valid")
    check = run({"OLLAMA_NODES": "not valid"})["providers"]
    assert check.status == "fail" and "OLLAMA_NODES" in check.detail


def test_mode_line_reflects_the_flags():
    detail = run({"WARNETECH_OFFLINE": "1"})["mode"].detail
    assert detail == "offline=on, local-only=off"


# ---- network configuration ---------------------------------------------

@pytest.mark.parametrize("raw,shown", [
    ("http://user:secret@proxy.example:3128", "http://proxy.example:3128"),
    ("https://proxy.example", "https://proxy.example"),
    ("user:secret@proxy.example:3128", "proxy.example:3128"),
    ("proxy.example:3128", "proxy.example:3128"),
])
def test_proxy_credentials_are_never_shown(raw, shown):
    assert redact_proxy(raw) == shown


def test_proxy_in_the_report_has_no_credentials():
    checks = run({"HTTPS_PROXY": "http://user:secret@proxy.example:3128"})
    detail = checks["proxy HTTPS_PROXY"].detail
    assert "proxy.example:3128" in detail and "secret" not in detail and "user" not in detail


def test_ca_bundle_that_exists_is_ok_and_one_that_does_not_warns():
    ok = run({"SSL_CERT_FILE": "/etc/ca.pem"}, exists=lambda p: True)["ca SSL_CERT_FILE"]
    bad = run({"SSL_CERT_FILE": "/etc/ca.pem"}, exists=lambda p: False)["ca SSL_CERT_FILE"]
    assert ok.status == "ok" and bad.status == "warn" and "does not exist" in bad.detail


# ---- paths and disk -----------------------------------------------------

def test_unwritable_data_directory_warns():
    checks = run(access=lambda p, mode: False)
    assert checks["data memory"].status == "warn" and "not writable" in checks["data memory"].detail


def test_disk_space_boundary():
    at_limit = run(disk_free=lambda p: LOW_DISK_BYTES)["data memory"]
    below = run(disk_free=lambda p: LOW_DISK_BYTES - 1)["data memory"]
    assert at_limit.status == "ok" and below.status == "warn"


def test_a_configured_file_that_is_missing_warns():
    missing = "/nope/routing.yaml"
    checks = run({"WARNETECH_ROUTING_FILE": missing}, exists=lambda p: p != missing)
    assert checks["warnetech_routing_file"].status == "warn"


# ---- local hosts --------------------------------------------------------

def test_network_check_is_skipped_unless_asked():
    assert not any(name.startswith("host ") for name in run({"OLLAMA_HOST": "http://h:1"}))


def test_network_check_with_no_hosts_says_so():
    assert run(network=True)["ollama hosts"].detail.startswith("none configured")


def test_network_check_reports_each_host(monkeypatch):
    import warnetech_core.runtime.nodes as nodes

    async def fake(hosts, **kw):
        return [
            nodes.NodeStatus("ollama", "u", True, models=("a", "b")),
            nodes.NodeStatus("ollama-gpu", "u", False, error="ConnectError"),
        ]

    monkeypatch.setattr(nodes, "check_nodes", fake)
    checks = run({"OLLAMA_HOST": "http://h:1", "OLLAMA_NODES": "gpu=http://g:1"}, network=True)
    assert checks["host ollama"].status == "ok" and "2 models" in checks["host ollama"].detail
    assert checks["host ollama-gpu"].status == "fail"


# ---- output -------------------------------------------------------------

def test_exit_code_is_nonzero_only_for_failures():
    assert exit_code([Check("a", "ok", ""), Check("b", "warn", ""), Check("c", "info", "")]) == 0
    assert exit_code([Check("a", "ok", ""), Check("b", "fail", "")]) == 1


def test_text_output_aligns_one_line_per_check():
    lines = format_text([Check("a", "ok", "x"), Check("longer", "warn", "y")]).splitlines()
    assert lines == ["ok    a       x", "warn  longer  y"]


def test_json_output_is_a_list_of_objects():
    assert to_dicts([Check("a", "ok", "x")]) == [{"name": "a", "status": "ok", "detail": "x"}]


def test_the_command_prints_valid_json(capsys, monkeypatch):
    monkeypatch.setenv("WARNETECH_OFFLINE", "1")
    code = cli_main(["doctor", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert code in (0, 1) and {"python", "providers", "resources"} <= {c["name"] for c in data}


def test_the_command_text_output_has_a_python_line(capsys, monkeypatch):
    monkeypatch.setenv("WARNETECH_OFFLINE", "1")
    cli_main(["doctor"])
    assert any(line.startswith("ok    python") for line in capsys.readouterr().out.splitlines())
    assert os.environ.get("WARNETECH_OFFLINE") == "1"


# ---- Android / 32-bit -------------------------------------------------------------


from warnetech_core.runtime.doctor import detect_android  # noqa: E402


@pytest.mark.parametrize(
    "env,platform_name,expected",
    [
        ({"TERMUX_VERSION": "0.118"}, "linux", "termux"),
        ({"ANDROID_ROOT": "/system", "ANDROID_DATA": "/data"}, "linux", "android"),
        ({}, "android", "android"),
        ({"ANDROID_ROOT": "/system"}, "linux", None),  # one variable alone is not enough
        ({}, "linux", None),
    ],
)
def test_detect_android(env, platform_name, expected):
    assert detect_android(env, platform_name) == expected


def test_termux_arm64_is_reported_without_warning():
    env = {"TERMUX_VERSION": "0.118"}
    checks = run(env, machine="aarch64", pointer_bits=64, sys_platform="linux")
    assert checks["android"].status == "info"
    assert "termux" in checks["android"].detail and "64-bit" in checks["android"].detail
    assert "python bits" not in checks


def test_32_bit_python_warns_everywhere():
    checks = run({}, machine="armv7l", pointer_bits=32, sys_platform="linux")
    assert checks["python bits"].status == "warn"
    assert "android" not in checks


def test_32_bit_python_on_termux_points_at_64_bit_devices():
    checks = run({"TERMUX_VERSION": "1"}, machine="armv7l", pointer_bits=32, sys_platform="linux")
    assert checks["python bits"].status == "warn"
    assert "Termux" in checks["python bits"].detail


def test_android_on_an_unexpected_architecture_warns():
    checks = run({"TERMUX_VERSION": "1"}, machine="i686", pointer_bits=64, sys_platform="linux")
    assert checks["android"].status == "warn"


def test_plain_linux_has_no_android_check():
    checks = run({}, machine="x86_64", pointer_bits=64, sys_platform="linux")
    assert "android" not in checks and "python bits" not in checks
