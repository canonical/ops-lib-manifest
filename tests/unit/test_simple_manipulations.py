from ops.manifests import ManifestLabel, Manifests, PatchEq, SubtractEq, CreateNamespace


def test_regex_matching(harness):
    class TestManifests(Manifests):
        def __init__(self):
            self.data = {}
            super().__init__(
                "test-manifest",
                harness.model,
                "tests/data/mock_manifests",
                [
                    SubtractEq("ConfigMap/kube-system/test-manifest-config-map"),
                    PatchEq(".*", ManifestLabel(self)),
                    CreateNamespace("another-ns"),
                ],
            )

        @property
        def config(self):
            return self.data

    manifest = TestManifests()
    assert len(manifest.resources) == 4
    for each in manifest.resources:
        assert each.resource.metadata and len(each.resource.metadata.labels) == 3
