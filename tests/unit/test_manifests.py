# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import unittest.mock as mock
from collections import namedtuple

import pytest

from ops.manifests import HashableResource, Manifests, NamespaceKind


def test_namespace_kind():
    assert str(NamespaceKind("object", "default")) == "object/default"
    assert str(NamespaceKind("object", None)) == "object"


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
    m1 = Manifests("m1", "tests/data/mock_manifests")
    m2 = Manifests("m2", "tests/data/mock_manifests")
    assert m1.name != m2.name


def test_manifest_without_config():
    m1 = Manifests("m1", "tests/data/mock_manifests")
    with pytest.raises(NotImplementedError):
        _ = m1.config


@pytest.fixture
def manifest():
    class TestManifests(Manifests):
        def __init__(self):
            self.data = {}
            super().__init__(
                "test-manifest",
                "tests/data/mock_manifests",
                default_namespace="default",
            )

        @property
        def config(self):
            return self.data

    yield TestManifests()


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


@pytest.mark.parametrize("release, uniqs", [("v0.1", 1), ("v0.2", 4), ("v0.3.1", 2)])
def test_resources_version(manifest, release, uniqs):
    manifest.data["release"] = release
    rscs = manifest.resources
    assert (
        len(rscs) == uniqs
    ), f"{uniqs} unique namespace kind resources in {manifest.current_release}"
    if uniqs <= 1:
        return

    key = NamespaceKind("ServiceAccount", "kube-system")
    assert len(rscs[key]) == 1, "1 service account in kube-system namespace"
    element = next(iter(rscs[key]))
    assert element.namespace == "kube-system"
    assert element.name == "test-manifest-manager"
    assert element.kind == "ServiceAccount"


def mock_get_responder(klass, name, namespace=None):
    response = mock.MagicMock(spec=klass)
    response.kind = "ServiceAccount"
    response.metadata.name = name
    response.metadata.namespace = namespace
    return response


def mock_list_responder(klass, namespace=None, labels=None):
    response = mock.MagicMock(spec=klass)
    response.kind = "ServiceAccount"
    response.metadata.name = "mock-item"
    response.metadata.namespace = namespace
    response.metadata.labels = labels
    return [response]


def test_status(manifest):
    Condition = namedtuple("Condition", "status,type")
    with mock.patch.object(
        manifest, "client", new_callable=mock.PropertyMock
    ) as mock_client:
        resource = mock_client.get.return_value
        resource.kind = "ServiceAccount"
        resource.status.conditions = [Condition("False", "Ready")]
        resource_status = manifest.status()
    assert mock_client.get.call_count == 4
    # Because mock_client.get.return_value returns the same for all 7 resources
    # The HashableResource is the same for each.
    assert len(resource_status) == 1


def test_apply_resources(manifest, lk_client, caplog):
    manifest.apply_manifests()
    assert lk_client.apply.call_count == 4
    assert caplog.messages[0] == "Applying Namespace/default"
    assert (
        caplog.messages[1]
        == "Applying ServiceAccount/kube-system/test-manifest-manager"
    )
    assert caplog.messages[2] == "Applying Secret/kube-system/test-manifest-secret"
    assert (
        caplog.messages[3] == "Applying Deployment/kube-system/test-manifest-deployment"
    )


def test_expected_resources(manifest):
    with mock.patch.object(
        manifest, "client", new_callable=mock.PropertyMock
    ) as mock_client:
        mock_client.get.side_effect = mock_get_responder
        rscs = manifest.expected_resources()
    assert mock_client.get.call_count == 4

    key = NamespaceKind("ServiceAccount", "kube-system")
    assert len(rscs[key]) == 1, "1 service account in kube-system namespace"
    element = next(iter(rscs[key]))
    assert element.namespace == "kube-system"
    assert element.name == "test-manifest-manager"
    assert element.kind == "ServiceAccount"


