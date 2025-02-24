# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
"""Base class useful operating on manifests from collections of releases."""

import logging
import os
import re
from collections import OrderedDict, namedtuple
from functools import cached_property, lru_cache
from pathlib import Path
from typing import (
    Dict,
    FrozenSet,
    Iterator,
    KeysView,
    List,
    Mapping,
    Optional,
    Union,
    no_type_check,
)

import yaml
from httpx import HTTPError
from lightkube import Client, codecs
from lightkube.codecs import AnyResource
from lightkube.core.exceptions import ApiError
from lightkube.generic_resource import (
    create_resources_from_crd,
    load_in_cluster_generic_resources,
)

import ops.manifests.literals as literals
from ops.model import Model

from .exceptions import ManifestClientError
from .manipulations import (
    Addition,
    AnyCondition,
    HashableResource,
    ManifestLabel,
    Manipulation,
    Patch,
    Subtraction,
)

log = logging.getLogger(__file__)

PathLike = Union[str, os.PathLike]
Version = List[Union[str, int]]

_VERSION_SPLIT = re.compile(r"(\d+)")
FILE_TYPES = ["yaml", "yml"]


def _by_version(version: str) -> Version:
    def convert(part):
        return int(part) if part.isdigit() else part

    return [convert(c) for c in _VERSION_SPLIT.split(version)]


