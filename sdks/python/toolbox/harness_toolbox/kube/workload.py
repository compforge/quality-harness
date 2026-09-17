"""Workload resolution over the existing native resource access path."""

from typing import Protocol

from aiohttp import ClientConnectionError, ClientSSLError
from harness_common import KubernetesWorkload, KubernetesWorkloadInstance
from kubernetes_asyncio import client as kubernetes
from kubernetes_asyncio.client.exceptions import ApiException

from harness_toolbox.errors import ErrorKind, KubernetesError, ToolboxError
from harness_toolbox.kube.selector import label_selector


class _Resources(Protocol):
    async def get(self, api_version: str, kind: str, name: str) -> dict: ...
    async def list(self, api_version: str, kind: str, *, label_selector: str = "") -> dict: ...


async def resolve_workload(
    resources: _Resources,
    workload: KubernetesWorkload,
    namespace: str,
    environment: str,
) -> list[KubernetesWorkloadInstance]:
    """Resolve without readiness filtering. API failure is never an empty inventory.

    @rule environment is a stable, non-secret target ID supplied by the caller's
    environment registry. Transport, credentials and config paths never derive it.
    """
    if not environment.strip():
        raise KubernetesError("Environment identity is required", kind=ErrorKind.INVALID_ARGUMENT)
    if not namespace.strip():
        raise KubernetesError("Workload namespace is required", kind=ErrorKind.INVALID_ARGUMENT)
    try:
        location = workload.location
        if location["kind"] == "labels":
            selector = label_selector(kubernetes.V1LabelSelector(match_labels=location["labels"]))
        else:
            kind = "Service" if location["kind"] == "service" else location["resource_kind"]
            if kind not in ("Service", "Pod", "Deployment", "StatefulSet", "DaemonSet"):
                raise KubernetesError(
                    "Unsupported workload resource", kind=ErrorKind.UNSUPPORTED_OPERATION
                )
            resource = await resources.get(
                "v1" if kind in ("Service", "Pod") else "apps/v1", kind, location["name"]
            )
            if kind == "Pod":
                return [_instance(resource, workload, namespace, environment)]
            selected = resource.get("spec", {}).get("selector")
            if not selected:
                raise KubernetesError(
                    "Resource has no Pod selector", kind=ErrorKind.UNSUPPORTED_OPERATION
                )
            if kind == "Service":
                selector = label_selector(kubernetes.V1LabelSelector(match_labels=selected))
            else:
                selector = label_selector(
                    kubernetes.V1LabelSelector(
                        match_labels=selected.get("matchLabels"),
                        match_expressions=[
                            kubernetes.V1LabelSelectorRequirement(**entry)
                            for entry in selected.get("matchExpressions", [])
                        ],
                    )
                )
        result = await resources.list("v1", "Pod", label_selector=selector)
        return sorted(
            [_instance(pod, workload, namespace, environment) for pod in result["items"]],
            key=lambda item: (item.pod, item.uid),
        )
    except ToolboxError:
        raise
    except ApiException as error:
        kind = {
            401: ErrorKind.AUTHENTICATION_FAILED,
            403: ErrorKind.PERMISSION_DENIED,
            404: ErrorKind.RESOURCE_NOT_FOUND,
            408: ErrorKind.TIMEOUT,
            504: ErrorKind.TIMEOUT,
            429: ErrorKind.LIMIT_EXCEEDED,
        }.get(error.status, ErrorKind.OPERATION_FAILED)
        raise KubernetesError(
            f"Resolve workload in namespace {namespace!r}: {kind.value}",
            kind=kind,
            code=error.status,
        ) from error
    except TimeoutError as error:
        raise KubernetesError("Workload discovery timed out", kind=ErrorKind.TIMEOUT) from error
    except ClientSSLError as error:
        raise KubernetesError(
            "Kubernetes TLS verification failed", kind=ErrorKind.TLS_VERIFICATION_FAILED
        ) from error
    except ClientConnectionError as error:
        raise KubernetesError(
            "Kubernetes connection failed", kind=ErrorKind.CONNECTION_FAILED
        ) from error
    except (KeyError, TypeError, ValueError) as error:
        raise KubernetesError(
            "Invalid workload selector or API response", kind=ErrorKind.INVALID_RESPONSE
        ) from error
    except Exception as error:
        raise KubernetesError(
            "Workload discovery failed", kind=ErrorKind.OPERATION_FAILED
        ) from error


def _instance(
    pod: dict, workload: KubernetesWorkload, namespace: str, environment: str
) -> KubernetesWorkloadInstance:
    metadata = pod["metadata"]
    if (
        not metadata.get("name")
        or not metadata.get("uid")
        or metadata.get("namespace") != namespace
    ):
        raise KubernetesError(
            "Pod response lacks identity or has a different namespace",
            kind=ErrorKind.INVALID_RESPONSE,
        )
    return KubernetesWorkloadInstance(
        environment, workload.name, namespace, metadata["name"], metadata["uid"], workload.container
    )
