# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import unittest.mock as mock
from collections import namedtuple
from pathlib import Path
from tempfile import NamedTemporaryFile

import httpx
import pytest

from ops.manifests import HashableResource, ManifestClientError, Manifests


def test_fail_load_crds(mock_load_in_cluster_generic_resources):
    mock_load_in_cluster_generic_resources.side_effect = httpx.ConnectError(
        "SSL: CERTIFICATE_VERIFY_FAILED"
    )
    model = mock.MagicMock(autospec="ops.model.Model")
    m1 = Manifests("m1", model, "tests/data/mock_manifests")
    with pytest.raises(ManifestClientError):
        m1.client


@pytest.mark.parametrize("namespace", [None, "default"])
def test_hashable_resource(namespace):
    rsc_obj = mock.MagicMock()
    kind = type(rsc_obj).__name__
    rsc_obj.kind = kind
    rsc_obj.metadata.name = "test-resource"
    rsc_obj.metadata.namespace = namespace
    hr = HashableResource(rsc_obj)
    assert str(hr) == f"{kind}/{namespace+'/' if namespace else ''}test-resource"

    hr2 = HashableResource(rsc_obj)
    assert hr == hr2
    assert len({hr, hr2}) == 1


def test_manifest():
    model = mock.MagicMock(autospec="ops.model.Model")
    m1 = Manifests("m1", model, "tests/data/mock_manifests")
    m2 = Manifests("m2", model, "tests/data/mock_manifests")
    assert m1.name != m2.name


def test_manifest_without_config():
    model = mock.MagicMock(autospec="ops.model.Model")
    m1 = Manifests("m1", model, "tests/data/mock_manifests")
    with pytest.raises(NotImplementedError):
        _ = m1.config


def test_releases(manifest):
    assert (
        manifest.default_release == "v0.2"
    )  # as defined by tests/data/mock_manifests/version
    assert (
        manifest.latest_release == "v0.3.1"
    )  # as defined by tests/data/mock_manifests sort order
    assert manifest.releases == ["v0.3.1", "v0.2"]


def test_current_release(manifest):
    manifest.data["release"] = None
    assert (
        manifest.current_release == "v0.2"
    )  # as defined by tests/data/mock_manifests/version

    manifest.data["release"] = "v0.1"
    assert manifest.current_release == "v0.1"  # as defined by config

    with mock.patch.object(
        manifest, "default_release", new_callable=mock.PropertyMock(return_value=None)
    ):
        manifest.default_release == "v0.3.1"  # absence of a default_release


@pytest.mark.parametrize("release, uniqs", [("v0.1", 0), ("v0.2", 4), ("v0.3.1", 1)])
def test_resources_version(manifest, release, uniqs):
    manifest.data["release"] = release
    rscs = manifest.resources
    assert (
        len(rscs) == uniqs
    ), f"{uniqs} unique namespace kind resources in {manifest.current_release}"
    if uniqs < 1:
        return

    assert len(rscs) > 0, "1 service account in kube-system namespace"
    element = next(rsc for rsc in rscs if rsc.kind == "ServiceAccount")
    assert element.namespace == "kube-system"
    assert element.name == "test-manifest-manager"
    assert element.kind == "ServiceAccount"


def mock_get_responder(klass, name, namespace=None):
    Condition = namedtuple("Condition", "status,type")
    response = mock.MagicMock(spec=klass)
    response.kind = klass.__name__
    response.metadata.name = name
    response.metadata.namespace = namespace
    if hasattr(response, "status"):
        response.status.conditions = [Condition("False", "Ready")]
    return response


def mock_list_responder(klass, namespace=None, labels=None):
    response = mock.MagicMock(spec=klass)
    response.kind = klass.__name__
    response.metadata.name = "mock-item"
    response.metadata.namespace = namespace
    response.metadata.labels = labels
    return [response]