class Manifests:
    """Class used to apply manifest files from a release directory.

    base_path should link to a folder heirarchy

    <base_path>
    ├── version                  - a file containing the default version
    ├── manifests                - a folder containing all the releases
    │   ├── v1.1.10              - a folder matching a configurable version
    │   │   ├── manifest-1.yaml  - any file with a `.yaml|.yml` file type
    │   │   └── manifest-2.yaml
    │   ├── v1.1.11
    │   │   ├── manifest-1.yml
    │   │   └── manifest-2.yml
    │   │   └── manifest-3.yaml
    """

    def __init__(
        self,
        name: str,
        model: Model,
        base_path: PathLike,
        manipulations: Optional[List[Manipulation]] = None,
    ):
        """Create Manifests object.

        @param name:         Uniquely idenitifes these released manifests.
        @param model:        ops framework Model
        @param base_path:    path to folder containing manifest files for various
                             releases.
        @param manipulations list of manipulation objects which will alter the existing
                             resources in the manifest files.
                             ~ defaults to updating the label ~
        """

        self.name = name
        self.base_path = Path(base_path)
        self.model = model
        if manipulations is None:
            self.manipulations: List[Manipulation] = [ManifestLabel(self)]
        else:
            self.manipulations = manipulations

    @cached_property
    def client(self) -> Client:
        """Lazy evaluation of the lightkube client."""
        client = Client(field_manager=f"{self.model.app.name}-{self.name}")
        msg = "Failed to load in cluster CRDs"
        try:
            load_in_cluster_generic_resources(client)
        except (ApiError, HTTPError) as ex:
            log.exception(msg)
            raise ManifestClientError(msg, ex) from ex
        return client

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
                for ext in FILE_TYPES
                for manifests in self.manifest_path.glob(f"*/*.{ext}")
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
    def resources(self) -> KeysView[HashableResource]:
        """All unique component resources.

        Order is guaranteed to be:
        * Addition Manipulations
        * Subtraction Manipulations
        * Manifest files contents
        * Patches applied to all
        """

        # Generate Addition resources
        additions: List[AnyResource] = list(
            filter(
                None,
                (
                    add
                    for manipulate in self.manipulations
                    if isinstance(manipulate, Addition)
                    for add in manipulate
                ),
            )
        )

        # Generate Static resources
        release_path = Path(self.manifest_path / self.current_release)
        ymls = sorted(
            manifests for ext in FILE_TYPES for manifests in release_path.glob(f"*.{ext}")
        )
        statics = [rsc for yml in ymls for rsc in self._resource_from_yaml(yml)]

        # Apply subtractions
        for manipulate in self.manipulations:
            if isinstance(manipulate, Subtraction):
                statics = [rsc for rsc in statics if not manipulate(rsc)]

        # Apply manipulations
        all_resources = additions + statics
        for rsc in all_resources:
            for manipulate in self.manipulations:
                if isinstance(manipulate, Patch):
                    manipulate(rsc)

        return OrderedDict((HashableResource(obj), None) for obj in all_resources).keys()

    def _resource_from_yaml(self, filepath: Path) -> List[AnyResource]:
        """Read manifest file and parse its contents into Lightkube Objects."""

        def create_crd(rsc):
            if rsc.kind == "CustomResourceDefinition":
                create_resources_from_crd(rsc)
            return rsc

        return [
            create_crd(codecs.from_dict(dict(item)))  # Map to lightkube resources
            for item in self._safe_load(filepath)
        ]

    @lru_cache()
    def _safe_load(self, filepath: Path) -> List[Mapping]:
        """Read manifest file and parse its content into list of dicts.

        Note: Lightkube can't properly read kind = *List (a list of k8s resources).
        Therefore this method will also flatten all kind = *List resources into a python
        list of resources.
        """

        def _flatten(raw_resources: Iterator) -> List[Mapping]:
            """Flatten objects in a given python iterator which are kind=List."""
            resources = []
            for rsc in raw_resources:
                if not isinstance(rsc, Mapping):
                    # found a non-dict item?  Let's log it
                    log.warning(f"Ignoring non-dictionary resource rsc='{rsc}' in {filepath}")
                elif not rsc.get("kind") or not rsc.get("apiVersion"):
                    log.warning(f"Ignoring non-kubernetes resource rsc='{rsc}' in {filepath}")
                elif rsc["kind"].endswith("List"):
                    # found a "*List" kind -- lets _flatten all its "items"
                    resources += _flatten(rsc.get("items", []))
                else:
                    # found a non-"List" kind
                    resources.append(rsc)
            return resources

        # read manifest file as a list
        # yaml.safe_load_all(..) can read yaml documents which contain
        #   * 1 kubernetes resource
        #       * yields a list with 1 item
        #   * N kubernetes resources separated with the document separator
        #       * https://yaml.org/spec/1.0/#id2561718
        #       * yields a list of N items

        content_list = yaml.safe_load_all(filepath.read_text())
        return _flatten(content_list)

    def status(self) -> FrozenSet[HashableResource]:
        """Returns all installed objects which have a `.status.conditions` attribute."""
        return frozenset(_ for _ in self.installed_resources() if _.status_conditions)

    def is_ready(self, obj: HashableResource, cond: AnyCondition) -> Optional[bool]:
        """
        Default Implementation
        Can be overriden by a manifest object.

        Evaluates True if an object's condition is ready.
        Evaluates False if an object's condition is not ready.
        Evaluates None if an object's condition should be ignored.
        """
        return cond.status == "True"

    @no_type_check
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
            except ManifestClientError:
                log.exception(f"Cannot connect to the api endpoint, marking ({obj}) as missing")
                continue
            except (ApiError, HTTPError):
                log.exception(f"Didn't find expected resource installed ({obj})")
                continue
            result[HashableResource(next_rsc)] = None
        return frozenset(result.keys())

    def conflicting_resources(
        self, installed: FrozenSet[HashableResource]
    ) -> FrozenSet[HashableResource]:
        """Determine which currently installed resources were installed by this manifest.

        Returns:
            A set of resources that are expected to be installed by this
            manifest but have been installed otherwise in the cluster.
        """
        result: Dict[HashableResource, None] = OrderedDict()
        expected = self.resources
        for obj in installed:
            if match := next((m for m in expected if m == obj), None):
                compare = obj, match
                app, match_app = (_.labels.get(literals.APP_LABEL) for _ in compare)
                name, match_name = (_.labels.get(literals.MANIFEST_LABEL) for _ in compare)
                if app != match_app or name != match_name:
                    result[match] = None
            else:
                raise ManifestClientError(f"Unexpected resource installed: {obj}")

        return frozenset(result.keys())

    def labelled_resources(self) -> FrozenSet[HashableResource]:
        """Any resource ever installed and labeled by this class."""
        NamespaceKind = namedtuple("NamespaceKind", "namespace, kind")
        ns_kinds = set(NamespaceKind(obj.namespace, type(obj.resource)) for obj in self.resources)

        return frozenset(
            HashableResource(rsc)
            for ns_kind in ns_kinds
            for rsc in self.client.list(
                ns_kind.kind,
                namespace=ns_kind.namespace,
                labels={
                    literals.APP_LABEL: self.model.app.name,
                    literals.MANIFEST_LABEL: self.name,
                },
            )
        )

    def apply_manifests(self):
        """Apply all manifest files from the current release after manipulating."""
        log.info(f"Applying {self.name} version: {self.current_release}")
        self.apply_resources(*self.resources)

    def delete_manifests(self, **kwargs):
        """Delete all installed manifests associated with the current release."""
        installed_resources = self.labelled_resources()
        self.delete_resources(*installed_resources, **kwargs)

    def apply_resources(self, *resources: HashableResource):
        """Apply set of resources to the cluster.

        @param *resources: set of resourecs to apply
        """
        for rsc in resources:
            log.info(f"Applying {rsc}")
            msg = f"Failed Applying {rsc}"
            try:
                self.client.apply(rsc.resource, force=True)
            except (ApiError, HTTPError) as ex:
                log.exception(msg)
                raise ManifestClientError(msg, ex) from ex
        log.info(f"Applied {len(resources)} Resources")

    def delete_resources(
        self,
        *resources: HashableResource,
        namespace: Optional[str] = None,
        ignore_not_found: bool = False,
        ignore_unauthorized: bool = False,
        ignore_labels: bool = False,
    ):
        """Delete specific resources."""
        for obj in resources:
            try:
                log.info(f"Deleting {obj}...")
                self._delete(obj, obj.namespace or namespace, ignore_labels)
            except (ApiError, HTTPError) as ex:
                msg = str(ex)
                if hasattr(ex, "status") and ex.status.message is not None:
                    msg = ex.status.message
                not_found = ignore_not_found and "not found" in msg.lower()
                unauthed = ignore_unauthorized and "(unauthorized)" in msg.lower()
                if not_found or unauthed:
                    log.warning(f"Ignored failed delete of resource: {obj}")
                    log.warning(msg)
                else:
                    log_msg = f"Failed to delete resource: {obj}"
                    log.exception(f"{log_msg}")
                    raise ManifestClientError(log_msg) from ex

    @no_type_check
    def _delete(self, obj: HashableResource, namespace: Optional[str], ignore_labels: bool):
        if ignore_labels:
            self.client.delete(type(obj.resource), obj.name, namespace=namespace)
        else:
            for item in self.client.list(
                type(obj.resource),
                namespace=namespace,
                labels={
                    literals.APP_LABEL: self.model.app.name,
                    literals.MANIFEST_LABEL: self.name,
                },
                fields={"metadata.name": obj.name},
            ):
                self.client.delete(type(item), item.metadata.name, namespace=namespace)

    apply_resource = apply_resources
    delete_resource = delete_resources
