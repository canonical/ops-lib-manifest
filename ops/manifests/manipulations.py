# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
"""Classes used for mutating or adding to manifests."""

import logging
from dataclasses import dataclass
from typing import (
    TYPE_CHECKING,
    Callable,
    Iterable,
    Iterator,
    List,
    Mapping,
    Optional,
    Union,
    cast,
)

from lightkube import codecs
from lightkube.generic_resource import GenericGlobalResource, GenericNamespacedResource
from lightkube.models.core_v1 import Toleration
from lightkube.models.meta_v1 import Time

import ops.manifests.literals as literals

AnyResource = Union[GenericGlobalResource, GenericNamespacedResource]

if TYPE_CHECKING:
    from .manifest import Manifests  # pragma: no cover

log = logging.getLogger(__file__)


class NameValidationError(Exception):
    """Raised when a Kubernetes resource name violates RFC1123 rules."""


def validate_resource_name(name_to_check: Optional[str], resource_type: str = "Resource") -> None:
    """Verify that a resource name meets Kubernetes RFC1123 subdomain requirements.

    Kubernetes requires resource names to be RFC1123 subdomains which means:
    - Maximum 253 characters total
    - Consists of lowercase alphanumeric characters, hyphens, or periods
    - Must begin and end with an alphanumeric character
    - When split by periods, each label must be 1-63 characters
    - Each label must start and end with an alphanumeric character

    Args:
        name_to_check: The resource name to validate (can be None)
        resource_type: Type of resource for error messaging

    Raises:
        NameValidationError: If the name violates any RFC1123 rule
    """
    if not name_to_check:
        raise NameValidationError(f"{resource_type} name cannot be empty or None")

    name_len = len(name_to_check)

    # Check maximum length first
    if name_len > literals.MAX_NAME_LENGTH:
        safe_name = repr(name_to_check)
        raise NameValidationError(
            f"{resource_type} name {safe_name} is too long ({name_len} characters). "
            f"Maximum allowed is {literals.MAX_NAME_LENGTH} characters"
        )

    # Use regex to validate RFC1123 subdomain format
    if not literals.RFC1123_SUBDOMAIN_PATTERN.match(name_to_check):
        # Provide detailed error message by checking specific violations
        safe_name = repr(name_to_check)
        
        # Check if name starts or ends with a period (these create empty labels)
        if name_to_check[0] == '.' or name_to_check[-1] == '.':
            first_char = name_to_check[0]
            last_char = name_to_check[-1]
            if first_char == '.':
                raise NameValidationError(
                    f"{resource_type} name {safe_name} starts with '.' which is invalid. "
                    f"Names must begin with a lowercase letter (a-z) or digit (0-9)"
                )
            if last_char == '.':
                raise NameValidationError(
                    f"{resource_type} name {safe_name} ends with '.' which is invalid. "
                    f"Names must end with a lowercase letter (a-z) or digit (0-9)"
                )
        
        # Check for consecutive dots (empty labels)
        if ".." in name_to_check:
            raise NameValidationError(
                f"{resource_type} name {safe_name} contains empty labels (consecutive periods). "
                f"Each period-separated label must contain at least one character"
            )
        
        # Check per-label constraints
        labels = name_to_check.split(".")
        for label in labels:
            if len(label) > 63:
                safe_label = repr(label)
                raise NameValidationError(
                    f"{resource_type} name {safe_name} contains label {safe_label} that is too long ({len(label)} characters). "
                    f"Each period-separated label must be at most 63 characters"
                )
            
            # Check if label starts with invalid character (skip empty labels from start/end dots)
            if label and label[0] not in literals.ALPHANUMERIC_LOWER:
                safe_label = repr(label)
                safe_char = repr(label[0])
                # For single-label names, provide simpler error message
                if len(labels) == 1:
                    raise NameValidationError(
                        f"{resource_type} name {safe_name} starts with {safe_char} which is invalid. "
                        f"Names must begin with a lowercase letter (a-z) or digit (0-9)"
                    )
                else:
                    raise NameValidationError(
                        f"{resource_type} name {safe_name} contains label {safe_label} starting with {safe_char}. "
                        f"Each period-separated label must start with a lowercase letter or digit"
                    )
            
            # Check if label ends with invalid character (skip empty labels from start/end dots)
            if label and label[-1] not in literals.ALPHANUMERIC_LOWER:
                safe_label = repr(label)
                safe_char = repr(label[-1])
                # For single-label names, provide simpler error message
                if len(labels) == 1:
                    raise NameValidationError(
                        f"{resource_type} name {safe_name} ends with {safe_char} which is invalid. "
                        f"Names must end with a lowercase letter (a-z) or digit (0-9)"
                    )
                else:
                    raise NameValidationError(
                        f"{resource_type} name {safe_name} contains label {safe_label} ending with {safe_char}. "
                        f"Each period-separated label must end with a lowercase letter or digit"
                    )
        
        # Check for invalid characters (if none of the above caught it)
        name_chars = set(name_to_check)
        invalid_chars = name_chars - literals.VALID_NAME_CHARS
        
        if invalid_chars:
            # Create helpful error message based on what's wrong
            error_details = []
            if any(c.isupper() for c in invalid_chars):
                error_details.append("uppercase letters (use lowercase instead)")
            if "_" in invalid_chars:
                error_details.append("underscores (use hyphens instead)")
            if " " in invalid_chars:
                error_details.append("spaces (use hyphens instead)")
            
            other_invalid = invalid_chars - literals.COMMON_INVALID_CHARS
            if other_invalid:
                char_list = ", ".join(repr(c) for c in sorted(other_invalid))
                error_details.append(f"invalid characters: {char_list}")
            
            detail_str = "; ".join(error_details)
            raise NameValidationError(
                f"{resource_type} name {safe_name} contains {detail_str}. "
                f"Only lowercase letters, digits, hyphens (-), and periods (.) are permitted"
            )

    log.debug(f"Validated {resource_type} name: {repr(name_to_check)}")


