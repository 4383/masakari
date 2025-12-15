#!/usr/bin/env python3
"""
Threading configuration management for Masakari WSGI server migration.

This module provides configuration options and utilities for managing
the transition from eventlet to threading-based WSGI serving.
"""

from oslo_config import cfg

# Threading-specific configuration options
wsgi_opts = [
    cfg.StrOpt('wsgi_server',
              default='cheroot',
              choices=['eventlet', 'cheroot'],
              help='WSGI server implementation to use. '
                   'eventlet uses green threads, cheroot uses native threads.'),

    cfg.IntOpt('default_pool_size',
              default=100,
              min=1,
              max=1000,
              help='Maximum number of threads in the WSGI thread pool. '
                   'Adjust based on expected concurrent connections and '
                   'server resources.'),

    cfg.IntOpt('max_workers',
              default=200,
              min=1,
              max=2000,
              help='Maximum number of worker threads that can be created '
                   'dynamically by the thread pool executor.'),

    cfg.IntOpt('client_socket_timeout',
              default=10,
              min=1,
              max=300,
              help='Timeout in seconds for client socket connections. '
                   'Connections idle longer than this will be closed.'),

    cfg.IntOpt('tcp_keepidle',
              default=600,
              min=1,
              max=7200,
              help='Time in seconds before sending keepalive probes '
                   'on TCP connections.'),

    cfg.StrOpt('thread_prefix',
              default='masakari-api-',
              help='Prefix for thread names to aid in debugging and '
                   'monitoring.'),

    cfg.IntOpt('shutdown_timeout',
              default=30,
              min=1,
              max=300,
              help='Maximum time in seconds to wait for graceful '
                   'shutdown of WSGI server.'),

    cfg.BoolOpt('enable_thread_metrics',
               default=True,
               help='Enable collection of threading metrics for '
                    'monitoring and debugging.'),

    cfg.IntOpt('thread_stack_size',
              default=0,
              min=0,
              help='Stack size in bytes for worker threads. '
                   '0 means use system default.'),
]

# RPC configuration updates for threading
rpc_opts = [
    cfg.StrOpt('executor',
              default='threading',
              choices=['eventlet', 'threading'],
              help='RPC executor type. Use "threading" when migrating '
                   'from eventlet to native threads.'),

    cfg.IntOpt('rpc_thread_pool_size',
              default=64,
              min=1,
              max=256,
              help='Size of RPC thread pool for handling incoming '
                   'RPC requests.'),
]

# Database configuration for threading compatibility
database_opts = [
    cfg.IntOpt('max_pool_size',
              default=10,
              min=1,
              max=100,
              help='Maximum number of database connections in pool. '
                   'Should be adjusted for threading workloads.'),

    cfg.IntOpt('max_overflow',
              default=20,
              min=0,
              max=100,
              help='Maximum number of connections that can overflow '
                   'the pool. Important for thread safety.'),

    cfg.BoolOpt('pool_pre_ping',
               default=True,
               help='Enable connection health checks before use. '
                    'Recommended for threaded environments.'),
]


def register_opts(conf):
    """Register all threading-related configuration options."""
    conf.register_opts(wsgi_opts, group='wsgi')
    conf.register_opts(rpc_opts, group='rpc')
    conf.register_opts(database_opts, group='database')


def get_wsgi_config():
    """Get WSGI configuration with threading optimizations."""
    return {
        'server_type': cfg.CONF.wsgi.wsgi_server,
        'pool_size': cfg.CONF.wsgi.default_pool_size,
        'max_workers': cfg.CONF.wsgi.max_workers,
        'socket_timeout': cfg.CONF.wsgi.client_socket_timeout,
        'tcp_keepidle': cfg.CONF.wsgi.tcp_keepidle,
        'thread_prefix': cfg.CONF.wsgi.thread_prefix,
        'shutdown_timeout': cfg.CONF.wsgi.shutdown_timeout,
        'enable_metrics': cfg.CONF.wsgi.enable_thread_metrics,
        'stack_size': cfg.CONF.wsgi.thread_stack_size,
    }


def get_rpc_config():
    """Get RPC configuration optimized for threading."""
    return {
        'executor': cfg.CONF.rpc.executor,
        'thread_pool_size': cfg.CONF.rpc.rpc_thread_pool_size,
    }


def get_database_config():
    """Get database configuration optimized for threading."""
    return {
        'max_pool_size': cfg.CONF.database.max_pool_size,
        'max_overflow': cfg.CONF.database.max_overflow,
        'pool_pre_ping': cfg.CONF.database.pool_pre_ping,
    }


