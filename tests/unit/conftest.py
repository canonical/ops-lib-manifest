# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import unittest.mock as mock

import httpx
import pytest
from lightkube import ApiError
from lightkube.codecs import from_dict

from ops.charm import CharmBase
from ops.manifests import Manifests
from ops.manifests.manipulations import ConfigRegistry, ManifestLabel, SubtractEq
from ops.testing import Harness


@pytest.fixture
def mock_load_in_cluster_generic_resources():
    with mock.patch("ops.manifests.manifest.load_in_cluster_generic_resources") as the_mock:
        yield the_mock


@pytest.fixture(autouse=True)
def lk_client(mock_load_in_cluster_generic_resources):
    with mock.patch("ops.manifests.manifest.Client", autospec=True) as mock_lightkube:
        yield mock_lightkube.return_value
    if mock_load_in_cluster_generic_resources.called:
        mock_load_in_cluster_generic_resources.assert_called_with(mock_lightkube.return_value)


@pytest.fixture()
def api_error_klass():
    class TestApiError(ApiError):
        status = mock.MagicMock()

        def __init__(self):
            pass

    yield TestApiError


@pytest.fixture()
def http_gateway_error():
    return httpx.HTTPStatusError(
        "502 Bad Gateway",
        request=httpx.Request("POST", "/any-path"),
        response=httpx.Response(502),
    )


@pytest.fixture
def harness():
    class UnitTestCharm(CharmBase):
        pass

    yield Harness(UnitTestCharm)


@pytest.fixture
def manifest(harness):
    remove_me = from_dict(
        dict(
            apiVersion="v1",
            kind="ConfigMap",
            metadata=dict(name="test-manifest-config-map", namespace="kube-system"),
        )
    )

    class TestManifests(Manifests):
        def __init__(self):
            self.data = {}
            super().__init__(
                "test-manifest",
                harness.model,
                "tests/data/mock_manifests",
                [
                    ManifestLabel(self),
                    SubtractEq(self, remove_me),
                    ConfigRegistry(self),
                ],
            )

        @property
        def config(self):
            return self.data

        def is_ready(self, obj, cond):
            if obj.kind == "CustomResourceDefinition" and cond.type == "Ignored":
                return None
            return super().is_ready(obj, cond)

    yield TestManifests()
