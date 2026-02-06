# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import unittest.mock as mock

import pytest
from lightkube.codecs import from_dict

from ops.manifests import (
    Addition,
    ConfigRegistry,
    CreateNamespace,
    ManifestLabel,
    SubtractEq,
    update_tolerations,
)


def test_config_registry_unset(caplog):
    manifest, obj = mock.MagicMock(), mock.MagicMock()
    manifest.config = {"image-registry": None}
    adjustment = ConfigRegistry(manifest)

    del obj.kind  # indicate to the mock object this attribute shouldn't be set
    caplog.clear()
    adjustment(obj)
    assert not hasattr(obj, "kind"), "Ensure it exits before assessing `obj.kind`"
    assert caplog.messages == []


def test_config_registry_unsupported(caplog):
    manifest = mock.MagicMock()
    manifest.config = {"image-registry": "rocks.canonical.com:443/cdk"}
    adjustment = ConfigRegistry(manifest)
    obj = from_dict(dict(apiVersion="v1", kind="Secret"))
    caplog.clear()
    adjustment(obj)
    assert caplog.messages == []


def test_config_registry_of_pod(caplog):
    manifest = mock.MagicMock()
    rocks = "rocks.canonical.com:443/cdk"
    manifest.config = {"image-registry": rocks}
    c1 = dict(name="cool-pod", image="mcr.microsoft.com/awesome/image:1.0")
    c2 = dict(name="other-pod", image="gcr.io/other/image:2.0")
    obj = from_dict(dict(apiVersion="v1", kind="Pod", spec=dict(containers=[c1, c2])))

    adjustment = ConfigRegistry(manifest)
    caplog.clear()
    adjustment(obj)

    assert obj.spec.containers[0].image == f"{rocks}/awesome/image:1.0"
    assert obj.spec.containers[1].image == f"{rocks}/other/image:2.0"
    assert caplog.messages == [
        f"Replacing Image: mcr.microsoft.com/awesome/image:1.0 with {rocks}/awesome/image:1.0",
        f"Replacing Image: gcr.io/other/image:2.0 with {rocks}/other/image:2.0",
    ]


def test_config_registry_of_daemonset():
    manifest = mock.MagicMock()
    manifest.config = {"image-registry": "rocks.canonical.com:443/cdk"}
    c1 = dict(name="cool-pod", image="mcr.microsoft.com/awesome/image:1.0")
    c2 = dict(name="other-pod", image="gcr.io/other/image:2.0")
    spec = dict(
        template=dict(spec=dict(containers=[c1, c2])),
        selector=dict(matchLabels=dict(app="myCoolApp")),
    )
    obj = from_dict(dict(apiVersion="apps/v1", kind="DaemonSet", spec=spec))

    adjustment = ConfigRegistry(manifest)
    adjustment(obj)

    assert (
        obj.spec.template.spec.containers[0].image
        == "rocks.canonical.com:443/cdk/awesome/image:1.0"
    )
    assert (
        obj.spec.template.spec.containers[1].image == "rocks.canonical.com:443/cdk/other/image:2.0"
    )


def test_config_registry_pod_with_init_container():
    manifest = mock.MagicMock()
    rocks = "rocks.canonical.com:443/cdk"
    manifest.config = {"image-registry": rocks}
    c1 = dict(name="cool-pod", image="mcr.microsoft.com/awesome/image:1.0")
    c2 = dict(name="other-pod", image="gcr.io/other/image:2.0")
    obj = from_dict(
        dict(apiVersion="v1", kind="Pod", spec=dict(containers=[c1], initContainers=[c2]))
    )

    adjustment = ConfigRegistry(manifest)
    adjustment(obj)

    assert obj.spec.containers[0].image == f"{rocks}/awesome/image:1.0"
    assert obj.spec.initContainers[0].image == f"{rocks}/other/image:2.0"


def test_create_namespace(manifest):
    adjustment = CreateNamespace(manifest, "default")
    obj = adjustment()
    assert obj and obj.metadata, "Should have metadata"
    assert type(obj).__name__ == "Namespace"


