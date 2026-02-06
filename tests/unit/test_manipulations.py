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
    """Test the validate_resource_name function.
    
    These tests verify our wrapper logic around the fqdn package.
    The fqdn package handles most RFC1123 validation, so we focus on:
    - Empty/None handling
    - Length limits
    - Lowercase requirement (our addition)
    - Trailing dot rejection (our addition)
    - Integration with real-world scenarios
    """

    @pytest.mark.parametrize("name", [
        "my-app",
        "app123",
        "a",
        "my.app",
        "app.example.com",
        "my-app.example.com",
    ])
    def test_valid_names(self, name):
        """Test that valid names pass validation."""
        from ops.manifests import validate_resource_name
        validate_resource_name(name, "StorageClass")

    def test_valid_max_length_name(self):
        """Test that 253 character names pass validation."""
        from ops.manifests import validate_resource_name

        # Create a valid 253 character name with proper label structure
        # 63 + 1 (dot) + 63 + 1 (dot) + 63 + 1 (dot) + 61 = 253
        label1 = "a" * 63
        label2 = "b" * 63
        label3 = "c" * 63
        label4 = "d" * 61
        long_name = f"{label1}.{label2}.{label3}.{label4}"
        assert len(long_name) == 253
        validate_resource_name(long_name, "StorageClass")

    @pytest.mark.parametrize("name,match", [
        ("", "cannot be empty"),
        (None, "cannot be empty"),
        ("a" * 254, "too long"),
    ])
    def test_invalid_basic_checks(self, name, match):
        """Test basic validation failures (empty, None, too long)."""
        from ops.manifests import NameValidationError, validate_resource_name

        with pytest.raises(NameValidationError, match=match):
            validate_resource_name(name, "StorageClass")

    @pytest.mark.parametrize("name", [
        "MyApp",
        "my-App",
        "myapp.",
        "my_app",
        "cephfs-fs_data",
    ])
    def test_invalid_rfc1123_violations(self, name):
        """Test RFC1123 validation failures (uppercase, trailing dot, underscore)."""
        from ops.manifests import NameValidationError, validate_resource_name

        with pytest.raises(NameValidationError, match="RFC1123"):
            validate_resource_name(name, "StorageClass")

    @pytest.mark.parametrize("resource_type,invalid_name", [
        ("ClusterRole", "invalid_name"),
        ("ConfigMap", "Invalid"),
    ])
    def test_error_message_includes_resource_type(self, resource_type, invalid_name):
        """Test that error messages include the resource type."""
        from ops.manifests import NameValidationError, validate_resource_name

        with pytest.raises(NameValidationError, match=resource_type):
            validate_resource_name(invalid_name, resource_type)


class TestGetValidationError:
    """Test the get_validation_error function."""

    @pytest.mark.parametrize("name", [
        "my-app",
        "app.example",
    ])
    def test_valid_name_returns_none(self, name):
        """Test that valid names return None."""
        from ops.manifests import get_validation_error
        assert get_validation_error(name, "StorageClass") is None

    @pytest.mark.parametrize("name,expected_substring", [
        ("my_app", "RFC1123"),
        ("my-App", "RFC1123"),
        ("", "empty"),
    ])
    def test_invalid_name_returns_error_string(self, name, expected_substring):
        """Test that invalid names return error messages."""
        from ops.manifests import get_validation_error

        error = get_validation_error(name, "StorageClass")
        assert error is not None
        assert expected_substring in error


class TestRealWorldScenarios:
    """Test real-world scenarios from the issue."""

    def test_cephfs_pool_with_underscore(self):
        """Test the specific scenario from the issue: fs_data pool name."""
        from ops.manifests import NameValidationError, get_validation_error, validate_resource_name

        # This is the problematic name from the issue
        invalid_name = "cephfs-ceph-fs-ceph-fs_data"

        with pytest.raises(NameValidationError, match="RFC1123"):
            validate_resource_name(invalid_name, "StorageClass")

        error = get_validation_error(invalid_name, "StorageClass")
        assert error is not None
        assert "fs_data" in invalid_name  # The pool name causes the issue
        assert "RFC1123" in error

    def test_valid_alternative_without_underscore(self):
        """Test that the corrected name (with hyphen) is valid."""
        from ops.manifests import get_validation_error, validate_resource_name

        # Corrected version with hyphen instead of underscore
        valid_name = "cephfs-ceph-fs-ceph-fs-data"
        validate_resource_name(valid_name, "StorageClass")
        assert get_validation_error(valid_name, "StorageClass") is None


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

    @pytest.mark.parametrize("kind,name", [
        ("StorageClass", "cephfs-pool"),
        ("Service", "my-service"),
    ])
    def test_valid_resource_names(self, validator, kind, name):
        """Test that valid resource names pass validation."""
        manifest_dict = {
            "apiVersion": "v1" if kind == "Service" else "storage.k8s.io/v1",
            "kind": kind,
            "metadata": {"name": name},
        }
        if kind == "StorageClass":
            manifest_dict["provisioner"] = "cephfs.csi.ceph.com"

        obj = from_dict(manifest_dict)

        # Should not raise
        validator(obj)

    @pytest.mark.parametrize("kind,name", [
        ("StorageClass", "cephfs-fs_data"),
        ("StorageClass", "CephFS-pool"),
        ("StorageClass", "cephfs-pool-"),
        ("ConfigMap", "my_config"),
    ])
    def test_invalid_resource_names(self, validator, kind, name):
        """Test that invalid resource names fail validation."""
        from ops.manifests import NameValidationError

        manifest_dict = {
            "apiVersion": "v1" if kind == "ConfigMap" else "storage.k8s.io/v1",
            "kind": kind,
            "metadata": {"name": name},
        }
        if kind == "StorageClass":
            manifest_dict["provisioner"] = "cephfs.csi.ceph.com"

        obj = from_dict(manifest_dict)

        with pytest.raises(NameValidationError, match="Invalid Kubernetes resource name"):
            validator(obj)

    def test_objects_without_metadata_are_skipped(self, validator):
        """Test that objects without metadata are skipped."""
        obj = mock.MagicMock()
        obj.metadata = None

        # Should not raise
        validator(obj)

    def test_objects_without_name_are_skipped(self, validator):
        """Test that objects without name are skipped."""
        obj = mock.MagicMock()
        obj.metadata = mock.MagicMock()
        obj.metadata.name = None

        # Should not raise
        validator(obj)
