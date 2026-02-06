# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

APP_LABEL = "juju.io/application"
MANIFEST_LABEL = "juju.io/manifest"
MANIFEST_VERSION_LABEL = "juju.io/manifest-version"

# RFC1123 validation constants
LOWERCASE_LETTERS = frozenset("abcdefghijklmnopqrstuvwxyz")
DIGITS = frozenset("0123456789")
SEPARATORS = frozenset("-.")
ALPHANUMERIC_LOWER = LOWERCASE_LETTERS | DIGITS
VALID_NAME_CHARS = ALPHANUMERIC_LOWER | SEPARATORS
MAX_NAME_LENGTH = 253
