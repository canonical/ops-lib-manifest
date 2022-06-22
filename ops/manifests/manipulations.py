# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
"""Classes used for mutating or adding to manifests."""

import logging
from typing import Callable, List, Optional, TYPE_CHECKING

from lightkube import codecs
from lightkube.codecs import AnyResource
from lightkube.models.core_v1 import Toleration

if TYPE_CHECKING:
    from .manifest import Manifests

log = logging.getLogger(__file__)


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


class CreateNamespace(Addition):
    """Class used to create additional namespace before apply manifests."""

    def __init__(self, manifests: "Manifests", namespace="") -> None:
        super().__init__(manifests)
        self.namespace = namespace

    def __call__(self) -> Optional[AnyResource]:
        """Create the default namespace if available."""
        which_ns = self.namespace or self.manifests.namespace
        if which_ns:
            return codecs.from_dict(
                dict(
                    apiVersion="v1",
                    kind="Namespace",
                    metadata=dict(name=which_ns),
                )
            )
        return None


class ManifestLabel(Patch):
    """Ensure every manifest item is labeled with the manifest name."""

    def __call__(self, obj: AnyResource):
        """Adds manifest.name label to obj."""
        if obj.metadata:
            obj.metadata.labels = obj.metadata.labels or {}  # ensure object has labels
            obj.metadata.labels[self.manifests.name] = "true"


class ConfigRegistry(Patch):
    """Applies image registry to the manifest."""

    def __call__(self, obj):
        """Use the image-registry config and updates container images in obj."""
        registry = self.manifests.config.get("image-registry")
        if not registry:
            return
        if obj.kind in ["Pod"]:
            containers = obj.spec.containers
        elif obj.kind in ["DaemonSet", "Deployment", "StatefulSet"]:
            containers = obj.spec.template.spec.containers
        else:
            containers = []

        for container in containers:
            full_image = container.image
            if full_image:
                _, image = full_image.split("/", 1)
                new_full_image = f"{registry}/{image}"
                container.image = new_full_image
                log.info(f"Replacing Image: {full_image} with {new_full_image}")


TolerationAdjuster = Callable[[Toleration], List[Toleration]]


def update_toleration(obj: AnyResource, adjuster: TolerationAdjuster):
    """Uses the adjuster service and updates any object tolerations."""
    if obj.kind in ["Pod"]:
        spec = obj.spec
    elif obj.kind in ["DaemonSet", "Deployment", "StatefulSet"]:
        spec = obj.spec.template.spec
    else:
        return

    new_tolerations = []
    for toleration in spec.tolerations:
        adjustment = adjuster(toleration)
        if not adjustment:
            log.info(f"Removing toleration {toleration}")
        else:
            log.info(f"Replacing toleration {toleration} with {adjustment}")
            new_tolerations += adjustment
    spec.tolerations = new_tolerations
