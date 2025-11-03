#!/usr/bin/env python
"""
Comprehensive test cleanup utilities to prevent hanging tests.
"""

import gc
import threading
import time
import signal
import os


def aggressive_cleanup():
    """Perform aggressive cleanup of all threading resources."""

    # 1. Cleanup our global executors
    try:
        from masakari import utils
        utils._cleanup_global_executors()
    except Exception:
        pass

    # 2. Cleanup oslo.service resources
    try:
        # Reset oslo.service backend state
        import oslo_service.service as oslo_service
        if hasattr(oslo_service, '_launcher'):
            oslo_service._launcher = None
        if hasattr(oslo_service, '_backend'):
            oslo_service._backend = None
    except Exception:
        pass

    # 3. Cleanup any remaining ThreadPoolExecutors globally
    try:
        import concurrent.futures
        for obj in gc.get_objects():
            if isinstance(obj, (concurrent.futures.ThreadPoolExecutor,)):
                try:
                    obj.shutdown(wait=False)
                except Exception:
                    pass
    except Exception:
        pass

    # 4. Cleanup futurist executors
    try:
        from futurist import DynamicThreadPoolExecutor
        for obj in gc.get_objects():
            if isinstance(obj, DynamicThreadPoolExecutor):
                try:
                    obj.shutdown(wait=False)
                except Exception:
                    pass
    except Exception:
        pass

    # 5. Force garbage collection multiple times
    for _ in range(3):
        gc.collect()
        time.sleep(0.1)

    # 6. Print thread status for debugging
    active_threads = threading.enumerate()
    daemon_threads = [t for t in active_threads if t.daemon]
    non_daemon_threads = [t for t in active_threads if not t.daemon and t != threading.current_thread()]

    if len(non_daemon_threads) > 0:
        print(f"⚠️  {len(non_daemon_threads)} non-daemon threads still running:")
        for t in non_daemon_threads:
            print(f"   - {t.name} ({t.__class__.__name__})")

    if len(daemon_threads) > 10:  # More than expected daemon threads
        print(f"⚠️  {len(daemon_threads)} daemon threads running (might be excessive)")


def cleanup_after_service_tests():
    """Specific cleanup for service-related tests."""
    aggressive_cleanup()

    # Additional service-specific cleanup
    try:
        from masakari import service_backend
        # Reset service backend state
        service_backend._backend_initialized = False
    except Exception:
        pass


def cleanup_after_wsgi_tests():
    """Specific cleanup for WSGI-related tests."""
    aggressive_cleanup()

    # Additional WSGI-specific cleanup
    try:
        # Kill any remaining cheroot processes
        import subprocess
        subprocess.run(['pkill', '-f', 'cheroot'], capture_output=True)
    except Exception:
        pass


if __name__ == "__main__":
    print("Running aggressive cleanup...")
    aggressive_cleanup()
    print("Cleanup complete.")