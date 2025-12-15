#!/usr/bin/env python3
"""
Oslo.service integration adapter for threading-based WSGI server.

This module provides the necessary integration between oslo.service and
the new Cheroot-based WSGI server, maintaining compatibility with existing
OpenStack service management patterns.
"""

import signal
import threading
import time

from oslo_log import log as logging
from oslo_service import service
from oslo_utils import excutils

from masakari_wsgi_server import CherootWSGIServer

LOG = logging.getLogger(__name__)


class ThreadedWSGIService(service.ServiceBase):
    """
    Oslo.service integration for threading-based WSGI servers.

    This service adapter maintains compatibility with existing oslo.service
    patterns while providing the benefits of native threading.
    """

    def __init__(self, name, loader, host='0.0.0.0', port=0, pool_size=None,
                 use_ssl=False, max_url_len=None):
        """Initialize the threaded WSGI service.

        :param name: Service name for identification and logging
        :param loader: WSGI application loader
        :param host: Host address to bind to
        :param port: Port to bind to (0 for random port)
        :param pool_size: Number of threads in the pool
        :param use_ssl: Whether to enable SSL/TLS
        :param max_url_len: Maximum URL length to accept
        """
        self.name = name
        self.loader = loader
        self.host = host
        self.port = port
        self.pool_size = pool_size
        self.use_ssl = use_ssl
        self.max_url_len = max_url_len

        # Load the WSGI application
        self.app = self.loader.load_app(name)

        # Create the WSGI server
        self.server = CherootWSGIServer(
            name=name,
            app=self.app,
            host=host,
            port=port,
            pool_size=pool_size,
            use_ssl=use_ssl,
            max_url_len=max_url_len
        )

        # Service state management
        self._started = False
        self._stopping = False
        self._start_lock = threading.RLock()

        # Setup signal handlers for graceful shutdown
        self._setup_signal_handlers()

        LOG.info("Initialized ThreadedWSGIService '%s' on %s:%d",
                 name, host, port)

    def _setup_signal_handlers(self):
        """Setup signal handlers for graceful shutdown."""
        def signal_handler(signum, frame):
            LOG.info("Received signal %s, initiating graceful shutdown", signum)
            self.stop()

        # Register handlers for common shutdown signals
        for sig in [signal.SIGTERM, signal.SIGINT]:
            try:
                signal.signal(sig, signal_handler)
            except (ValueError, OSError):
                # Signal handling may not be available in some environments
                LOG.debug("Could not register signal handler for signal %s", sig)

    def start(self):
        """Start the WSGI service.

        This method is called by oslo.service when the service should begin
        accepting connections.
        """
        with self._start_lock:
            if self._started:
                LOG.warning("Service '%s' already started", self.name)
                return

            if self._stopping:
                LOG.error("Cannot start service '%s' while stopping", self.name)
                return

            try:
                LOG.info("Starting ThreadedWSGIService '%s'", self.name)

                # Start the underlying WSGI server
                self.server.start()

                # Update port if it was randomly assigned
                if self.port == 0:
                    self.port = self.server.port

                self._started = True

                LOG.info("ThreadedWSGIService '%s' started successfully on %s:%d",
                         self.name, self.host, self.port)

            except Exception:
                with excutils.save_and_reraise_exception():
                    LOG.exception("Failed to start ThreadedWSGIService '%s'",
                                  self.name)

    def stop(self, graceful=False):
        """Stop the WSGI service.

        :param graceful: Whether to perform a graceful shutdown
        """
        with self._start_lock:
            if not self._started:
                LOG.debug("Service '%s' not started, nothing to stop", self.name)
                return

            if self._stopping:
                LOG.debug("Service '%s' already stopping", self.name)
                return

            self._stopping = True

            try:
                LOG.info("Stopping ThreadedWSGIService '%s' (graceful=%s)",
                         self.name, graceful)

                if graceful:
                    # Allow some time for requests to complete
                    self._graceful_stop()
                else:
                    # Immediate stop
                    self.server.stop()

                self._started = False
                self._stopping = False

                LOG.info("ThreadedWSGIService '%s' stopped successfully",
                         self.name)

            except Exception:
                with excutils.save_and_reraise_exception():
                    LOG.exception("Error stopping ThreadedWSGIService '%s'",
                                  self.name)
                    self._started = False
                    self._stopping = False

    def _graceful_stop(self):
        """Perform a graceful shutdown of the service."""
        # Get metrics to see current load
        metrics = self.server.get_metrics()
        active_threads = metrics.get('active_threads', 0)

        if active_threads > 0:
            LOG.info("Graceful shutdown: waiting for %d active threads to complete",
                     active_threads)

            # Wait up to 30 seconds for threads to complete
            timeout = 30
            start_time = time.time()

            while time.time() - start_time < timeout:
                metrics = self.server.get_metrics()
                active_threads = metrics.get('active_threads', 0)

                if active_threads == 0:
                    LOG.info("All threads completed, proceeding with shutdown")
                    break

                LOG.debug("Waiting for %d threads to complete...", active_threads)
                time.sleep(1)
            else:
                LOG.warning("Graceful shutdown timeout reached, forcing stop")

        # Stop the server
        self.server.stop()

    def wait(self):
        """Block until the service has stopped."""
        if self._started:
            try:
                self.server.wait()
            except Exception:
                with excutils.save_and_reraise_exception():
                    LOG.exception("Error waiting for ThreadedWSGIService '%s'",
                                  self.name)

    def reset(self):
        """Reset the service to default configuration."""
        LOG.info("Resetting ThreadedWSGIService '%s'", self.name)
        self.server.reset()

    def get_port(self):
        """Get the port number the service is listening on."""
        return self.port

    def get_host(self):
        """Get the host address the service is listening on."""
        return self.host

    def get_metrics(self):
        """Get service metrics for monitoring."""
        base_metrics = {
            'service_name': self.name,
            'service_started': self._started,
            'service_stopping': self._stopping,
            'service_host': self.host,
            'service_port': self.port,
        }

        # Add WSGI server metrics
        if hasattr(self.server, 'get_metrics'):
            base_metrics.update(self.server.get_metrics())

        return base_metrics