def test_manifest_label(manifest):
    obj = from_dict(
        dict(
            apiVersion="v1",
            kind="Secret",
            metadata=dict(name="super-secret", labels={"pre-existing": "label"}),
        )
    )

    adjustment = ManifestLabel(manifest)
    adjustment(obj)

    assert obj.metadata, "Should have metadata"
    assert obj.metadata.labels, "Should have labels"
    assert obj.metadata.labels["pre-existing"] == "label", "Should leave existing labels alone"
    assert obj.metadata.labels["juju.io/application"] == "unit-testing", (
        "Should add the application label"
    )
    assert obj.metadata.labels["juju.io/manifest"] == "test-manifest", (
        "Should add the manifest name"
    )
    assert obj.metadata.labels["juju.io/manifest-version"] == "test-manifest-v0.2", (
        "Should add the manifest label with current-version"
    )


def test_update_pod_toleration():
    def adjuster(tolerations):
        tolerations = tolerations[1:]  # remove first toleration
        tolerations[0].key = "something.else/unreachable"  # patch second toleration
        tolerations.append(tolerations[0])  # duplicate the second just to test dedupe
        return tolerations

    t1 = dict(key="node-role.kubernetes.io/not-ready", operator="Exists", effect="NoSchedule")
    t2 = dict(
        key="node-role.kubernetes.io/unreachable",
        operator="Exists",
        effect="NoSchedule",
    )
    obj = from_dict(
        dict(apiVersion="v1", kind="Pod", spec=dict(tolerations=[t1, t2], containers=[]))
    )

    update_tolerations(obj, adjuster)

    assert len(obj.spec.tolerations) == 1, "The first toleration should be removed"
    assert obj.spec.tolerations[0].key == "something.else/unreachable"


def test_update_deployment_toleration():
    def adjuster(tolerations):
        tolerations = tolerations[1:]  # remove first
        tolerations[0].key = "something.else/unreachable"  # adjust second
        tolerations.append(tolerations[0])  # duplicate the second to test de-dupe
        return tolerations

    t1 = dict(key="node-role.kubernetes.io/not-ready", operator="Exists", effect="NoSchedule")
    t2 = dict(
        key="node-role.kubernetes.io/unreachable",
        operator="Exists",
        effect="NoSchedule",
    )
    spec = dict(
        template=dict(spec=dict(containers=[], tolerations=[t1, t2])),
        selector=dict(matchLabels=dict(app="myCoolApp")),
    )
    obj = from_dict(dict(apiVersion="apps/v1", kind="DaemonSet", spec=spec))
    update_tolerations(obj, adjuster)

    assert len(obj.spec.template.spec.tolerations) == 1, "The first toleration should be removed"
    assert obj.spec.template.spec.tolerations[0].key == "something.else/unreachable"


def test_subtraction_eq(manifest):
    rsc1 = from_dict(
        dict(
            apiVersion="v1",
            kind="ServiceAccount",
            metadata=dict(name="test-manifest-manager-1", namespace="kube-system"),
        )
    )

    rsc2 = from_dict(
        dict(
            apiVersion="v1",
            kind="ServiceAccount",
            metadata=dict(name="test-manifest-manager-2", namespace="kube-system"),
        )
    )

    adjustment1 = SubtractEq(manifest, rsc1)
    assert adjustment1(rsc1)
    assert not adjustment1(rsc2)


@pytest.mark.parametrize("secret_count", [0, 1, 2])
def test_custom_addition(manifest, request, secret_count):
    name = request.node.name

    class CustomAddition(Addition):
        def __call__(self):
            if secret_count == 0:
                # Simulate an addition that returns None when called
                return None
            if secret_count == 1:
                # Simulate an addition that returns a single object
                return from_dict(
                    dict(
                        apiVersion="v1",
                        kind="Secret",
                        metadata=dict(name=name),
                    )
                )
            # Simulate an addition that returns any iterable
            return [
                from_dict(
                    dict(
                        apiVersion="v1",
                        kind="Secret",
                        metadata=dict(name=f"{name}-{i}"),
                    )
                )
                for i in range(secret_count)
            ]

    manifest.manipulations.append(CustomAddition(manifest))
    resources = manifest.resources
    assert len([rsc for rsc in resources if name in rsc.name]) == secret_count


