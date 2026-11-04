"""Structural checks on deploy/kubernetes. Not validated against a live API server."""

from pathlib import Path

import pytest
import yaml

DIR = Path(__file__).resolve().parent.parent / "deploy" / "kubernetes"


def load(name):
    return yaml.safe_load((DIR / name).read_text(encoding="utf-8"))


def test_every_manifest_parses_with_kind():
    for f in DIR.glob("*.yaml"):
        doc = yaml.safe_load(f.read_text(encoding="utf-8"))
        assert doc.get("kind"), f.name


def test_kustomization_lists_existing_files_and_not_the_secret():
    res = load("kustomization.yaml")["resources"]
    assert all((DIR / r).is_file() for r in res)
    assert "secret.example.yaml" not in res


def test_deployment_is_hardened():
    pod = load("deployment.yaml")["spec"]["template"]["spec"]
    c = pod["containers"][0]
    assert pod["securityContext"]["runAsNonRoot"] is True
    assert pod["securityContext"]["seccompProfile"]["type"] == "RuntimeDefault"
    assert pod["automountServiceAccountToken"] is False
    sc = c["securityContext"]
    assert sc["allowPrivilegeEscalation"] is False
    assert sc["readOnlyRootFilesystem"] is True
    assert sc["capabilities"]["drop"] == ["ALL"]
    mounts = {m["mountPath"] for m in c["volumeMounts"]}
    assert {"/data", "/tmp"} <= mounts  # writable paths exist despite read-only root
    assert c["readinessProbe"]["httpGet"]["path"] == "/health"
    assert "limits" in c["resources"]


def test_service_targets_the_deployment_port():
    dep = load("deployment.yaml")
    svc = load("service.yaml")
    sel = dep["spec"]["selector"]["matchLabels"]
    assert svc["spec"]["selector"] == sel
    assert dep["spec"]["template"]["spec"]["containers"][0]["ports"][0]["containerPort"] == 8080


def test_example_secret_holds_only_a_placeholder():
    data = load("secret.example.yaml")["stringData"]
    assert set(data.values()) == {"REPLACE_ME"}


@pytest.mark.parametrize("name", ["deployment.yaml", "configmap.yaml", "service.yaml", "pvc.yaml"])
def test_no_inline_credentials(name):
    text = (DIR / name).read_text(encoding="utf-8").lower()
    for bad in ("api_key", "password", "sk-"):
        assert bad not in text
