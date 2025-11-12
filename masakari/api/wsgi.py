# Copyright (c) 2016 NTT DATA
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

"""Utility methods for working with WSGI servers."""

import os.path
import socket as socket_module
import sys

import cheroot.wsgi
from futurist import DynamicThreadPoolExecutor
from oslo_log import log as logging
from oslo_service import service
from oslo_utils import excutils
from paste import deploy
import routes.middleware
import webob.dec
import webob.exc

import masakari.conf
from masakari import exception
from masakari.i18n import _
from masakari import utils

CONF = masakari.conf.CONF

LOG = logging.getLogger(__name__)


class Server(service.ServiceBase):
    """Server class to manage a WSGI server, serving a WSGI application."""

    default_pool_size = CONF.wsgi.default_pool_size

    def __init__(self, name, app, host='0.0.0.0', port=0,
                 pool_size=None, backlog=128,
                 use_ssl=False, max_url_len=None):
        """Initialize, but do not start, a WSGI server.

        :param name: Pretty name for logging.
        :param app: The WSGI application to serve.
        :param host: IP address to serve the application.
        :param port: Port number to server the application.
        :param pool_size: Maximum number of threads to spawn concurrently.
        :param backlog: Maximum number of queued connections.
        :param max_url_len: Maximum length of permitted URLs.
        :returns: None
        :raises: masakari.exception.InvalidInput
        """
        self.name = name
        self.app = app
        self._server = None
        self._httpd = None
        self.pool_size = pool_size or self.default_pool_size
        self._pool = DynamicThreadPoolExecutor(max_workers=self.pool_size)
        self._pool_shutdown = False  # Track if pool has been shutdown
        self._logger = logging.getLogger("masakari.%s.wsgi.server" % self.name)
        self._use_ssl = use_ssl
        self._max_url_len = max_url_len

        self.client_socket_timeout = CONF.wsgi.client_socket_timeout or None

        # Store host and port - cheroot will handle socket binding
        self.host = host
        self.port = port

        # Validate backlog parameter even though cheroot handles it
        if backlog < 1:
            raise exception.InvalidInput(
                reason=_('The backlog must be more than 0'))

        LOG.info("%(name)s configured for %(host)s:%(port)d",
                 {'name': self.name, 'host': self.host, 'port': self.port})

    def start(self):
        """Start serving a WSGI application.

        :returns: None
        """
        # Recreate thread pool if it has been shutdown to allow restart
        # DynamicThreadPoolExecutor cannot be reused after shutdown()
        # is called
        try:
            # Try to submit a dummy task to check if pool is still usable
            self._pool.submit(lambda: None)
        except RuntimeError:
            # Pool has been shutdown, recreate it
            LOG.info("Recreating thread pool for server restart")
            self._pool = DynamicThreadPoolExecutor(
                max_workers=self.pool_size)
            self._pool_shutdown = False

        try:
            # Wrap the WSGI app with URL length checking if
            # max_url_len is specified
            wsgi_app = self.app
            if self._max_url_len:
                wsgi_app = self._create_url_length_wrapper(
                    self.app, self._max_url_len)

            # Create cheroot WSGI server with proper configuration
            server_kwargs = {
                'bind_addr': (self.host, self.port),
                'wsgi_app': wsgi_app,
                'numthreads': self.pool_size,
                'server_name': self.name,
                'timeout': self.client_socket_timeout or 10,
                'shutdown_timeout': 1  # Faster shutdown for tests
            }
            self._httpd = cheroot.wsgi.Server(**server_kwargs)

            # Configure socket options to match original implementation
            self._configure_socket_options()

            # Configure SSL if enabled
            if self._use_ssl:
                self._configure_ssl()

            LOG.info("Starting cheroot WSGI server on %(host)s:%(port)d",
                    {'host': self.host, 'port': self.port})

            # Start the server in a background thread
            self._server = utils.spawn(self._httpd.start)

            # Update port if it was set to 0 (random port)
            if self.port == 0:
                # Wait for cheroot to bind and get the actual port
                import time
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

    def _configure_ssl(self):
        """Configure SSL for cheroot WSGI server."""
        ca_file = CONF.wsgi.ssl_ca_file
        cert_file = CONF.wsgi.ssl_cert_file
        key_file = CONF.wsgi.ssl_key_file

        # Validate SSL files exist
        if cert_file and not os.path.exists(cert_file):
            raise RuntimeError(
                _("Unable to find cert_file : %s") % cert_file)

        if ca_file and not os.path.exists(ca_file):
            raise RuntimeError(
                _("Unable to find ca_file : %s") % ca_file)

        if key_file and not os.path.exists(key_file):
            raise RuntimeError(
                _("Unable to find key_file : %s") % key_file)

        if self._use_ssl and (not cert_file or not key_file):
            raise RuntimeError(
                _("When running server in SSL mode, you must "
                  "specify both a cert_file and key_file "
                  "option value in your configuration file"))

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
                # Set SO_REUSEADDR (this is typically
                # set by cheroot by default)
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
                elif hasattr(self._httpd, 'bind_addr'):
                    # Try to find the socket after prepare
                    for attr in ['socket', 'sock', '_sock', 'listener']:
                        if hasattr(self._httpd, attr):
                            sock = getattr(self._httpd, attr)
                            if sock:
                                configure_socket(sock)
                                break

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
            if app:
                return app(environ, start_response)
            else:
                # No app provided, return a simple response
                status = '200 OK'
                headers = [('Content-Type', 'text/plain')]
                start_response(status, headers)
                return [b'OK']

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

            # Force aggressive shutdown for tests
            try:
                # Force shutdown the bus system
                if hasattr(self._httpd, 'bus') and self._httpd.bus:
                    self._httpd.bus.exit()

                # Force shutdown any remaining server components
                if hasattr(self._httpd, '_server') and self._httpd._server:
                    self._httpd._server.shutdown()

                # Clear the server reference
                self._httpd = None

            except Exception as e:
                LOG.debug("Exception during aggressive server shutdown: %s", e)

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
            # Clean up references aggressively
            if self._httpd is not None:
                try:
                    # Final cleanup attempt
                    if hasattr(self._httpd, 'bus') and self._httpd.bus:
                        self._httpd.bus.exit()
                except Exception as e:
                    LOG.debug(
                        f"Error during final cleanup of HTTP server bus: {e}")

            self._httpd = None
            self._server = None


