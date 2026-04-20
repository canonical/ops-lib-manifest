class ManifestBaseError(Exception):
    """
    Base Exception for manifest handling.
    """


class ManifestClientError(ManifestBaseError):
    """
    Error caused by kubernetes client.
    """


class ManifestReleaseError(ManifestBaseError):
    """
    Error caused by an invalid release.
    """