class ThreadingConfigurationManager:
    """
    Manager for threading-related configuration validation and optimization.
    """

    def __init__(self, conf=None):
        self.conf = conf or cfg.CONF

    def validate_configuration(self):
        """Validate threading configuration for common issues."""
        issues = []

        # Check WSGI configuration
        wsgi_config = get_wsgi_config()

        if wsgi_config['pool_size'] > wsgi_config['max_workers']:
            issues.append(
                "WSGI pool_size (%d) should not exceed max_workers (%d)" % (
                    wsgi_config['pool_size'], wsgi_config['max_workers']))

        # Check RPC configuration compatibility
        rpc_config = get_rpc_config()

        if (wsgi_config['server_type'] == 'cheroot' and
            rpc_config['executor'] == 'eventlet'):
            issues.append(
                "Using cheroot WSGI server with eventlet RPC executor "
                "may cause compatibility issues. Consider using threading "
                "executor.")

        # Check database pool sizing
        db_config = get_database_config()
        total_threads = wsgi_config['max_workers'] + rpc_config['thread_pool_size']
        total_db_connections = db_config['max_pool_size'] + db_config['max_overflow']

        if total_db_connections < total_threads * 0.5:
            issues.append(
                "Database connection pool (%d total) may be too small "
                "for threading workload (%d total threads). Consider "
                "increasing max_pool_size or max_overflow." % (
                    total_db_connections, total_threads))

        return issues

    def get_optimization_recommendations(self):
        """Get recommendations for optimizing threading configuration."""
        recommendations = []

        wsgi_config = get_wsgi_config()

        # CPU core based recommendations
        try:
            import multiprocessing
            cpu_count = multiprocessing.cpu_count()

            if wsgi_config['pool_size'] > cpu_count * 4:
                recommendations.append(
                    "Consider reducing WSGI pool_size from %d to %d "
                    "(4x CPU cores) for better performance." % (
                        wsgi_config['pool_size'], cpu_count * 4))
        except:
            pass

        # Memory based recommendations
        if wsgi_config['stack_size'] == 0:
            recommendations.append(
                "Consider setting explicit thread_stack_size for "
                "better memory management in high-concurrency scenarios.")

        return recommendations

    def generate_config_sample(self):
        """Generate a sample configuration with threading optimizations."""
        return """
[wsgi]
# Threading-optimized WSGI configuration
wsgi_server = cheroot
default_pool_size = 100
max_workers = 200
client_socket_timeout = 10
tcp_keepidle = 600
thread_prefix = masakari-api-
shutdown_timeout = 30
enable_thread_metrics = true
thread_stack_size = 0

[rpc]
# Threading-compatible RPC configuration
executor = threading
rpc_thread_pool_size = 64

[database]
# Threading-optimized database configuration
max_pool_size = 20
max_overflow = 40
pool_pre_ping = true
"""


def migrate_eventlet_config():
    """
    Provide guidance for migrating from eventlet to threading configuration.
    """
    migration_guide = {
        'wsgi': {
            'changes': [
                "Change wsgi_server from 'eventlet' to 'cheroot'",
                "Increase default_pool_size if needed (eventlet was more efficient)",
                "Add thread-specific timeouts and limits",
            ],
            'new_options': [
                'max_workers', 'thread_prefix', 'enable_thread_metrics',
                'thread_stack_size'
            ]
        },
        'rpc': {
            'changes': [
                "Change executor from 'eventlet' to 'threading'",
                "Add rpc_thread_pool_size for dedicated RPC threads",
            ]
        },
        'database': {
            'changes': [
                "Increase max_pool_size for thread safety",
                "Add max_overflow for peak load handling",
                "Enable pool_pre_ping for connection health",
            ]
        }
    }

    return migration_guide


if __name__ == '__main__':
    # Example usage and validation
    from oslo_config import cfg

    conf = cfg.ConfigOpts()
    register_opts(conf)

    # Parse command line for configuration file
    import sys
    if len(sys.argv) > 1:
        conf(['--config-file', sys.argv[1]])
    else:
        # Use defaults
        conf([])

    manager = ThreadingConfigurationManager(conf)

    print("Threading Configuration Validation")
    print("=" * 50)

    issues = manager.validate_configuration()
    if issues:
        print("Configuration Issues Found:")
        for issue in issues:
            print(f"  - {issue}")
    else:
        print("No configuration issues found.")

    print("\nOptimization Recommendations:")
    recommendations = manager.get_optimization_recommendations()
    if recommendations:
        for rec in recommendations:
            print(f"  - {rec}")
    else:
        print("  No specific recommendations.")

    print("\nSample Configuration:")
    print(manager.generate_config_sample())