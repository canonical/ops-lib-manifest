# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
"""Base class useful operating on manifests from collections of releases."""

import logging
import os
import re
from collections import namedtuple, OrderedDict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import cast, Dict, FrozenSet, List, Optional, Union

import yaml
from backports.cached_property import cached_property
from lightkube import Client, codecs
from lightkube.codecs import AnyResource
from lightkube.core.exceptions import ApiError
from lightkube.models.meta_v1 import Time

from .manipulations import Addition, ManifestLabel, Manipulation, Patch

log = logging.getLogger(__file__)

PathLike = Union[str, os.PathLike]
Version = List[Union[str, int]]

_VERSION_SPLIT = re.compile(r"(\d+)")


def _by_version(version: str) -> Version:
    def convert(part):
        return int(part) if part.isdigit() else part

    return [convert(c) for c in _VERSION_SPLIT.split(version)]


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


class HashableResource:
    """Wraps a lightkube resource object so it is hashable."""

    def __init__(self, resource: AnyResource):
        self.resource = resource

    def __uniq(self):
        return self.kind, self.namespace, self.name

    @property
    def status_conditions(self) -> List[AnyCondition]:
        status = getattr(self.resource, "status", None)
        if not status:
            return []
        return getattr(self.resource.status, "conditions", [])

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