class ThreadedWSGIServiceManager(service.ServiceLauncher):
    """
    Service manager for threading-based WSGI services.

    This extends oslo.service's ServiceLauncher to provide specific
    functionality for managing threaded WSGI services.
    """

    def __init__(self, conf):
        super(ThreadedWSGIServiceManager, self).__init__(conf)
        self._wsgi_services = {}

    def launch_wsgi_service(self, name, loader, host='0.0.0.0', port=0,
                           pool_size=None, use_ssl=False, max_url_len=None):
        """Launch a new threaded WSGI service.

        :param name: Service name
        :param loader: WSGI application loader
        :param host: Host to bind to
        :param port: Port to bind to
        :param pool_size: Thread pool size
        :param use_ssl: Enable SSL
        :param max_url_len: Maximum URL length
        :returns: The launched service
        """
        service = ThreadedWSGIService(
            name=name,
            loader=loader,
            host=host,
            port=port,
            pool_size=pool_size,
            use_ssl=use_ssl,
            max_url_len=max_url_len
        )

        # Launch the service using oslo.service
        self.launch_service(service)

        # Keep track of WSGI services separately
        self._wsgi_services[name] = service

        return service

    def get_wsgi_service(self, name):
        """Get a WSGI service by name."""
        return self._wsgi_services.get(name)

    def get_all_wsgi_services(self):
        """Get all registered WSGI services."""
        return list(self._wsgi_services.values())

    def stop_wsgi_service(self, name, graceful=True):
        """Stop a specific WSGI service.

        :param name: Name of the service to stop
        :param graceful: Whether to perform graceful shutdown
        """
        service = self._wsgi_services.get(name)
        if service:
            service.stop(graceful=graceful)

    def get_service_metrics(self):
        """Get metrics for all managed WSGI services."""
        metrics = {}
        for name, service in self._wsgi_services.items():
            metrics[name] = service.get_metrics()
        return metrics


# Compatibility layer for existing code
class WSGIService(ThreadedWSGIService):
    """
    Compatibility wrapper that maintains the original WSGIService interface.

    This allows existing code to continue working without changes while
    benefiting from the new threading-based implementation.
    """

    def __init__(self, name, loader, host='0.0.0.0', port=0, **kwargs):
        # Map old eventlet-specific parameters to new threading parameters
        pool_size = kwargs.pop('pool_size', None)
        backlog = kwargs.pop('backlog', 128)  # Not used in cheroot
        use_ssl = kwargs.pop('use_ssl', False)
        max_url_len = kwargs.pop('max_url_len', None)

        # Initialize the threaded service
        super(WSGIService, self).__init__(
            name=name,
            loader=loader,
            host=host,
            port=port,
            pool_size=pool_size,
            use_ssl=use_ssl,
            max_url_len=max_url_len
        )

        # Log compatibility usage
        LOG.info("Using threading-compatible WSGIService for '%s'", name)


def create_wsgi_service(name, loader, conf, **kwargs):
    """
    Factory function for creating WSGI services.

    This function provides a clean interface for creating WSGI services
    while hiding the implementation details of the threading migration.
    """
    # Extract configuration from conf if available
    if hasattr(conf, 'wsgi'):
        kwargs.setdefault('pool_size', getattr(conf.wsgi, 'default_pool_size', None))
        kwargs.setdefault('use_ssl', getattr(conf.wsgi, 'use_ssl', False))

    return ThreadedWSGIService(name, loader, **kwargs)


if __name__ == '__main__':
    # Example usage and testing
    import logging
    from oslo_config import cfg

    logging.basicConfig(level=logging.INFO)

    # Mock WSGI application loader
    class MockLoader:
        def load_app(self, name):
            from masakari_wsgi_server import SimpleApp
            return SimpleApp()

    # Create and test the service
    conf = cfg.ConfigOpts()
    loader = MockLoader()

    service_manager = ThreadedWSGIServiceManager(conf)

    try:
        # Launch a test service
        service = service_manager.launch_wsgi_service(
            name='test-api',
            loader=loader,
            host='127.0.0.1',
            port=8080
        )

        print(f"Service started on {service.get_host()}:{service.get_port()}")
        print("Service metrics:", service.get_metrics())

        # Wait for a bit then stop
        time.sleep(2)
        service_manager.stop()

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()