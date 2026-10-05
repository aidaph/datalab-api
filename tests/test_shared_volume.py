from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from kubernetes import client
from kubernetes.client.rest import ApiException

from datalab_api.config import Settings
from datalab_api.services.volumes import describe_shared_volume

from .conftest import make_namespace


def shared_pvc(
    storage_class: str = "longhorn",
    volume: str | None = "pvc-123",
    phase: str = "Bound",
) -> client.V1PersistentVolumeClaim:
    return client.V1PersistentVolumeClaim(
        metadata=client.V1ObjectMeta(name="ids-data-shared"),
        spec=client.V1PersistentVolumeClaimSpec(
            storage_class_name=storage_class,
            volume_name=volume,
            resources=client.V1VolumeResourceRequirements(
                requests={"storage": "100Gi"}
            ),
        ),
        status=client.V1PersistentVolumeClaimStatus(
            phase=phase, capacity={"storage": "100Gi"} if volume else None
        ),
    )


def longhorn(state: str, robustness: str) -> dict[str, object]:
    return {
        "metadata": {"name": "pvc-123"},
        "status": {
            "state": state,
            "robustness": robustness,
            "actualSize": "1073741824",
        },
    }


@pytest.mark.parametrize(
    ("state", "robustness", "ready"),
    [
        ("attached", "healthy", True),
        ("attached", "degraded", True),
        ("detached", "unknown", True),
        ("attached", "faulted", False),
        ("attaching", "unknown", False),
    ],
)
def test_longhorn_volume_readiness(
    settings: Settings, kube: MagicMock, state: str, robustness: str, ready: bool
) -> None:
    kube.core.read_namespaced_persistent_volume_claim.side_effect = None
    kube.core.read_namespaced_persistent_volume_claim.side_effect = None
    kube.core.read_namespaced_persistent_volume_claim.return_value = shared_pvc()
    kube.custom.get_namespaced_custom_object.side_effect = None
    kube.custom.get_namespaced_custom_object.return_value = longhorn(state, robustness)

    volume = describe_shared_volume(kube, settings, "jupyterhub-ids", "ids")

    assert volume.on_longhorn and volume.created
    assert volume.ready is ready
    assert volume.size == "100Gi"
    assert volume.used_bytes == 1073741824
    assert volume.status == f"{state} · {robustness}"
    kube.custom.get_namespaced_custom_object.assert_called_with(
        "longhorn.io", "v1beta2", "longhorn-system", "volumes", "pvc-123"
    )


def test_volume_not_on_longhorn(settings: Settings, kube: MagicMock) -> None:
    kube.core.read_namespaced_persistent_volume_claim.side_effect = None
    kube.core.read_namespaced_persistent_volume_claim.return_value = shared_pvc(
        storage_class="cinder-csi"
    )
    kube.custom.get_namespaced_custom_object.side_effect = ApiException(status=404)

    volume = describe_shared_volume(kube, settings, "jupyterhub-ids", "ids")

    assert volume.created and not volume.on_longhorn
    assert volume.ready  # Bound
    assert volume.storage_class == "cinder-csi"
    assert volume.status == "Bound"


def test_volume_not_created_yet(settings: Settings, kube: MagicMock) -> None:
    kube.core.read_namespaced_persistent_volume_claim.side_effect = ApiException(
        status=404
    )
    volume = describe_shared_volume(kube, settings, "jupyterhub-ids", "ids")
    assert not volume.created and not volume.ready


def test_pending_volume_is_not_ready(settings: Settings, kube: MagicMock) -> None:
    kube.core.read_namespaced_persistent_volume_claim.side_effect = None
    kube.core.read_namespaced_persistent_volume_claim.return_value = shared_pvc(
        volume=None, phase="Pending"
    )
    volume = describe_shared_volume(kube, settings, "jupyterhub-ids", "ids")
    assert volume.created and not volume.ready and not volume.on_longhorn
    kube.custom.get_namespaced_custom_object.assert_not_called()


def test_environment_reports_shared_volume_only_when_it_has_one(
    app_client: TestClient, kube: MagicMock, token_for
) -> None:
    kube.core.list_namespace.return_value = client.V1NamespaceList(
        items=[make_namespace("jupyterhub-ids"), make_namespace("jupyterhub-dummy")]
    )
    kube.core.read_namespaced_persistent_volume_claim.side_effect = None
    kube.core.read_namespaced_persistent_volume_claim.side_effect = None
    kube.core.read_namespaced_persistent_volume_claim.return_value = shared_pvc()
    kube.custom.get_namespaced_custom_object.side_effect = None
    kube.custom.get_namespaced_custom_object.return_value = longhorn(
        "attached", "healthy"
    )

    kube.apps.read_namespaced_deployment_status.side_effect = ApiException(status=404)

    envs = {
        e["type"]: e for e in app_client.get("/deployments", headers=token_for()).json()
    }

    assert envs["ids"]["shared_volume"]["on_longhorn"] is True
    assert envs["ids"]["shared_volume"]["ready"] is True
    assert envs["dummy"]["shared_volume"] is None
