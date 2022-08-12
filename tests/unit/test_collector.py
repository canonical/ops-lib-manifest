# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import unittest.mock as mock
from collections import namedtuple

import lightkube.codecs as codecs

from ops.manifests.collector import Collector
from ops.manifests.manifest import HashableResource


def test_collector_list_versions(manifest):
    event = mock.MagicMock()
    collector = Collector(manifest)
    collector.list_versions(event)
    event.set_results.assert_called_once_with(
        {"test-manifest-versions": "v0.3.1\nv0.2"}
    )


def test_collector_short_version(manifest):
    collector = Collector(manifest)
    assert collector.short_version == "v0.2"


def test_collector_long_version(manifest):
    collector = Collector(manifest)
    assert collector.long_version == "Versions: test-manifest=v0.2"


def test_collector_list_resources_all(manifest):
    event = mock.MagicMock()
    collector = Collector(manifest)
    collector.list_resources(event, None, None)
    event.set_results.assert_called_once_with(
        {
            "test-manifest-missing": "\n".join(
                [
                    "Deployment/kube-system/test-manifest-deployment",
                    "Secret/kube-system/test-manifest-secret",
                    "ServiceAccount/kube-system/test-manifest-manager",
                ]
            )
        }
    )


def test_collector_list_kind_filter(manifest):
    event = mock.MagicMock()
    collector = Collector(manifest)
    collector.list_resources(event, None, "deployment")
    event.set_results.assert_called_once_with(
        {"test-manifest-missing": "Deployment/kube-system/test-manifest-deployment"}
    )


def test_collector_list_manifest_filter(manifest):
    event = mock.MagicMock()
    collector = Collector(manifest)
    collector.list_resources(event, "garbage", None)
    event.set_results.assert_called_once_with({})


@mock.patch("ops.manifests.collector.Collector._list_resources")
def test_collector_scrub_resources(mock_list_resources, manifest, lk_client):
    resource = codecs.from_dict(
        dict(
            apiVersion="v1",
            kind="Namespace",
            metadata=dict(name="delete-me"),
        )
    )
    analysis = mock.MagicMock()
    analysis.extra = {HashableResource(resource)}
    mock_list_resources.return_value = {"test-manifest": analysis}

    event = mock.MagicMock()
    collector = Collector(manifest)
    collector.scrub_resources(event, None, None)

    assert mock_list_resources.call_count == 2
    mock_list_resources.assert_called_with(event, None, None)
    event.log.assert_called_once_with("Removing Namespace/delete-me")
    lk_client.delete.assert_called_once_with(
        type(resource), "delete-me", namespace=None
    )


@mock.patch("ops.manifests.collector.Collector._list_resources")
def test_collector_install_missing_resources(
    mock_list_resources, manifest, lk_client, caplog
):
    resource = codecs.from_dict(
        dict(
            apiVersion="v1",
            kind="Namespace",
            metadata=dict(name="install-me-im-missing"),
        )
    )
    analysis = mock.MagicMock()
    analysis.missing = {HashableResource(resource)}
    mock_list_resources.return_value = {"test-manifest": analysis}

    event = mock.MagicMock()
    collector = Collector(manifest)
    collector.apply_missing_resources(event, None, None)

    assert mock_list_resources.call_count == 2
    mock_list_resources.assert_called_with(event, None, None)
    event.log.assert_called_once_with("Applying Namespace/install-me-im-missing")
    assert lk_client.apply.call_count == 1
    assert caplog.messages == [
        "Applying Namespace/install-me-im-missing",
        "Applied 1 Resources",
    ]


def test_collector_unready(manifest, lk_client):
    def mock_get_responder(klass, name, namespace=None):
        Condition = namedtuple("Condition", "status,type")
        response = mock.MagicMock(spec=klass)
        response.kind = klass.__name__
        response.metadata.name = name
        response.metadata.namespace = namespace
        if hasattr(response, "status"):
            response.status.conditions = [Condition("False", "Ready")]
        return response

    collector = Collector(manifest)
    template = "test-manifest: {} is not Ready"
    with mock.patch.object(lk_client, "get") as mock_get:
        mock_get.side_effect = mock_get_responder
        assert collector.unready == [
            template.format("Deployment/kube-system/test-manifest-deployment"),
        ]
