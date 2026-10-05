"""State of an environment's shared data volume, checked against Longhorn."""

import logging
from typing import Any

from kubernetes.client.rest import ApiException

from ..config import Settings
from ..k8s import KubeClient
from ..schemas import SharedVolume

log = logging.getLogger(__name__)

LONGHORN_GROUP = "longhorn.io"
LONGHORN_VERSION = "v1beta2"


def shared_volume_name(deployment_type: str) -> str:
    return f"{deployment_type}-data-shared"


def _longhorn_volume(
    kube: KubeClient, settings: Settings, name: str
) -> dict[str, Any] | None:
    """The Longhorn Volume behind a PV (same name), or None if it is not Longhorn."""
    try:
        volume: dict[str, Any] = kube.custom.get_namespaced_custom_object(
            LONGHORN_GROUP,
            LONGHORN_VERSION,
            settings.longhorn_namespace,
            "volumes",
            name,
        )
    except ApiException as exc:
        # 404: not a Longhorn volume (or Longhorn not installed); 403: no access.
        if exc.status not in (403, 404):
            raise
        return None
    return volume


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _longhorn_ready(state: str | None, robustness: str | None) -> bool:
    """Longhorn's "ready for workload": attached and serving, or detached and intact."""
    if robustness == "faulted":
        return False
    if state == "attached":
        return robustness in ("healthy", "degraded")
    return state == "detached"


def describe_shared_volume(
    kube: KubeClient, settings: Settings, namespace: str, deployment_type: str
) -> SharedVolume:
    name = shared_volume_name(deployment_type)
    try:
        pvc = kube.core.read_namespaced_persistent_volume_claim(name, namespace)
    except ApiException as exc:
        if exc.status != 404:
            raise
        return SharedVolume(name=name, created=False, on_longhorn=False, ready=False)

    spec, status = pvc.spec, pvc.status
    phase = (status.phase if status else None) or "Unknown"
    size = (status.capacity or {}).get("storage") if status else None
    if size is None and spec.resources and spec.resources.requests:
        size = spec.resources.requests.get("storage")

    longhorn = (
        _longhorn_volume(kube, settings, spec.volume_name) if spec.volume_name else None
    )
    if longhorn is None:
        return SharedVolume(
            name=name,
            created=True,
            on_longhorn=False,
            ready=phase == "Bound",
            storage_class=spec.storage_class_name,
            size=size,
            status=phase,
        )

    lh_status = longhorn.get("status") or {}
    state, robustness = lh_status.get("state"), lh_status.get("robustness")
    return SharedVolume(
        name=name,
        created=True,
        on_longhorn=True,
        ready=phase == "Bound" and _longhorn_ready(state, robustness),
        storage_class=spec.storage_class_name,
        size=size,
        used_bytes=_as_int(lh_status.get("actualSize")),
        status=" · ".join(value for value in (state, robustness) if value) or phase,
    )
