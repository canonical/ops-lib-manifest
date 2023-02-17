# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
"""Classes used for mutating or adding to manifests."""

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Iterable, List, Mapping, Optional

from lightkube import codecs
from lightkube.codecs import AnyResource
from lightkube.generic_resource import GenericGlobalResource, GenericNamespacedResource
from lightkube.models.core_v1 import Toleration
from lightkube.models.meta_v1 import Time

if TYPE_CHECKING:
    from .manifest import Manifests  # pragma: no cover

log = logging.getLogger(__file__)


@dataclass
class AnyCondition:
    """Condition describes the state of a resources at a certain point.

    **parameters**

    * **status** ``str`` - Status of the condition, one of True, False, Unknown.
    * **type** ``str`` - Type of replica set condition.
    * **lastTransitionTime** ``meta_v1.Time`` - *(optional)* The last time the condition
                                                transitioned from one status to another.
    * **message** ``str`` - *(optional)* A human readable message indicating details
                                         about the transition.
    * **reason** ``str`` - *(optional)* The reason for the condition's last transition.
    """

    status: "str"
    type: "str"
    lastTransitionTime: Optional[Time] = None
    message: Optional[str] = None
    reason: Optional[str] = None


def _unique(collection, key):
    """Yields a unique iterable of items from collection.

    uniqueness is determined from the result of key(item).
    """
    seen = set()

    for item in collection:
        value = key(item)
        if value not in seen:
            seen.add(value)
            yield item


class HashableResource:
    """Wraps a lightkube resource object so it is hashable."""

    def __init__(self, resource: AnyResource):
        self.resource = resource

    def __uniq(self):
        return self.kind, self.namespace, self.name

    @staticmethod
    def _condition_unwrap(condition: Mapping[str, str]) -> Optional[AnyCondition]:
        """Attempt to retrieve status and type from a Mapping"""
        try:
            _status, _type = (condition[_] for _ in ("status", "type"))
            return AnyCondition(_status, _type)
        except KeyError:
            return None

    @property
    def status_conditions(self) -> List[AnyCondition]:
        status = getattr(self.resource, "status", None)
        if not status:
            return []
        elif isinstance(self.resource.status, dict):
            conditions = [
                _
                for c in self.resource.status.get("conditions", [])
                for _ in map(self._condition_unwrap, [c])
                if _
            ]
        else:
            conditions = getattr(self.resource.status, "conditions", [])
        return conditions

    @property
    def kind(self) -> str:
        """Return the resource's kind."""
        return self.resource.kind or type(self.resource).__name__

    @property
    def namespace(self) -> Optional[str]:
        """Return the resource's namespace."""
        return self.resource.metadata.namespace if self.resource.metadata else None

    @property
    def name(self) -> Optional[str]:
        """Return the resource's name."""
        return self.resource.metadata.name if self.resource.metadata else None

    def __str__(self):
        """String version of the unique parts.

        example: 'kind/[namespace/]name'
        """
        return "/".join(filter(None, self.__uniq()))

    def __hash__(self):
        """Returns a hash of the unique parts."""
        return hash(self.__uniq())

    def __eq__(self, other):
        """Comparison only of the unique parts."""
        return isinstance(other, HashableResource) and other.__uniq() == self.__uniq()


class Manipulation:
    """Class used to support charm deviations from the manifests."""

    def __init__(self, manifests: "Manifests") -> None:
        self.manifests = manifests


class Patch(Manipulation):
    """Class used to define how to patch an existing object in the manifests."""

    def __call__(self, obj: AnyResource) -> None:
        """Method called to optionally update the object before application."""
        ...


class Addition(Manipulation):
    """Class used to define objects to add to the original manifests."""

    def __call__(self) -> Optional[AnyResource]:
        """Method called to optionally create an object."""
        ...


class Subtraction(Manipulation):
    """Class used to define objects to subtract from the original manifests."""

    def __call__(self, obj: AnyResource) -> bool:  # type: ignore
        """Method called to optionally subtract an object.

        this is an abstract method, each implementation should return a bool
        """
        ...