# RFC1123 Validation Tests


class TestValidateResourceName:
    """Test the validate_resource_name function."""

    def test_valid_simple_name(self):
        """Test that simple valid names pass validation."""
        from ops.manifests import validate_resource_name

        validate_resource_name("my-app", "StorageClass")
        validate_resource_name("app123", "StorageClass")
        validate_resource_name("123app", "StorageClass")
        validate_resource_name("a", "StorageClass")
        validate_resource_name("0", "StorageClass")

    def test_valid_name_with_dots(self):
        """Test that names with dots pass validation."""
        from ops.manifests import validate_resource_name

        validate_resource_name("my.app", "StorageClass")
        validate_resource_name("app.example.com", "StorageClass")
        validate_resource_name("storage.v1.class", "StorageClass")

    def test_valid_name_with_hyphens_and_dots(self):
        """Test that names with hyphens and dots pass validation."""
        from ops.manifests import validate_resource_name

        validate_resource_name("my-app.example", "StorageClass")
        validate_resource_name("cephfs-ceph-fs.data", "StorageClass")
        validate_resource_name("a-b-c.d-e-f", "StorageClass")

    def test_valid_max_length_name(self):
        """Test that 253 character names pass validation."""
        from ops.manifests import validate_resource_name

        long_name = "a" * 253
        validate_resource_name(long_name, "StorageClass")

    def test_invalid_empty_name(self):
        """Test that empty names fail validation."""
        from ops.manifests import NameValidationError, validate_resource_name

        with pytest.raises(NameValidationError, match="cannot be empty"):
            validate_resource_name("", "StorageClass")

    def test_invalid_none_name(self):
        """Test that None names fail validation."""
        from ops.manifests import NameValidationError, validate_resource_name

        with pytest.raises(NameValidationError, match="cannot be empty"):
            validate_resource_name(None, "StorageClass")

    def test_invalid_too_long_name(self):
        """Test that names over 253 characters fail validation."""
        from ops.manifests import NameValidationError, validate_resource_name

        long_name = "a" * 254
        with pytest.raises(NameValidationError, match="too long"):
            validate_resource_name(long_name, "StorageClass")

    def test_invalid_underscore_in_name(self):
        """Test that names with underscores fail validation."""
        from ops.manifests import NameValidationError, validate_resource_name

        with pytest.raises(NameValidationError, match="underscores"):
            validate_resource_name("my_app", "StorageClass")

        with pytest.raises(NameValidationError, match="underscores"):
            validate_resource_name("cephfs-fs_data", "StorageClass")

    def test_invalid_uppercase_in_name(self):
        """Test that names with uppercase letters fail validation."""
        from ops.manifests import NameValidationError, validate_resource_name

        with pytest.raises(NameValidationError, match="must begin"):
            validate_resource_name("MyApp", "StorageClass")

        with pytest.raises(NameValidationError, match="uppercase"):
            validate_resource_name("my-App", "StorageClass")

    def test_invalid_space_in_name(self):
        """Test that names with spaces fail validation."""
        from ops.manifests import NameValidationError, validate_resource_name

        with pytest.raises(NameValidationError, match="spaces"):
            validate_resource_name("my app", "StorageClass")

    def test_invalid_start_with_hyphen(self):
        """Test that names starting with hyphen fail validation."""
        from ops.manifests import NameValidationError, validate_resource_name

        with pytest.raises(NameValidationError, match="must begin"):
            validate_resource_name("-myapp", "StorageClass")

    def test_invalid_start_with_dot(self):
        """Test that names starting with dot fail validation."""
        from ops.manifests import NameValidationError, validate_resource_name

        with pytest.raises(NameValidationError, match="must begin"):
            validate_resource_name(".myapp", "StorageClass")

    def test_invalid_end_with_hyphen(self):
        """Test that names ending with hyphen fail validation."""
        from ops.manifests import NameValidationError, validate_resource_name

        with pytest.raises(NameValidationError, match="must end"):
            validate_resource_name("myapp-", "StorageClass")

    def test_invalid_end_with_dot(self):
        """Test that names ending with dot fail validation."""
        from ops.manifests import NameValidationError, validate_resource_name

        with pytest.raises(NameValidationError, match="must end"):
            validate_resource_name("myapp.", "StorageClass")

    def test_invalid_special_characters(self):
        """Test that names with special characters fail validation."""
        from ops.manifests import NameValidationError, validate_resource_name

        invalid_names = [
            "my@app",
            "my#app",
            "my$app",
            "my%app",
            "my&app",
            "my*app",
            "my+app",
            "my=app",
            "my[app",
            "my]app",
        ]
        for name in invalid_names:
            with pytest.raises(NameValidationError, match="invalid characters"):
                validate_resource_name(name, "StorageClass")

    def test_error_message_includes_resource_type(self):
        """Test that error messages include the resource type."""
        from ops.manifests import NameValidationError, validate_resource_name

        with pytest.raises(NameValidationError, match="ClusterRole"):
            validate_resource_name("invalid_name", "ClusterRole")

        with pytest.raises(NameValidationError, match="ConfigMap"):
            validate_resource_name("Invalid", "ConfigMap")