def test_single_manipulations(manifest):
    manifest.data = {"image-registry": "rocks.canonical.com/cdk"}
    _ = manifest.resources
    second = manifest.resources
    for obj in second:
        if obj.kind == "Deployment":
            image_path = obj.resource.spec.template.spec.containers[0].image
            assert image_path.startswith("rocks.canonical.com/cdk")
            assert not image_path.startswith("rocks.canonical.com/cdk/cdk")


def test_status(manifest, lk_client):
    with mock.patch.object(lk_client, "get") as mock_get:
        mock_get.side_effect = mock_get_responder
        resource_status = manifest.status()
    assert mock_get.call_count == 4
    # Because mock_client.get.return_value returns the same for all 7 resources
    # The HashableResource is the same for each.
    assert len(resource_status) == 2


def test_apply_resource_empty(manifest, lk_client, caplog):
    manifest.apply_resource()
    assert lk_client.apply.call_count == 0
    assert caplog.messages == ["Applied 0 Resources"]


def test_apply_resources(manifest, lk_client, caplog):
    (secret,) = (_ for _ in manifest.resources if _.kind == "Secret")
    manifest.apply_resources(secret)
    assert lk_client.apply.call_count == 1
    assert caplog.messages == [
        "Applying Secret/kube-system/test-manifest-secret",
        "Applied 1 Resources",
    ]


def test_apply_manifests(manifest, lk_client, caplog):
    manifest.apply_manifests()
    assert lk_client.apply.call_count == 4
    assert caplog.messages == [
        "Applying test-manifest version: v0.2",
        "Applying ServiceAccount/kube-system/test-manifest-manager",
        "Applying Secret/kube-system/test-manifest-secret",
        "Applying Deployment/kube-system/test-manifest-deployment",
        "Applying CustomResourceDefinition/test-manifest-crd",
        "Applied 4 Resources",
    ]


def test_apply_api_error(manifest, lk_client, api_error_klass, caplog):
    lk_client.apply.side_effect = [mock.MagicMock(), api_error_klass]
    with pytest.raises(ManifestClientError) as mce:
        manifest.apply_manifests()
    assert isinstance(mce.value.__cause__, api_error_klass)
    assert lk_client.apply.call_count == 2
    assert caplog.messages == [
        "Applying test-manifest version: v0.2",
        "Applying ServiceAccount/kube-system/test-manifest-manager",
        "Applying Secret/kube-system/test-manifest-secret",
        "Failed Applying Secret/kube-system/test-manifest-secret",
    ]


def test_apply_http_error(manifest, lk_client, http_gateway_error, caplog):
    lk_client.apply.side_effect = [mock.MagicMock(), http_gateway_error]
    with pytest.raises(ManifestClientError) as mce:
        manifest.apply_manifests()
    assert mce.value.__cause__ is http_gateway_error
    assert lk_client.apply.call_count == 2
    assert caplog.messages == [
        "Applying test-manifest version: v0.2",
        "Applying ServiceAccount/kube-system/test-manifest-manager",
        "Applying Secret/kube-system/test-manifest-secret",
        "Failed Applying Secret/kube-system/test-manifest-secret",
    ]


def test_installed_resources(manifest, lk_client):
    with mock.patch.object(lk_client, "get") as mock_get:
        mock_get.side_effect = mock_get_responder
        rscs = manifest.installed_resources()
    assert mock_get.call_count == 4

    assert len(rscs) == 4, "4 installed resources"
    element = next(rsc for rsc in rscs if rsc.kind == "ServiceAccount")
    assert element.namespace == "kube-system"
    assert element.name == "test-manifest-manager"
    assert element.kind == "ServiceAccount"


def test_installed_resources_api_error(manifest, lk_client, api_error_klass):
    with mock.patch.object(lk_client, "get", side_effect=api_error_klass) as mock_get:
        rscs = manifest.installed_resources()
    assert mock_get.call_count == 4
    assert len(rscs) == 0, "No resources expected to be installed."


def test_installed_resources_http_error(manifest, lk_client, http_gateway_error):
    with mock.patch.object(
        lk_client, "get", side_effect=http_gateway_error
    ) as mock_get:
        rscs = manifest.installed_resources()
    assert mock_get.call_count == 4
    assert len(rscs) == 0, "No resources expected to be installed."


