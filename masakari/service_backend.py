# Copyright 2016 NTT DATA
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

"""Oslo.service backend initialization for Masakari."""

from oslo_log import log as logging
from oslo_service.backend import BackendType
from oslo_service.backend import exceptions
from oslo_service.backend import get_component
from oslo_service.backend import init_backend

import masakari.conf

CONF = masakari.conf.CONF
LOG = logging.getLogger(__name__)

# Flag to ensure backend is only initialized once
_backend_initialized = False


def initialize_service_backend():
    """Initialize oslo.service threading backend.

    This function initializes the oslo.service threading backend
    which provides native Python threading-based concurrency
    instead of eventlet green threads.

    :returns: None
    """
    global _backend_initialized

    if _backend_initialized:
        return

    try:
        # Try to initialize the threading backend
        LOG.info("Initializing oslo.service threading backend")
        init_backend(BackendType.THREADING)
        _backend_initialized = True
        LOG.info("Oslo.service threading backend initialized successfully")

    except exceptions.BackendAlreadySelected:
        # Backend already selected - continue with existing backend
        # This is normal in test environments or when oslo.service
        # is pre-initialized
        LOG.info("Oslo.service backend already initialized, "
                 "continuing with existing backend. "
                 "This is expected in test environments or "
                 "when services are restarted.")
        _backend_initialized = True


def get_process_launcher():
    """Get ProcessLauncher component from threading backend.

    :returns: ProcessLauncher class from threading backend
    """
    initialize_service_backend()
    return get_component("ProcessLauncher")


def get_service_launcher():
    """Get ServiceLauncher component from threading backend.

    :returns: ServiceLauncher class from threading backend
    """
    initialize_service_backend()
    return get_component("ServiceLauncher")


def launch_service(conf, service, workers=None):
    """Launch a service using the threading backend.

    :param conf: Configuration object
    :param service: Service instance to launch
    :param workers: Number of worker processes
    :returns: Launcher instance
    """
    initialize_service_backend()
    launch = get_component("launch")
    return launch(conf, service, workers=workers, restart_method='mutate')