class TestGetValidationError:
    """Test the get_validation_error function."""

    def test_valid_name_returns_none(self):
        """Test that valid names return None."""
        from ops.manifests import get_validation_error

        assert get_validation_error("my-app", "StorageClass") is None
        assert get_validation_error("app.example", "StorageClass") is None

    def test_invalid_name_returns_error_string(self):
        """Test that invalid names return error messages."""
        from ops.manifests import get_validation_error

        error = get_validation_error("my_app", "StorageClass")
        assert error is not None
        assert "underscores" in error

        error = get_validation_error("my-App", "StorageClass")
        assert error is not None
        assert "uppercase" in error

    def test_empty_name_returns_error(self):
        """Test that empty names return error messages."""
        from ops.manifests import get_validation_error

        error = get_validation_error("", "StorageClass")
        assert error is not None
        assert "empty" in error

    def test_error_string_contains_name(self):
        """Test that error messages contain the invalid name."""
        from ops.manifests import get_validation_error

        error = get_validation_error("bad_name", "StorageClass")
        assert "bad_name" in error


class TestRealWorldScenarios:
    """Test real-world scenarios from the issue."""

    def test_cephfs_pool_with_underscore(self):
        """Test the specific scenario from the issue: fs_data pool name."""
        from ops.manifests import NameValidationError, get_validation_error, validate_resource_name

        # This is the problematic name from the issue
        invalid_name = "cephfs-ceph-fs-ceph-fs_data"

        with pytest.raises(NameValidationError, match="underscores"):
            validate_resource_name(invalid_name, "StorageClass")

        error = get_validation_error(invalid_name, "StorageClass")
        assert error is not None
        assert "fs_data" in invalid_name  # The pool name causes the issue
        assert "underscores" in error

    def test_valid_alternative_without_underscore(self):
        """Test that the corrected name (with hyphen) is valid."""
        from ops.manifests import get_validation_error, validate_resource_name

        # Corrected version with hyphen instead of underscore
        valid_name = "cephfs-ceph-fs-ceph-fs-data"
        validate_resource_name(valid_name, "StorageClass")
        assert get_validation_error(valid_name, "StorageClass") is None

    def test_various_pool_name_patterns(self):
        """Test various pool naming patterns that might appear."""
        from ops.manifests import NameValidationError, validate_resource_name

        # Valid patterns
        valid_patterns = [
            "cephfs-pool1",
            "cephfs-my-pool",
            "rbd-xfs-pool",
            "rbd-ext4-pool",
        ]
        for pattern in valid_patterns:
            validate_resource_name(pattern, "StorageClass")

        # Invalid patterns
        invalid_patterns = [
            "cephfs_pool1",  # underscore
            "CephFS-pool",  # uppercase
            "cephfs-pool_1",  # underscore
        ]
        for pattern in invalid_patterns:
            with pytest.raises(NameValidationError):
                validate_resource_name(pattern, "StorageClass")