def test_labelled_resources(manifest, lk_client):
    with mock.patch.object(lk_client, "list") as mock_list:
        mock_list.side_effect = mock_list_responder
        rscs = manifest.labelled_resources()
    assert mock_list.call_count == 4

    assert len(rscs) > 1, "1 service account in kube-system namespace"
    element = next(rsc for rsc in rscs if rsc.kind == "ServiceAccount")
    assert element.namespace == "kube-system"
    assert element.name == "mock-item"
    assert element.kind == "ServiceAccount"


def test_delete_no_resources(manifest):
    with mock.patch.object(manifest, "_delete") as mock_delete:
        manifest.delete_resource()
    mock_delete.assert_not_called()


def test_delete_ignore_labels(manifest, lk_client, caplog):
    rscs = manifest.resources
    element = next(rsc for rsc in rscs if rsc.kind == "Secret")
    with mock.patch.object(lk_client, "delete") as mock_delete:
        manifest.delete_resource(element, ignore_labels=True)
    mock_delete.assert_called_once_with(
        type(element.resource),
        "test-manifest-secret",
        namespace="kube-system",
    )
    assert caplog.messages[0] == "Deleting Secret/kube-system/test-manifest-secret..."


def test_delete_observe_labels(manifest, lk_client, caplog):
    rscs = manifest.resources
    element = next(rsc for rsc in rscs if rsc.kind == "Secret")
    rsc_type = type(element.resource)
    rsc_name = "test-manifest-secret"
    rsc_ns = element.resource.metadata.namespace
    filter_labels = {
        "juju.io/application": "unit-testing",
        "juju.io/manifest": "test-manifest",
    }
    with mock.patch.object(lk_client, "delete") as mock_delete:
        with mock.patch.object(lk_client, "list") as mock_list:
            mock_list.return_value = [element.resource]
            manifest.delete_resource(element, ignore_labels=False)
    mock_delete.assert_called_once_with(rsc_type, rsc_name, namespace=rsc_ns)
    mock_list.assert_called_once_with(
        rsc_type,
        namespace=rsc_ns,
        labels=filter_labels,
        fields={"metadata.name": rsc_name},
    )
    assert caplog.messages[0] == "Deleting Secret/kube-system/test-manifest-secret..."


def test_delete_one_resource(manifest, caplog):
    rscs = manifest.resources
    element = next(rsc for rsc in rscs if rsc.kind == "Secret")
    with mock.patch.object(manifest, "_delete") as mock_delete:
        manifest.delete_resource(element)
    mock_delete.assert_called_once_with(element, "kube-system", False)
    assert caplog.messages[0] == "Deleting Secret/kube-system/test-manifest-secret..."


def test_delete_current_resources(manifest, caplog):
    with mock.patch.object(manifest, "_delete") as mock_delete:
        manifest.delete_manifests()
    assert len(caplog.messages) == 4, "Should delete the 4 resources in this release"
    assert all(msg.startswith("Deleting") for msg in caplog.messages)

    rscs = manifest.resources
    element = next(rsc for rsc in rscs if rsc.kind == "Secret")
    mock_delete.assert_any_call(element, "kube-system", False)


@pytest.mark.parametrize(
    "status",
    [
        "deleting an item that is not found",
        "(unauthorized) Sorry Dave, I cannot do that",
    ],
    ids=["Not found ignored", "Unauthorized ignored"],
)
def test_delete_resource_errors(manifest, api_error_klass, caplog, status):
    rscs = manifest.resources
    element = next(rsc for rsc in rscs if rsc.kind == "Secret")
    api_error_klass.status.message = status

    with mock.patch.object(manifest, "_delete") as mock_delete:
        mock_delete.side_effect = api_error_klass
        manifest.delete_resource(
            element, ignore_unauthorized=True, ignore_not_found=True
        )

    mock_delete.assert_called_once_with(element, "kube-system", False)
    obj = "Secret/kube-system/test-manifest-secret"
    assert caplog.messages[0] == f"Deleting {obj}..."
    assert caplog.messages[1] == f"Ignored failed delete of resource: {obj}"
    assert caplog.messages[2] == status


