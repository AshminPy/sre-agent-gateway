"""
Unit tests for tools/pods.py's describe_pod — specifically the volumes,
volume_mounts, owner_references, and current container state fields added
to close the custom-MCP tool-parity gap for config/secret-error incidents
(Phase 1 requirement: read-only tool coverage for missing ConfigMap/Secret
mounts). Uses lightweight SimpleNamespace fakes, same pattern as test_tools.py.
"""
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

from tools.pods import describe_pod


def _pod(volumes=None, volume_mounts=None, owner_references=None, container_state=None):
    return NS(
        metadata=NS(
            name="app-abc123",
            namespace="prod",
            labels={"app": "app"},
            annotations={},
            owner_references=owner_references or [],
        ),
        spec=NS(
            node_name="node-1",
            volumes=volumes or [],
            containers=[
                NS(
                    name="app",
                    image="app:1.0",
                    resources=None,
                    command=None,
                    args=None,
                    volume_mounts=volume_mounts or [],
                )
            ],
        ),
        status=NS(
            phase="Running",
            container_statuses=[
                NS(
                    name="app",
                    ready=True,
                    restart_count=0,
                    image="app:1.0",
                    state=container_state,
                    last_state=NS(terminated=None),
                )
            ],
        ),
    )


def test_describe_pod_includes_volumes_and_sources():
    v1 = MagicMock()
    v1.read_namespaced_pod.return_value = _pod(
        volumes=[
            NS(name="cfg", secret=None, config_map=NS(name="app-config"),
               persistent_volume_claim=None, empty_dir=None, host_path=None),
            NS(name="creds", secret=NS(secret_name="app-secret"), config_map=None,
               persistent_volume_claim=None, empty_dir=None, host_path=None),
        ]
    )
    result = describe_pod(v1, "prod", "app-abc123")
    assert {"name": "cfg", "source": "configMap:app-config"} in result["volumes"]
    assert {"name": "creds", "source": "secret:app-secret"} in result["volumes"]
    # secret VALUE must never appear anywhere in the payload
    assert "app-secret-value" not in str(result)


def test_describe_pod_includes_volume_mounts():
    v1 = MagicMock()
    v1.read_namespaced_pod.return_value = _pod(
        volume_mounts=[NS(name="cfg", mount_path="/etc/app", read_only=True)]
    )
    result = describe_pod(v1, "prod", "app-abc123")
    assert result["containers"][0]["volume_mounts"] == [
        {"name": "cfg", "mount_path": "/etc/app", "read_only": True}
    ]


def test_describe_pod_includes_owner_references():
    v1 = MagicMock()
    v1.read_namespaced_pod.return_value = _pod(
        owner_references=[NS(kind="ReplicaSet", name="app-abc123")]
    )
    result = describe_pod(v1, "prod", "app-abc123")
    assert result["owner_references"] == [{"kind": "ReplicaSet", "name": "app-abc123"}]


def test_describe_pod_current_state_waiting_reason_surfaces_configmap_error():
    v1 = MagicMock()
    v1.read_namespaced_pod.return_value = _pod(
        container_state=NS(
            waiting=NS(reason="CreateContainerConfigError",
                       message="configmap \"app-config\" not found"),
            running=None, terminated=None,
        )
    )
    result = describe_pod(v1, "prod", "app-abc123")
    cs = result["container_statuses"][0]
    assert cs["state"]["phase"] == "waiting"
    assert cs["state"]["reason"] == "CreateContainerConfigError"
    assert "app-config" in cs["state"]["message"]