class Request(webob.Request):
    def __init__(self, environ, *args, **kwargs):
        if CONF.wsgi.secure_proxy_ssl_header:
            scheme = environ.get(CONF.wsgi.secure_proxy_ssl_header)
            if scheme:
                environ['wsgi.url_scheme'] = scheme
        super(Request, self).__init__(environ, *args, **kwargs)


class Application(object):
    """Base WSGI application wrapper. Subclasses need to implement __call__."""

    @classmethod
    def factory(cls, global_config, **local_config):
        """Used for paste app factories in paste.deploy config files.

        Any local configuration (that is, values under the [app:APPNAME]
        section of the paste config) will be passed into the `__init__` method
        as kwargs.

        A hypothetical configuration would look like:

            [app:wadl]
            latest_version = 1.3
            paste.app_factory = masakari.api.fancy_api:Wadl.factory

        which would result in a call to the `Wadl` class as

            import masakari.api.fancy_api
            fancy_api.Wadl(latest_version='1.3')

        You could of course re-implement the `factory` method in subclasses,
        but using the kwarg passing it shouldn't be necessary.

        """
        return cls(**local_config)

    def __call__(self, environ, start_response):
        r"""Subclasses will probably want to implement __call__ like this:

        @webob.dec.wsgify(RequestClass=Request)
        def __call__(self, req):
          # Any of the following objects work as responses:

          # Option 1: simple string
          res = 'message\n'

          # Option 2: a nicely formatted HTTP exception page
          res = exc.HTTPForbidden(explanation='Nice try')

          # Option 3: a webob Response object (in case you need to play with
          # headers, or you want to be treated like an iterable, or ...)
          res = Response()
          res.app_iter = open('somefile')

          # Option 4: any wsgi app to be run next
          res = self.application

          # Option 5: you can get a Response object for a wsgi app, too, to
          # play with headers etc
          res = req.get_response(self.application)

          # You can then just return your response...
          return res
          # ... or set req.response and return None.
          req.response = res

        See the end of http://pythonpaste.org/webob/modules/dec.html
        for more info.

        """
        raise NotImplementedError(_('You must implement __call__'))