@pytest.mark.parametrize(
    "status",
    [
        "maybe the dingo ate your cloud-secret",
        None,
    ],
    ids=["Unignorable status", "No status message"],
)
def test_delete_resource_api_error(manifest, api_error_klass, caplog, status):
    rscs = manifest.resources
    element = next(rsc for rsc in rscs if rsc.kind == "Secret")
    api_error_klass.status.message = status

    with mock.patch.object(
        manifest, "_delete", side_effect=api_error_klass
    ) as mock_delete:
        with pytest.raises(ManifestClientError):
            manifest.delete_resource(
                element, ignore_unauthorized=True, ignore_not_found=True
            )
    mock_delete.assert_called_once_with(element, "kube-system", False)
    obj = "Secret/kube-system/test-manifest-secret"
    assert caplog.messages[0] == f"Deleting {obj}..."
    assert caplog.messages[1] == f"Failed to delete resource: {obj}"


def test_delete_resource_http_error(manifest, http_gateway_error, caplog):
    rscs = manifest.resources
    element = next(rsc for rsc in rscs if rsc.kind == "Secret")

    with mock.patch.object(
        manifest, "_delete", side_effect=http_gateway_error
    ) as mock_delete:
        with pytest.raises(ManifestClientError):
            manifest.delete_resource(
                element, ignore_unauthorized=True, ignore_not_found=True
            )
    mock_delete.assert_called_once_with(element, "kube-system", False)
    obj = "Secret/kube-system/test-manifest-secret"
    assert caplog.messages[0] == f"Deleting {obj}..."
    assert caplog.messages[1] == f"Failed to delete resource: {obj}"


@pytest.fixture()
def tmp_manifests(tmp_path):
    model = mock.MagicMock(autospec="ops.model.Model")
    invalid_manifest_path = Path(tmp_path / "manifests/v1")
    invalid_manifest_path.mkdir(parents=True)
    with mock.patch(
        "ops.manifests.Manifests.config",
        mock.PropertyMock(return_value={"release": "v1"}),
    ):
        yield Manifests("m1", model, tmp_path)


def test_non_dictionary_resource(tmp_manifests, caplog):
    caplog.set_level(logging.WARNING)
    path = tmp_manifests.base_path / "manifests" / tmp_manifests.current_release
    with NamedTemporaryFile(mode="w+t", dir=path, suffix=".yaml") as fp:
        fp.write("non-yaml")
        fp.flush()
        assert not tmp_manifests.resources
    assert caplog.messages == [
        f"Ignoring non-dictionary resource rsc='non-yaml' in {fp.name}"
    ]


def test_non_kubernetes_resource(tmp_manifests, caplog):
    caplog.set_level(logging.WARNING)
    path = tmp_manifests.base_path / "manifests" / tmp_manifests.current_release
    with NamedTemporaryFile(mode="w+t", dir=path, suffix=".yaml") as fp:
        fp.write("kind: Missing apiVersion")
        fp.flush()
        assert not tmp_manifests.resources
    rsc = "{'kind': 'Missing apiVersion'}"
    assert caplog.messages == [
        f"Ignoring non-kubernetes resource rsc='{rsc}' in {fp.name}"
    ]


def test_nested_kubernetes_resource(tmp_manifests, caplog):
    caplog.set_level(logging.WARNING)
    path = tmp_manifests.base_path / "manifests" / tmp_manifests.current_release
    with NamedTemporaryFile(mode="w+t", dir=path, suffix=".yaml") as fp:
        fp.write(
            """---
apiVersion: v1
kind: List
items:
- apiVersion: v1
  kind: List
  items:
  - apiVersion: v1
    kind: Pod
    metadata:
      name: test
      namespace: default
"""
        )
        fp.flush()
        assert len(tmp_manifests.resources) == 1
        (elem,) = tmp_manifests.resources
        assert elem.kind == "Pod"
        assert elem.name == "test"
        assert elem.namespace == "default"