class Manifests:
    """Class used to apply manifest files from a release directory.

    base_path should link to a folder heirarchy

    <base_path>
    ├── version                  - a file containing the default version
    ├── manifests                - a folder containing all the releases
    │   ├── v1.1.10              - a folder matching a configurable version
    │   │   ├── manifest-1.yaml  - any file with a `.yaml` file type
    │   │   └── manifest-2.yaml
    │   ├── v1.1.11
    │   │   ├── manifest-1.yaml
    │   │   └── manifest-2.yaml
    │   │   └── manifest-3.yaml
    """

    def __init__(
        self,
        name: str,
        app_name: str,
        base_path: PathLike,
        manipulations: List[Manipulation] = None,
    ):
        """Create Manifests object.

        @param name:         Uniquely idenitifes these released manifests.
        @param app_name:     Charm application name which deploys this manifest.
        @param base_path:    path to folder containing manifest files for various
                             releases.
        @param manipulations list of manipulation objects which will alter the existing
                             resources in the manifest files.
                             ~ defaults to updating the label ~
        """

        self.name = name
        self.app_name = app_name
        self.base_path = Path(base_path)
        if manipulations is None:
            self.manipulations: List[Manipulation] = [ManifestLabel(self)]
        else:
            self.manipulations = manipulations

    @cached_property
    def client(self) -> Client:
        """Lazy evaluation of the lightkube client."""
        return Client(field_manager=f"{self.app_name}-{self.name}")

    @property
    def config(self) -> Dict:
        """Retrieve the current available config to use during manifest building."""
        raise NotImplementedError

    @cached_property
    def manifest_path(self) -> Path:
        """Retrieve the path where the versioned manifests exist."""
        return self.base_path / "manifests"

    @cached_property
    def releases(self) -> List[str]:
        """List all possible releases supported by the manifests.

        Results are sorted by highest release number first.
        """
        return sorted(
            set(
                manifests.parent.name
                for manifests in self.manifest_path.glob("*/*.yaml")
            ),
            key=_by_version,
            reverse=True,
        )  # sort numerically

    @cached_property
    def default_release(self) -> str:
        """Lookup the default release suggested by the manifest."""
        version = self.base_path / "version"
        return version.read_text(encoding="utf-8").strip() if version.exists() else ""

    @cached_property
    def latest_release(self) -> str:
        """Lookup the default release suggested by the manifest."""
        return self.releases[0]

    @property
    def current_release(self) -> str:
        """Determine the current release from charm config."""
        return self.config.get("release") or self.default_release or self.latest_release

    @property
    def resources(self) -> FrozenSet[HashableResource]:
        """All component resource sets subdivided by kind and namespace."""
        result: Dict[HashableResource, None] = OrderedDict()
        ver = self.current_release

        # Generated additions
        for manipulate in self.manipulations:
            if isinstance(manipulate, Addition):
                obj = manipulate()
                if not obj:
                    continue
                result[HashableResource(obj)] = None

        # From static manifests
        for manifest in (self.manifest_path / ver).glob("*.yaml"):
            for obj in self._safe_load(manifest):
                result[HashableResource(obj)] = None

        return cast(FrozenSet[HashableResource], result.keys())

    @lru_cache()
    def _safe_load(self, filepath: Path) -> List[AnyResource]:
        """Read manifest file and parse its content into lightkube objects.

        Lightkube can't properly read manifest files which contain List kinds.
        """
        content = filepath.read_text()
        return [
            codecs.from_dict(item)  # Map to lightkube resources
            for rsc in yaml.safe_load_all(content)  # load content from file
            if rsc  # ignore empty objects
            for item in (rsc["items"] if rsc["kind"] == "List" else [rsc])
        ]

    def status(self) -> FrozenSet[HashableResource]:
        """Returns all installed objects which have a `.status.conditions` attribute."""
        return frozenset(_ for _ in self.installed_resources() if _.status_conditions)

    def installed_resources(self) -> FrozenSet[HashableResource]:
        """All currently installed resources expected by this manifest."""
        result: Dict[HashableResource, None] = OrderedDict()
        for obj in self.resources:
            try:
                next_rsc = self.client.get(
                    type(obj.resource),
                    obj.name,
                    namespace=obj.namespace,
                )
            except ApiError:
                continue
            result[HashableResource(next_rsc)] = None
        return cast(FrozenSet[HashableResource], result.keys())

    def labelled_resources(self) -> FrozenSet[HashableResource]:
        """Set of any installed resource ever labeled by this manifest."""

        NamespaceKind = namedtuple("NamespaceKind", "namespace, kind")
        ns_kinds = set(
            NamespaceKind(obj.namespace, type(obj.resource)) for obj in self.resources
        )

        return frozenset(
            HashableResource(rsc)
            for ns_kind in ns_kinds
            for rsc in self.client.list(
                ns_kind.kind,
                namespace=ns_kind.namespace,
                labels={
                    "juju.io/application": self.app_name,
                    "juju.io/manifest": self.name,
                },
            )
        )

    def apply_manifests(self):
        """Apply all manifest files from the current release."""
        log.info(f"Applying {self.name} version: {self.current_release}")
        for rsc in self.resources:
            for manipulate in self.manipulations:
                if isinstance(manipulate, Patch):
                    manipulate(rsc.resource)

            log.info(f"Applying {rsc}")
            try:
                self.client.apply(rsc.resource, force=True)
            except ApiError:
                log.exception(f"Failed Applying {rsc}")
                raise
        log.info("Applying Complete")

    def delete_manifests(self, **kwargs):
        """Delete all manifests associated with the current resources."""
        self.delete_resources(*self.resources, **kwargs)

    def delete_resources(
        self,
        *resources: HashableResource,
        namespace: Optional[str] = None,
        ignore_not_found: bool = False,
        ignore_unauthorized: bool = False,
    ):
        """Delete named resources."""
        for obj in resources:
            try:
                namespace = obj.namespace or namespace
                log.info(f"Deleting {obj}")
                self.client.delete(type(obj.resource), obj.name, namespace=namespace)
            except ApiError as err:
                if err.status.message is not None:
                    err_lower = err.status.message.lower()
                    if "not found" in err_lower and ignore_not_found:
                        log.warning(f"Ignoring not found error: {err.status.message}")
                    elif "(unauthorized)" in err_lower and ignore_unauthorized:
                        # Ignore error from https://bugs.launchpad.net/juju/+bug/1941655
                        log.warning(f"Unauthorized error ignored: {err.status.message}")
                    else:
                        log.exception(
                            "ApiError encountered while attempting to delete resource: "
                            + err.status.message
                        )
                        raise
                else:
                    log.exception(
                        "ApiError encountered while attempting to delete resource."
                    )
                    raise

    delete_resource = delete_resources