def test_active_resources(manifest):
    with mock.patch.object(
        manifest, "client", new_callable=mock.PropertyMock
    ) as mock_client:
        mock_client.list.side_effect = mock_list_responder
        rscs = manifest.active_resources()
    assert mock_client.list.call_count == 4

    key = NamespaceKind("ServiceAccount", "kube-system")
    assert len(rscs[key]) == 1, "1 service account in kube-system namespace"
    element = next(iter(rscs[key]))
    assert element.namespace == "kube-system"
    assert element.name == "mock-item"
    assert element.kind == "ServiceAccount"


def test_delete_no_resources(manifest):
    with mock.patch.object(
        manifest, "client", new_callable=mock.PropertyMock
    ) as mock_client:
        manifest.delete_resource()
    mock_client.delete.assert_not_called()


def test_delete_one_resource(manifest, caplog):
    rscs = manifest.resources
    key = NamespaceKind("Secret", "kube-system")
    element = next(iter(rscs[key]))
    with mock.patch.object(
        manifest, "client", new_callable=mock.PropertyMock
    ) as mock_client:
        manifest.delete_resource(element)
    mock_client.delete.assert_called_once_with(
        type(element.resource), "test-manifest-secret", namespace="kube-system"
    )
    assert caplog.messages[0] == "Deleting Secret/kube-system/test-manifest-secret"


def test_delete_current_resources(manifest, caplog):
    with mock.patch.object(
        manifest, "client", new_callable=mock.PropertyMock
    ) as mock_client:
        manifest.delete_manifests()
    assert len(caplog.messages) == 4, "Should delete the 4 resources in this release"
    assert all(msg.startswith("Deleting") for msg in caplog.messages)

    rscs = manifest.resources
    key = NamespaceKind("Secret", "kube-system")
    element = next(iter(rscs[key]))
    mock_client.delete.assert_any_call(
        type(element.resource), "test-manifest-secret", namespace="kube-system"
    )


@pytest.mark.parametrize(
    "status, log_format",
    [
        ("deleting an item that is not found", "Ignoring not found error: {0}"),
        (
            "(unauthorized) Sorry Dave, I cannot do that",
            "Unauthorized error ignored: {0}",
        ),
    ],
    ids=["Not found ignored", "Unauthorized ignored"],
)
def test_delete_resource_errors(manifest, api_error_klass, caplog, status, log_format):
    rscs = manifest.resources
    key = NamespaceKind("Secret", "kube-system")
    element = next(iter(rscs[key]))
    api_error_klass.status.message = status

    with mock.patch.object(
        manifest, "client", new_callable=mock.PropertyMock
    ) as mock_client:
        mock_client.delete.side_effect = api_error_klass
        manifest.delete_resource(
            element, ignore_unauthorized=True, ignore_not_found=True
        )
    mock_client.delete.assert_called_once_with(
        type(element.resource), "test-manifest-secret", namespace="kube-system"
    )
    assert caplog.messages[0] == "Deleting Secret/kube-system/test-manifest-secret"
    assert caplog.messages[1] == log_format.format(status)


@pytest.mark.parametrize(
    "status, log_format",
    [
        (
            "maybe the dingo ate your cloud-secret",
            "ApiError encountered while attempting to delete resource: {0}",
        ),
        (None, "ApiError encountered while attempting to delete resource."),
    ],
    ids=["Unignorable status", "No status message"],
)
def test_delete_resource_raised(manifest, api_error_klass, caplog, status, log_format):
    rscs = manifest.resources
    key = NamespaceKind("Secret", "kube-system")
    element = next(iter(rscs[key]))
    api_error_klass.status.message = status

    with mock.patch.object(
        manifest, "client", new_callable=mock.PropertyMock
    ) as mock_client:
        mock_client.delete.side_effect = api_error_klass
        with pytest.raises(api_error_klass):
            manifest.delete_resource(
                element, ignore_unauthorized=True, ignore_not_found=True
            )
    mock_client.delete.assert_called_once_with(
        type(element.resource), "test-manifest-secret", namespace="kube-system"
    )
    assert caplog.messages[0] == "Deleting Secret/kube-system/test-manifest-secret"
    assert caplog.messages[1] == log_format.format(status)
