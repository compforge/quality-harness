"""Compatibility imports; generic client ownership lives in harness-common."""

from harness_common.access.client import Client as Client
from harness_common.access.client import ClientManager as ClientManager
from harness_common.access.client import ClientProvider as ClientProvider
from harness_common.access.client import _ClientBorrower as _ClientBorrower
from harness_common.access.client import client_key as client_key
from harness_common.access.datasource import DataSource as DataSource
from harness_common.access.datasource import ServiceDataSource as ServiceDataSource
