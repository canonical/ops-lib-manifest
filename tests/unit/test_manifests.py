# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import unittest.mock as mock
from collections import namedtuple

import pytest

from ops.manifests import HashableResource, Manifests


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


@pytest.mark.parametrize("release, uniqs", [("v0.1", 0), ("v0.2", 3), ("v0.3.1", 1)])
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


def test_status(manifest, lk_client):
    with mock.patch.object(lk_client, "get") as mock_get:
        mock_get.side_effect = mock_get_responder
        resource_status = manifest.status()
    assert mock_get.call_count == 3
    # Because mock_client.get.return_value returns the same for all 7 resources
    # The HashableResource is the same for each.
    assert len(resource_status) == 1


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
    assert lk_client.apply.call_count == 3
    assert caplog.messages == [
        "Applying test-manifest version: v0.2",
        "Applying ServiceAccount/kube-system/test-manifest-manager",
        "Applying Secret/kube-system/test-manifest-secret",
        "Applying Deployment/kube-system/test-manifest-deployment",
        "Applied 3 Resources",
    ]


def test_apply_api_error(manifest, lk_client, api_error_klass, caplog):
    lk_client.apply.side_effect = [mock.MagicMock(), api_error_klass]
    with pytest.raises(api_error_klass):
        manifest.apply_manifests()
    assert lk_client.apply.call_count == 2
    assert caplog.messages == [
        "Applying test-manifest version: v0.2",
        "Applying ServiceAccount/kube-system/test-manifest-manager",
        "Applying Secret/kube-system/test-manifest-secret",
        "Failed Applying Secret/kube-system/test-manifest-secret",
    ]


def test_apply_http_error(manifest, lk_client, http_gateway_error, caplog):
    lk_client.apply.side_effect = [mock.MagicMock(), http_gateway_error]
    with pytest.raises(type(http_gateway_error)):
        manifest.apply_manifests()
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
    assert mock_get.call_count == 3

    assert len(rscs) == 3, "3 installed resources"
    element = next(rsc for rsc in rscs if rsc.kind == "ServiceAccount")
    assert element.namespace == "kube-system"
    assert element.name == "test-manifest-manager"
    assert element.kind == "ServiceAccount"


def test_installed_resources_api_error(manifest, lk_client, api_error_klass):
    with mock.patch.object(lk_client, "get") as mock_get:
        mock_get.side_effect = api_error_klass
        rscs = manifest.installed_resources()
    assert mock_get.call_count == 3
    assert len(rscs) == 0, "No resources expected to be installed."


def test_installed_resources_http_error(manifest, lk_client, http_gateway_error):
    with mock.patch.object(lk_client, "get") as mock_get:
        mock_get.side_effect = http_gateway_error
        rscs = manifest.installed_resources()
    assert mock_get.call_count == 3
    assert len(rscs) == 0, "No resources expected to be installed."


def test_labelled_resources(manifest, lk_client):
    with mock.patch.object(lk_client, "list") as mock_list:
        mock_list.side_effect = mock_list_responder
        rscs = manifest.labelled_resources()
    assert mock_list.call_count == 3

    assert len(rscs) > 1, "1 service account in kube-system namespace"
    element = next(rsc for rsc in rscs if rsc.kind == "ServiceAccount")
    assert element.namespace == "kube-system"
    assert element.name == "mock-item"
    assert element.kind == "ServiceAccount"


def test_delete_no_resources(manifest, lk_client):
    with mock.patch.object(lk_client, "delete") as mock_delete:
        manifest.delete_resource()
    mock_delete.assert_not_called()


def test_delete_one_resource(manifest, lk_client, caplog):
    rscs = manifest.resources
    element = next(rsc for rsc in rscs if rsc.kind == "Secret")
    with mock.patch.object(lk_client, "delete") as mock_delete:
        manifest.delete_resource(element)
    mock_delete.assert_called_once_with(
        type(element.resource), "test-manifest-secret", namespace="kube-system"
    )
    assert caplog.messages[0] == "Deleting Secret/kube-system/test-manifest-secret"


def test_delete_current_resources(manifest, lk_client, caplog):
    with mock.patch.object(lk_client, "delete") as mock_delete:
        manifest.delete_manifests()
    assert len(caplog.messages) == 3, "Should delete the 3 resources in this release"
    assert all(msg.startswith("Deleting") for msg in caplog.messages)

    rscs = manifest.resources
    element = next(rsc for rsc in rscs if rsc.kind == "Secret")
    mock_delete.assert_any_call(
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
def test_delete_resource_errors(
    manifest, api_error_klass, lk_client, caplog, status, log_format
):
    rscs = manifest.resources
    element = next(rsc for rsc in rscs if rsc.kind == "Secret")
    api_error_klass.status.message = status

    with mock.patch.object(lk_client, "delete") as mock_delete:
        mock_delete.side_effect = api_error_klass
        manifest.delete_resource(
            element, ignore_unauthorized=True, ignore_not_found=True
        )

    mock_delete.assert_called_once_with(
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
def test_delete_resource_api_error(
    manifest, api_error_klass, caplog, status, log_format
):
    rscs = manifest.resources
    element = next(rsc for rsc in rscs if rsc.kind == "Secret")
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


def test_delete_resource_http_error(manifest, http_gateway_error, caplog):
    rscs = manifest.resources
    element = next(rsc for rsc in rscs if rsc.kind == "Secret")

    with mock.patch.object(
        manifest, "client", new_callable=mock.PropertyMock
    ) as mock_client:
        mock_client.delete.side_effect = http_gateway_error
        with pytest.raises(type(http_gateway_error)):
            manifest.delete_resource(
                element, ignore_unauthorized=True, ignore_not_found=True
            )
    mock_client.delete.assert_called_once_with(
        type(element.resource), "test-manifest-secret", namespace="kube-system"
    )
    assert caplog.messages[0] == "Deleting Secret/kube-system/test-manifest-secret"
    assert (
        caplog.messages[1]
        == "HTTPError encountered while attempting to delete resource."
    )
