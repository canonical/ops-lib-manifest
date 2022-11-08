# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import unittest.mock as mock

from lightkube.codecs import from_dict

from ops.manifests import (
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
        "Replacing Image: mcr.microsoft.com/awesome/image:1.0 with "
        f"{rocks}/awesome/image:1.0",
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
        obj.spec.template.spec.containers[1].image
        == "rocks.canonical.com:443/cdk/other/image:2.0"
    )


def test_config_registry_pod_with_init_container():
    manifest = mock.MagicMock()
    rocks = "rocks.canonical.com:443/cdk"
    manifest.config = {"image-registry": rocks}
    c1 = dict(name="cool-pod", image="mcr.microsoft.com/awesome/image:1.0")
    c2 = dict(name="other-pod", image="gcr.io/other/image:2.0")
    obj = from_dict(
        dict(
            apiVersion="v1", kind="Pod", spec=dict(containers=[c1], initContainers=[c2])
        )
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
    assert (
        obj.metadata.labels["pre-existing"] == "label"
    ), "Should leave existing labels alone"
    assert (
        obj.metadata.labels["juju.io/application"] == "unit-testing"
    ), "Should add the application label"
    assert (
        obj.metadata.labels["juju.io/manifest"] == "test-manifest"
    ), "Should add the manifest name"
    assert (
        obj.metadata.labels["juju.io/manifest-version"] == "test-manifest-v0.2"
    ), "Should add the manifest label with current-version"


def test_update_pod_toleration():
    def adjuster(tolerations):
        tolerations = tolerations[1:]  # remove first toleration
        tolerations[0].key = "something.else/unreachable"  # patch second toleration
        tolerations.append(tolerations[0])  # duplicate the second just to test dedupe
        return tolerations

    t1 = dict(
        key="node-role.kubernetes.io/not-ready", operator="Exists", effect="NoSchedule"
    )
    t2 = dict(
        key="node-role.kubernetes.io/unreachable",
        operator="Exists",
        effect="NoSchedule",
    )
    obj = from_dict(
        dict(
            apiVersion="v1", kind="Pod", spec=dict(tolerations=[t1, t2], containers=[])
        )
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

    t1 = dict(
        key="node-role.kubernetes.io/not-ready", operator="Exists", effect="NoSchedule"
    )
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

    assert (
        len(obj.spec.template.spec.tolerations) == 1
    ), "The first toleration should be removed"
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