class TestValidateResourceNamesPatch:
    """Test the ValidateResourceNames patch class."""

    @pytest.fixture
    def mock_manifests(self):
        """Create a mock manifests object."""
        return mock.MagicMock()

    @pytest.fixture
    def validator(self, mock_manifests):
        """Create a ValidateResourceNames instance."""
        from ops.manifests import ValidateResourceNames

        return ValidateResourceNames(mock_manifests)

    def test_valid_storage_class_name(self, validator):
        """Test that valid StorageClass names pass validation."""
        obj = from_dict(
            {
                "apiVersion": "storage.k8s.io/v1",
                "kind": "StorageClass",
                "metadata": {"name": "cephfs-pool"},
                "provisioner": "cephfs.csi.ceph.com",
            }
        )

        # Should not raise
        validator(obj)

    def test_invalid_storage_class_name_with_underscore(self, validator):
        """Test that StorageClass names with underscores fail validation."""
        obj = from_dict(
            {
                "apiVersion": "storage.k8s.io/v1",
                "kind": "StorageClass",
                "metadata": {"name": "cephfs-fs_data"},
                "provisioner": "cephfs.csi.ceph.com",
            }
        )

        with pytest.raises(ValueError, match="Invalid Kubernetes resource name"):
            validator(obj)

    def test_invalid_storage_class_name_with_uppercase(self, validator):
        """Test that StorageClass names with uppercase fail validation."""
        obj = from_dict(
            {
                "apiVersion": "storage.k8s.io/v1",
                "kind": "StorageClass",
                "metadata": {"name": "CephFS-pool"},
                "provisioner": "cephfs.csi.ceph.com",
            }
        )

        with pytest.raises(ValueError, match="Invalid Kubernetes resource name"):
            validator(obj)

    def test_invalid_storage_class_name_ending_with_hyphen(self, validator):
        """Test that StorageClass names ending with hyphen fail validation."""
        obj = from_dict(
            {
                "apiVersion": "storage.k8s.io/v1",
                "kind": "StorageClass",
                "metadata": {"name": "cephfs-pool-"},
                "provisioner": "cephfs.csi.ceph.com",
            }
        )

        with pytest.raises(ValueError, match="Invalid Kubernetes resource name"):
            validator(obj)

    def test_object_without_metadata(self, validator):
        """Test that objects without metadata are skipped."""
        obj = mock.MagicMock()
        obj.metadata = None

        # Should not raise
        validator(obj)

    def test_object_without_name(self, validator):
        """Test that objects without name are skipped."""
        obj = mock.MagicMock()
        obj.metadata = mock.MagicMock()
        obj.metadata.name = None

        # Should not raise
        validator(obj)

    def test_valid_service_name(self, validator):
        """Test that valid Service names pass validation."""
        obj = from_dict(
            {
                "apiVersion": "v1",
                "kind": "Service",
                "metadata": {"name": "my-service"},
                "spec": {"selector": {"app": "myapp"}},
            }
        )

        # Should not raise
        validator(obj)

    def test_invalid_configmap_name(self, validator):
        """Test that invalid ConfigMap names fail validation."""
        obj = from_dict(
            {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {"name": "my_config"},
                "data": {"key": "value"},
            }
        )

        with pytest.raises(ValueError, match="Invalid Kubernetes resource name"):
            validator(obj)