class Middleware(Application):
    """Base WSGI middleware.

    These classes require an application to be
    initialized that will be called next.  By default the middleware will
    simply call its wrapped app, or you can override __call__ to customize its
    behavior.

    """

    @classmethod
    def factory(cls, global_config, **local_config):
        """Used for paste app factories in paste.deploy config files.

        Any local configuration (that is, values under the [filter:APPNAME]
        section of the paste config) will be passed into the `__init__` method
        as kwargs.

        A hypothetical configuration would look like:

            [filter:analytics]
            redis_host = 127.0.0.1
            paste.filter_factory = masakari.api.analytics:Analytics.factory

        which would result in a call to the `Analytics` class as

            import masakari.api.analytics
            analytics.Analytics(app_from_paste, redis_host='127.0.0.1')

        You could of course re-implement the `factory` method in subclasses,
        but using the kwarg passing it shouldn't be necessary.

        """
        def _factory(app):
            return cls(app, **local_config)
        return _factory

    def __init__(self, application):
        self.application = application

    def process_request(self, req):
        """Called on each request.

        If this returns None, the next application down the stack will be
        executed. If it returns a response then that response will be returned
        and execution will stop here.

        """
        return None

    def process_response(self, response):
        """Do whatever you'd like to the response."""
        return response

    @webob.dec.wsgify(RequestClass=Request)
    def __call__(self, req):
        response = self.process_request(req)
        if response:
            return response
        response = req.get_response(self.application)
        return self.process_response(response)


class Debug(Middleware):
    """Helper class for debugging a WSGI application.

    Can be inserted into any WSGI application chain to get information
    about the request and response.

    """

    @webob.dec.wsgify(RequestClass=Request)
    def __call__(self, req):
        print(('*' * 40) + ' REQUEST ENVIRON')
        for key, value in req.environ.items():
            print(key, '=', value)
        print()
        resp = req.get_response(self.application)

        print(('*' * 40) + ' RESPONSE HEADERS')
        for (key, value) in resp.headers.items():
            print(key, '=', value)
        print()

        resp.app_iter = self.print_generator(resp.app_iter)

        return resp

    @staticmethod
    def print_generator(app_iter):
        """Iterator that prints the contents of a wrapper string."""
        print(('*' * 40) + ' BODY')
        for part in app_iter:
            sys.stdout.write(part)
            sys.stdout.flush()
            yield part
        print()


class Router(object):
    """WSGI middleware that maps incoming requests to WSGI apps."""

    def __init__(self, mapper):
        """Create a router for the given routes.Mapper.

        Each route in `mapper` must specify a 'controller', which is a
        WSGI app to call.  You'll probably want to specify an 'action' as
        well and have your controller be an object that can route
        the request to the action-specific method.

        Examples:
          mapper = routes.Mapper()
          sc = ServerController()

          # Explicit mapping of one route to a controller+action
          mapper.connect(None, '/svrlist', controller=sc, action='list')

          # Actions are all implicitly defined
          mapper.resource('server', 'servers', controller=sc)

          # Pointing to an arbitrary WSGI app.  You can specify the
          # {path_info:.*} parameter so the target app can be handed just that
          # section of the URL.
          mapper.connect(None, '/v1.0/{path_info:.*}', controller=BlogApp())

        """
        self.map = mapper
        self._router = routes.middleware.RoutesMiddleware(self._dispatch,
                                                          self.map)

    @webob.dec.wsgify(RequestClass=Request)
    def __call__(self, req):
        """Route the incoming request to a controller based on self.map.

        If no match, return a 404.

        """
        return self._router

    @staticmethod
    @webob.dec.wsgify(RequestClass=Request)
    def _dispatch(req):
        """Dispatch the request to the appropriate controller.

        Called by self._router after matching the incoming request to a route
        and putting the information into req.environ.  Either returns 404
        or the routed WSGI app's response.

        """
        match = req.environ['wsgiorg.routing_args'][1]
        if not match:
            return webob.exc.HTTPNotFound()
        app = match['controller']
        return app


class Loader(object):
    """Used to load WSGI applications from paste configurations."""

    def __init__(self, config_path=None):
        """Initialize the loader, and attempt to find the config.

        :param config_path: Full or relative path to the paste config.
        :returns: None

        """
        self.config_path = None

        config_path = config_path or CONF.wsgi.api_paste_config
        if not os.path.isabs(config_path):
            self.config_path = CONF.find_file(config_path)
        elif os.path.exists(config_path):
            self.config_path = config_path

        if not self.config_path:
            raise exception.ConfigNotFound(path=config_path)

    def load_app(self, name):
        """Return the paste URLMap wrapped WSGI application.

        :param name: Name of the application to load.
        :returns: Paste URLMap object wrapping the requested application.
        :raises: `masakari.exception.PasteAppNotFound`

        """
        try:
            LOG.debug("Loading app %(name)s from %(path)s",
                      {'name': name, 'path': self.config_path})
            return deploy.loadapp("config:%s" % self.config_path, name=name)
        except LookupError:
            LOG.exception("Couldn't lookup app: %s", name)
            raise exception.PasteAppNotFound(name=name, path=self.config_path)