def get_validation_error(name_to_check: Optional[str], resource_type: str = "Resource") -> Optional[str]:
    """Check if a name is valid and return an error message if not.

    This is a non-throwing version of validate_resource_name that returns
    an error string instead of raising an exception.

    Args:
        name_to_check: The resource name to validate (can be None)
        resource_type: Type of resource for error messaging

    Returns:
        Error message if invalid, None if valid
    """
    try:
        validate_resource_name(name_to_check, resource_type)
        return None
    except NameValidationError as e:
        return str(e)


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
        conditions: List[AnyCondition] = []
        status = getattr(self.resource, "status", None)
        if not status:
            return conditions
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
        return self.resource.metadata and self.resource.metadata.namespace or None

    @property
    def name(self) -> Optional[str]:
        """Return the resource's name."""
        return self.resource.metadata and self.resource.metadata.name or None

    @property
    def labels(self) -> Mapping[str, str]:
        """Return the resource's labels."""
        return self.resource.metadata and self.resource.metadata.labels or {}

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

    def __call__(self) -> Union[None, AnyResource, Iterable[AnyResource]]:
        """Method called to optionally create an object."""
        ...

    def __iter__(self) -> Iterator[AnyResource]:
        """Treat every addition like a possible collection."""
        obj = self()
        if obj is None:
            return iter(())
        else:
            try:
                return iter(obj)
            except TypeError:
                obj = cast(AnyResource, obj)
                return iter((obj,))


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
                literals.APP_LABEL: self.manifests.model.app.name,
                literals.MANIFEST_LABEL: self.manifests.name,
                literals.MANIFEST_VERSION_LABEL: f"{self.manifests.name}-{version}",
            }
            if isinstance(obj, (GenericGlobalResource, GenericNamespacedResource)):
                # Custom resources in lightkube are built differently
                # from standard model resources
                obj["metadata"]["labels"] = obj.metadata.labels or {}
                obj["metadata"]["labels"].update(**labels)
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
            _unique(adjuster(spec.tolerations), key=lambda t: tuple(t.to_dict().values()))
        )
        log.info(f"Applying tolerations {updated} to {HashableResource(obj)}")
        spec.tolerations = updated
    return obj


class SubtractEq(Subtraction):
    """Remove any resource if they match a provided resource."""

    def __init__(self, manifests: "Manifests", to_compare: AnyResource) -> None:
        super().__init__(manifests)
        self.to_compare = to_compare

    def __call__(self, obj: AnyResource) -> bool:
        """Returns true if obj == rsc based on kind, name, and namespace"""
        return HashableResource(self.to_compare) == HashableResource(obj)


class ValidateResourceNames(Patch):
    """Validate that all resource names comply with RFC1123 subdomain rules."""

    def __call__(self, obj: AnyResource) -> None:
        """Check resource name against Kubernetes naming requirements."""
        if obj.metadata is None or obj.metadata.name is None:
            return

        resource_kind = obj.kind if hasattr(obj, "kind") else "Resource"
        error_msg = get_validation_error(obj.metadata.name, resource_kind)

        if error_msg:
            log.error("RFC1123 validation failed: %s", error_msg)
            raise NameValidationError(f"Invalid Kubernetes resource name: {error_msg}")