class CreateNamespace(Addition):
    """Class used to create additional namespace before apply manifests."""

    def __init__(self, manifests: "Manifests", namespace: str) -> None:
        super().__init__(manifests)
        self.namespace = namespace

    def __call__(self) -> Optional[AnyResource]:
        """Create the default namespace if available."""
        log.info(f"Creating namespace {self.namespace}")
        return codecs.from_dict(
            dict(
                apiVersion="v1",
                kind="Namespace",
                metadata=dict(name=self.namespace),
            )
        )


class ManifestLabel(Patch):
    """Ensure every manifest item is labeled with the manifest name.

    Similar to helm charts, add to each metadata some information
    regarding what applied this resource up
    https://helm.sh/docs/chart_best_practices/labels/
    """

    def __call__(self, obj: AnyResource):
        """Adds manifest.name label to obj."""
        if obj.metadata:
            version = self.manifests.current_release
            labels = {
                "juju.io/application": self.manifests.model.app.name,
                "juju.io/manifest": self.manifests.name,
                "juju.io/manifest-version": f"{self.manifests.name}-{version}",
            }
            if isinstance(obj, (GenericGlobalResource, GenericNamespacedResource)):
                # Custom resources in lightkube are built differently
                # from standard model resources
                obj["metadata"]["labels"] = obj.metadata.labels or {}
            else:
                # ensure object has labels
                obj.metadata.labels = obj.metadata.labels or {}
            obj.metadata.labels.update(**labels)


class ConfigRegistry(Patch):
    """Applies image registry to the manifest."""

    def __call__(self, obj):
        """Use the image-registry config and updates container images in obj."""
        registry = self.manifests.config.get("image-registry")
        if not registry:
            return
        if obj.kind in [
            # https://kubernetes.io/docs/concepts/workloads/pods/
            "Pod"
        ]:
            spec = obj.spec
        elif obj.kind in [
            # https://kubernetes.io/docs/concepts/workloads/controllers/daemonset/
            "DaemonSet",
            # https://kubernetes.io/docs/concepts/workloads/controllers/deployment/
            "Deployment",
            # https://kubernetes.io/docs/concepts/workloads/controllers/job/
            "Job",
            # https://kubernetes.io/docs/concepts/workloads/controllers/replicaset/
            "ReplicaSet",
            # https://kubernetes.io/docs/concepts/workloads/controllers/replicationcontroller/
            "ReplicationController",
            # https://kubernetes.io/docs/concepts/workloads/controllers/statefulset/
            "StatefulSet",
        ]:
            spec = obj.spec.template.spec
        elif obj.kind in [
            # https://kubernetes.io/docs/concepts/workloads/controllers/cron-jobs/
            "CronJob"
        ]:
            spec = obj.spec.jobTemplate.spec.template.spec
        else:
            spec = None

        containers = []
        if spec:
            if spec.containers:
                containers += spec.containers
            if spec.initContainers:
                containers += spec.initContainers

        for container in containers:
            full_image = container.image
            if full_image:
                _, image = full_image.split("/", 1)
                new_full_image = f"{registry}/{image}"
                container.image = new_full_image
                log.info(f"Replacing Image: {full_image} with {new_full_image}")


TolerationAdjuster = Callable[[List[Toleration]], Iterable[Toleration]]


def update_tolerations(obj: AnyResource, adjuster: TolerationAdjuster):
    """Uses the adjuster service and updates any object tolerations."""
    if obj.kind in ["Pod"]:
        spec = obj.spec
    elif obj.kind in ["DaemonSet", "Deployment", "StatefulSet"]:
        spec = obj.spec.template.spec
    else:
        spec = None

    if spec:
        updated = list(
            _unique(
                adjuster(spec.tolerations), key=lambda t: tuple(t.to_dict().values())
            )
        )
        log.info(f"Applying tolerations {updated} to {HashableResource(obj)}")
        spec.tolerations = updated
    return obj


class SubtractEq(Subtraction):
    """Ensure every manifest item is labeled with the manifest name.

    Similar to helm charts, add to each metadata some information
    regarding what applied this resource up
    https://helm.sh/docs/chart_best_practices/labels/
    """

    def __init__(self, manifests: "Manifests", to_compare: AnyResource) -> None:
        super().__init__(manifests)
        self.to_compare = to_compare

    def __call__(self, obj: AnyResource) -> bool:
        """Returns true if obj == rsc based on kind, name, and namespace"""
        return HashableResource(self.to_compare) == HashableResource(obj)
