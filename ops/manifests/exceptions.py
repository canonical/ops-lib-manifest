class ManifestBaseError(Exception):
    """
    Base Exception for manifest handling.
    """


class ManifestClientError(ManifestBaseError):
    """
    Error caused by kubernetes client.
    """
