#!/usr/bin/env python3
"""
Prototype implementation of Masakari WSGI server using Cheroot instead of Eventlet.

This demonstrates the core migration from eventlet-based green threads to
native Python threading using Cheroot as the WSGI server.
"""

import os.path
import socket as socket_module
import sys
import time
import threading

import cheroot.wsgi
from futurist import DynamicThreadPoolExecutor
from oslo_log import log as logging
from oslo_service import service
from oslo_utils import excutils
import webob.dec
import webob.exc

# Mock configuration for prototype - in real implementation this would be oslo.config
class MockConf:
    class wsgi:
        default_pool_size = 100
        client_socket_timeout = 10
        ssl_ca_file = None
        ssl_cert_file = None
        ssl_key_file = None
        tcp_keepidle = 600
        secure_proxy_ssl_header = 'HTTP_X_FORWARDED_PROTO'

CONF = MockConf()

LOG = logging.getLogger(__name__)


class CherootWSGIServer(service.ServiceBase):
    """
    WSGI server implementation using Cheroot instead of Eventlet.

    This class provides a drop-in replacement for eventlet.wsgi with improved
    thread safety and performance characteristics.
    """

    default_pool_size = CONF.wsgi.default_pool_size

    def __init__(self, name, app, host='0.0.0.0', port=0,
                 pool_size=None, backlog=128,
                 use_ssl=False, max_url_len=None):
        """Initialize the Cheroot WSGI server.

        :param name: Pretty name for logging.
        :param app: The WSGI application to serve.
        :param host: IP address to serve the application.
        :param port: Port number to serve the application.
        :param pool_size: Maximum number of threads to spawn concurrently.
        :param backlog: Maximum number of queued connections.
        :param max_url_len: Maximum length of permitted URLs.
        :returns: None
        :raises: ValueError for invalid input
        """
        self.name = name
        self.app = app
        self._server = None
        self._httpd = None
        self.pool_size = pool_size or self.default_pool_size
        self._pool = DynamicThreadPoolExecutor(max_workers=self.pool_size)
        self._pool_shutdown = False
        self._logger = logging.getLogger("masakari.%s.wsgi.server" % self.name)
        self._use_ssl = use_ssl
        self._max_url_len = max_url_len

        # Thread-local storage for request context
        self._local = threading.local()

        self.client_socket_timeout = CONF.wsgi.client_socket_timeout or None

        # Store host and port - cheroot will handle socket binding
        self.host = host
        self.port = port

        # Validate backlog parameter
        if backlog < 1:
            raise ValueError("The backlog must be more than 0")

        LOG.info("%(name)s configured for %(host)s:%(port)d",
                 {'name': self.name, 'host': self.host, 'port': self.port})

    def start(self):
        """Start serving a WSGI application.

        :returns: None
        """
        # Recreate thread pool if it has been shutdown to allow restart
        if self._pool_shutdown or self._pool is None:
            LOG.info("Recreating thread pool for server restart")
            self._pool = DynamicThreadPoolExecutor(
                max_workers=self.pool_size)
            self._pool_shutdown = False

        try:
            # Wrap the WSGI app with URL length checking if specified
            wsgi_app = self.app
            if self._max_url_len:
                wsgi_app = self._create_url_length_wrapper(
                    self.app, self._max_url_len)

            # Add context preservation wrapper
            wsgi_app = self._create_context_wrapper(wsgi_app)

            # Create cheroot WSGI server with proper configuration
            server_kwargs = {
                'bind_addr': (self.host, self.port),
                'wsgi_app': wsgi_app,
                'numthreads': self.pool_size,
                'server_name': self.name,
                'timeout': self.client_socket_timeout or 10,
                'shutdown_timeout': 1
            }
            self._httpd = cheroot.wsgi.Server(**server_kwargs)

            # Configure socket options to match original implementation
            self._configure_socket_options()

            # Configure SSL if enabled
            if self._use_ssl:
                self._configure_ssl()

            LOG.info("Starting cheroot WSGI server on %(host)s:%(port)d",
                    {'host': self.host, 'port': self.port})

            # Start the server (this will run in background)
            self._server = self._pool.submit(self._httpd.start)

            # Update port if it was set to 0 (random port)
            if self.port == 0:
                # Wait for cheroot to bind and get the actual port
                max_attempts = 50  # 5 seconds max
                for attempt in range(max_attempts):
                    if (hasattr(self._httpd, 'bind_addr') and
                        self._httpd.bind_addr and
                            self._httpd.bind_addr[1] != 0):
                        self.port = self._httpd.bind_addr[1]
                        LOG.info("Server bound to random port: %d", self.port)
                        break
                    time.sleep(0.1)
                else:
                    LOG.warning(
                        "Could not determine bound port after 5 seconds")

        except Exception as e:
            LOG.error(
                "Failed to start %(name)s on %(host)s:%(port)d: %(error)s",
                {'name': self.name, 'host': self.host,
                 'port': self.port, 'error': e})
            raise

    def _create_context_wrapper(self, app):
        """Create a WSGI middleware that preserves thread-local context."""
        def context_wrapper(environ, start_response):
            # Initialize thread-local context for each request
            if not hasattr(self._local, 'context'):
                self._local.context = {}

            # Store request ID for context tracking
            request_id = environ.get('HTTP_X_REQUEST_ID',
                                   'req-%s' % threading.current_thread().ident)
            self._local.context['request_id'] = request_id

            try:
                return app(environ, start_response)
            finally:
                # Clean up thread-local context after request
                if hasattr(self._local, 'context'):
                    self._local.context.clear()

        return context_wrapper

    def _configure_ssl(self):
        """Configure SSL for cheroot WSGI server."""
        ca_file = CONF.wsgi.ssl_ca_file
        cert_file = CONF.wsgi.ssl_cert_file
        key_file = CONF.wsgi.ssl_key_file

        # Validate SSL files exist
        if cert_file and not os.path.exists(cert_file):
            raise RuntimeError(
                "Unable to find cert_file : %s" % cert_file)

        if ca_file and not os.path.exists(ca_file):
            raise RuntimeError(
                "Unable to find ca_file : %s" % ca_file)

        if key_file and not os.path.exists(key_file):
            raise RuntimeError(
                "Unable to find key_file : %s" % key_file)

        if self._use_ssl and (not cert_file or not key_file):
            raise RuntimeError(
                "When running server in SSL mode, you must "
                "specify both a cert_file and key_file "
                "option value in your configuration file")

        try:
            # Configure cheroot's built-in SSL support
            import cheroot.ssl.builtin
            ssl_adapter = cheroot.ssl.builtin.BuiltinSSLAdapter(
                certificate=cert_file,
                private_key=key_file
            )

            # Add CA file if specified
            if ca_file:
                ssl_adapter.certificate_chain = ca_file

            self._httpd.ssl_adapter = ssl_adapter
            LOG.info("SSL configured for %(name)s with cert: %(cert)s",
                    {'name': self.name, 'cert': cert_file})

        except Exception:
            with excutils.save_and_reraise_exception():
                LOG.error("Failed to configure SSL for %(name)s on %(host)s"
                          ":%(port)d",
                          {'name': self.name, 'host': self.host,
                           'port': self.port})

    def _configure_socket_options(self):
        """Configure socket options for cheroot WSGI server."""
        if not self._httpd:
            return

        def configure_socket(sock):
            """Configure a socket with the required options."""
            if not sock:
                return

            try:
                # Set SO_REUSEADDR
                sock.setsockopt(
                    socket_module.SOL_SOCKET, socket_module.SO_REUSEADDR, 1)

                # Set SO_KEEPALIVE
                sock.setsockopt(
                    socket_module.SOL_SOCKET, socket_module.SO_KEEPALIVE, 1)

                # Set TCP_KEEPIDLE if available and configured
                if (hasattr(socket_module, 'TCP_KEEPIDLE') and
                    hasattr(CONF.wsgi, 'tcp_keepidle') and
                        CONF.wsgi.tcp_keepidle):
                    sock.setsockopt(socket_module.IPPROTO_TCP,
                                    socket_module.TCP_KEEPIDLE,
                                    CONF.wsgi.tcp_keepidle)

                LOG.debug("Socket options configured for %s", self.name)

            except Exception as e:
                LOG.debug("Could not configure socket options: %s", e)

        # Configure socket options using cheroot's prepare method
        original_prepare = getattr(self._httpd, 'prepare', None)
        if original_prepare:
            def prepare_with_socket_options():
                original_prepare()
                # Configure the main socket after it's created
                if hasattr(self._httpd, 'socket') and self._httpd.socket:
                    configure_socket(self._httpd.socket)

            self._httpd.prepare = prepare_with_socket_options

    def _create_url_length_wrapper(self, app, max_url_len):
        """Create a WSGI middleware that enforces URL length limits."""
        def url_length_middleware(environ, start_response):
            # Get the full URL from the request
            path_info = environ.get('PATH_INFO', '')
            query_string = environ.get('QUERY_STRING', '')

            # Construct the full URL path
            if query_string:
                full_url = path_info + '?' + query_string
            else:
                full_url = path_info

            # Check if URL exceeds the maximum length
            if len(full_url) > max_url_len:
                # Return 414 URI Too Large
                status = '414 URI Too Large'
                headers = [('Content-Type', 'text/plain')]
                start_response(status, headers)
                return [b'URI Too Large']

            # URL is acceptable, pass through to the actual app
            return app(environ, start_response)

        return url_length_middleware

    def reset(self):
        """Reset server thread pool size to default.

        :returns: None

        """
        LOG.info("Resetting WSGI thread pool size to default: %d",
                self.default_pool_size)
        # Shut down the old pool and create a new one with the default size
        if not self._pool_shutdown:
            self._pool.shutdown(wait=False)
        self._pool = DynamicThreadPoolExecutor(
            max_workers=self.default_pool_size)
        self._pool_shutdown = False
        self.pool_size = self.default_pool_size

    def stop(self):
        """Stop this server.

        This stops the WSGI server by shutting down the HTTP server.

        :returns: None

        """
        LOG.info("Stopping WSGI server.")

        if self._httpd is not None:
            # Cheroot provides clean shutdown
            self._httpd.stop()
            self._httpd = None

        if self._server is not None and not self._pool_shutdown:
            # Shutdown pool to stop new requests from being processed
            self._pool.shutdown(wait=False)
            self._pool_shutdown = True

    def wait(self):
        """Block, until the server has stopped.

        Waits on the server thread to finish, then returns.

        :returns: None

        """
        try:
            if self._server is not None:
                self._server.result()

                # Force shutdown the pool if not already shutdown
                if not self._pool_shutdown:
                    self._pool.shutdown(wait=False)
                    self._pool_shutdown = True

        except Exception as e:
            LOG.info("WSGI server has stopped: %s", e)
        finally:
            self._httpd = None
            self._server = None

    def get_metrics(self):
        """Get current server metrics for monitoring.

        :returns: Dictionary of server metrics
        """
        metrics = {
            'server_name': self.name,
            'host': self.host,
            'port': self.port,
            'pool_size': self.pool_size,
            'active_threads': getattr(self._pool, '_threads', 0),
            'pool_shutdown': self._pool_shutdown
        }

        if self._httpd:
            metrics.update({
                'server_running': hasattr(self._httpd, 'serving'),
                'bind_addr': getattr(self._httpd, 'bind_addr', None)
            })

        return metrics


# Example WSGI application for testing
class SimpleApp:
    """Simple WSGI application for testing the server."""

    @webob.dec.wsgify
    def __call__(self, req):
        return webob.Response("Hello from Cheroot WSGI Server!\n"
                              "Thread: %s\n"
                              "Request: %s %s\n" % (
                                  threading.current_thread().name,
                                  req.method,
                                  req.url))


def main():
    """Example usage of the CherootWSGIServer."""
    import logging
    logging.basicConfig(level=logging.INFO)

    app = SimpleApp()
    server = CherootWSGIServer("test-api", app, host="127.0.0.1", port=8080)

    try:
        print("Starting server on http://127.0.0.1:8080")
        server.start()
        print("Server started. Metrics:", server.get_metrics())
        server.wait()
    except KeyboardInterrupt:
        print("Shutting down...")
        server.stop()


if __name__ == '__main__':
    main()