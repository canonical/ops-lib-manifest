import unittest.mock as mock
from lightkube.codecs import from_dict
from lightkube.models.core_v1 import Toleration

from ops.manifests import ConfigRegistry, ManifestLabel, update_toleration


def test_config_registry_none():
    manifest, obj = mock.MagicMock(), mock.MagicMock()
    manifest.config = {"image-registry": None}
    del obj.kind  # indicate to the mock object this attribute shouldn't be set

    adjustment = ConfigRegistry(manifest)
    adjustment(obj)
    assert not hasattr(obj, 'kind'), "Ensure it exits before assessing `obj.kind`"


def test_config_registry_of_pod():
    manifest = mock.MagicMock()
    manifest.config = {"image-registry": "rocks.canonical.com:443/cdk"}
    c1=dict(name="cool-pod", image="mcr.microsoft.com/awesome/image:1.0")
    c2=dict(name="other-pod", image="gcr.io/other/image:2.0")
    obj = from_dict(dict(apiVersion="v1", kind="Pod", spec=dict(containers=[c1,c2])))

    adjustment = ConfigRegistry(manifest)
    adjustment(obj)
    
    assert obj.spec.containers[0].image == "rocks.canonical.com:443/cdk/awesome/image:1.0"
    assert obj.spec.containers[1].image == "rocks.canonical.com:443/cdk/other/image:2.0"


def test_config_registry_of_daemonset():
    manifest = mock.MagicMock()
    manifest.config = {"image-registry": "rocks.canonical.com:443/cdk"}
    c1=dict(name="cool-pod", image="mcr.microsoft.com/awesome/image:1.0")
    c2=dict(name="other-pod", image="gcr.io/other/image:2.0")
    spec = dict(
        template=dict(spec=dict(containers=[c1,c2])),
        selector=dict(matchLabels=dict(app="myCoolApp")),
    )
    obj = from_dict(dict(apiVersion="apps/v1", kind="DaemonSet", spec=spec))

    adjustment = ConfigRegistry(manifest)
    adjustment(obj)
    
    assert obj.spec.template.spec.containers[0].image == "rocks.canonical.com:443/cdk/awesome/image:1.0"
    assert obj.spec.template.spec.containers[1].image == "rocks.canonical.com:443/cdk/other/image:2.0"


def test_manifest_label():
    manifest = mock.MagicMock()
    manifest.name = "my-manifest"
    obj = from_dict(dict(apiVersion="v1", kind="Secret"))

    adjustment = ManifestLabel(manifest)
    adjustment(obj)

    assert obj.metadata.labels == {manifest.name: "true"}


def test_update_pod_toleration():
    adjuster = mock.MagicMock(side_effect=[
        None,
        [Toleration(key="something.else/unreachable", operator="Exists", effect="NoSchedule")],
    ])
    t1=dict(key="node-role.kubernetes.io/not-ready", operator="Exists", effect="NoSchedule")
    t2=dict(key="node-role.kubernetes.io/unreachable", operator="Exists", effect="NoSchedule")
    obj = from_dict(dict(apiVersion="v1", kind="Pod", spec=dict(tolerations=[t1, t2], containers=[])))

    update_toleration(obj, adjuster)
    
    assert len(obj.spec.tolerations) == 1, "The first toleration should be removed"
    assert obj.spec.tolerations[0].key == "something.else/unreachable"


def test_update_deployment_toleration():
    adjuster = mock.MagicMock(side_effect=[
        None,
        [Toleration(key="something.else/unreachable", operator="Exists", effect="NoSchedule")],
    ])
    t1=dict(key="node-role.kubernetes.io/not-ready", operator="Exists", effect="NoSchedule")
    t2=dict(key="node-role.kubernetes.io/unreachable", operator="Exists", effect="NoSchedule")
    spec = dict(
        template=dict(spec=dict(containers=[], tolerations=[t1, t2])),
        selector=dict(matchLabels=dict(app="myCoolApp")),
    )
    obj = from_dict(dict(apiVersion="apps/v1", kind="DaemonSet", spec=spec))
    update_toleration(obj, adjuster)
    
    assert len(obj.spec.template.spec.tolerations) == 1, "The first toleration should be removed"
    assert obj.spec.template.spec.tolerations[0].key == "something.else/unreachable"
