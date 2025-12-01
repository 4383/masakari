# Context Propagation Guidance for OpenStack Threading Migration

## Executive Summary

Context propagation is one of the most critical aspects when migrating from Eventlet to native Python threading in OpenStack services. This document provides comprehensive guidance on maintaining OpenStack request context, user authentication, and distributed tracing information across thread boundaries while adhering to OpenStack standards.

## Background

### Context in OpenStack

OpenStack services rely heavily on request context to maintain state across service boundaries:

- **Request ID**: Unique identifier for tracking requests across services
- **User Authentication**: User tokens, roles, and project information
- **Service Catalog**: Available service endpoints
- **Policy Context**: Authorization information
- **Distributed Tracing**: Performance monitoring and debugging data

### Eventlet vs Threading Context Challenges

**Eventlet Behavior:**
- Green threads inherit context automatically
- Context variables persist within the green thread
- Cooperative multitasking preserves context naturally

**Threading Challenges:**
- Native threads do not inherit context automatically
- Context must be explicitly propagated to worker threads
- Thread-local storage requires careful management
- Context cleanup is essential to prevent memory leaks

## Core Principles

### 1. Explicit Context Propagation

Context must be explicitly captured and restored in threading environments:

```python
from oslo_context import context

def worker_function():
    # Capture context before thread execution
    current_context = context.get_current()

    def wrapped_work():
        # Restore context in the worker thread
        context.set_context(current_context)
        try:
            # Perform actual work
            return do_work()
        finally:
            # Clean up context to prevent leaks
            context.clear()

    return wrapped_work
```

### 2. Context Lifecycle Management

Proper context lifecycle management prevents memory leaks and ensures consistency:

```python
import threading
from oslo_context import context

class ContextAwareWorker:
    def __init__(self):
        self._context = context.get_current()

    def run(self):
        # Set context at the beginning of thread execution
        context.set_context(self._context)
        try:
            self._do_work()
        finally:
            # Always clean up context when thread completes
            context.clear()
```

### 3. Thread Pool Integration

Integration with thread pools requires wrapper patterns:

```python
from concurrent.futures import ThreadPoolExecutor
from oslo_context import context

class ContextAwareThreadPoolExecutor:
    def __init__(self, max_workers=None):
        self._executor = ThreadPoolExecutor(max_workers=max_workers)

    def submit(self, func, *args, **kwargs):
        # Capture context at submission time
        current_context = context.get_current()

        def context_wrapper():
            context.set_context(current_context)
            try:
                return func(*args, **kwargs)
            finally:
                context.clear()

        return self._executor.submit(context_wrapper)
```

## OpenStack-Specific Implementation Patterns

### 1. Request Context Propagation

OpenStack services maintain multiple types of context information that must be preserved across thread boundaries. This includes not only the basic request context but also authentication details and distributed tracing information.

```python
from oslo_context import context
from oslo_middleware import request_id
from keystone import auth

class OpenStackContextManager:
    """Manages OpenStack request context across threads."""

    @staticmethod
    def capture_context():
        """Capture current OpenStack context for propagation."""
        return {
            'context': context.get_current(),
            'request_id': request_id.get_global_id(),
            'user_auth': auth.get_current_user_context()
        }

    @staticmethod
    def restore_context(captured_context):
        """Restore OpenStack context in a worker thread."""
        if captured_context.get('context'):
            context.set_context(captured_context['context'])

        if captured_context.get('request_id'):
            request_id.set_global_id(captured_context['request_id'])

        if captured_context.get('user_auth'):
            auth.set_current_user_context(captured_context['user_auth'])

    @staticmethod
    def clear_context():
        """Clean up all context information."""
        context.clear()
        request_id.clear_global_id()
        auth.clear_current_user_context()
```

This pattern demonstrates comprehensive context management for OpenStack environments. The `capture_context()` method gathers all relevant context information at the point where work is submitted to a thread pool. The `restore_context()` method ensures that worker threads have access to the same context information, enabling proper logging, authorization, and request tracing.

### 2. Service Context Integration

OpenStack services built on oslo.service need to integrate context propagation into their core architecture. This ensures that all spawned operations maintain proper context throughout their execution.

```python
from oslo_service import service
from oslo_context import context

class ContextAwareService(service.Service):
    """Base service class with context propagation support."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._context_executor = ContextAwareThreadPoolExecutor(
            max_workers=self._get_thread_pool_size()
        )

    def spawn(self, func, *args, **kwargs):
        """Spawn a function with context propagation."""
        return self._context_executor.submit(func, *args, **kwargs)

    def stop(self, graceful=False):
        """Stop service and cleanup resources."""
        self._context_executor.shutdown(wait=True)
        super().stop(graceful=graceful)
```

This base service class provides a drop-in replacement for typical OpenStack service patterns. By using a context-aware executor for the `spawn()` method, all background operations automatically inherit the current request context. The shutdown process ensures that all threads complete before the service stops, preventing context leaks or incomplete operations.

### 3. RPC Context Handling

```python
import oslo_messaging as messaging
from oslo_context import context

class ContextAwareRPCEndpoint:
    """RPC endpoint with proper context handling."""

    def process_notification(self, ctxt, publisher_id, event_type, payload, metadata):
        """Process notification with context propagation."""

        # Store context for the request
        context.set_context(ctxt)

        try:
            # Process in thread pool with context
            return self._threaded_process(ctxt, event_type, payload)
        finally:
            # Clean up context when done
            context.clear()

    def _threaded_process(self, ctxt, event_type, payload):
        """Process notification in a background thread."""
        current_context = context.get_current()

        def worker():
            context.set_context(current_context)
            try:
                return self._handle_notification(event_type, payload)
            finally:
                context.clear()

        future = self.thread_pool.submit(worker)
        return future.result(timeout=30)
```

## Futurist Integration

### 1. DynamicThreadPoolExecutor with Context

```python
from futurist import DynamicThreadPoolExecutor
from oslo_context import context

class ContextAwareDynamicExecutor(DynamicThreadPoolExecutor):
    """DynamicThreadPoolExecutor with automatic context propagation."""

    def submit(self, func, *args, **kwargs):
        # Capture context at submission time
        current_context = context.get_current()

        def context_wrapper():
            # Restore context in worker thread
            if current_context:
                context.set_context(current_context)

            try:
                return func(*args, **kwargs)
            finally:
                # Clean up context to prevent leaks
                context.clear()

        return super().submit(context_wrapper)

    def map(self, func, *iterables, timeout=None, chunksize=1):
        """Map with context propagation."""
        current_context = context.get_current()

        def context_aware_func(item):
            if current_context:
                context.set_context(current_context)
            try:
                return func(item)
            finally:
                context.clear()

        return super().map(context_aware_func, *iterables,
                          timeout=timeout, chunksize=chunksize)
```

### 2. Periodic Tasks with Context

```python
from oslo_service import periodic_task
from oslo_context import context

class ContextAwarePeriodicTasks(periodic_task.PeriodicTasks):
    """Periodic tasks with context propagation."""

    @periodic_task.periodic_task(spacing=60)
    def cleanup_task(self, context_obj):
        """Example periodic task with context."""
        # Set context for the periodic task
        context.set_context(context_obj)

        try:
            # Execute in thread pool with context
            self._execute_cleanup_with_context()
        finally:
            context.clear()

    def _execute_cleanup_with_context(self):
        """Execute cleanup in thread pool."""
        current_context = context.get_current()

        def cleanup_worker():
            context.set_context(current_context)
            try:
                return self._perform_cleanup()
            finally:
                context.clear()

        future = self.thread_pool.submit(cleanup_worker)
        return future.result()
```

## Context Validation and Debugging

### 1. Context Validation Utilities

```python
from oslo_context import context
from oslo_log import log

LOG = log.getLogger(__name__)

class ContextValidator:
    """Utilities for validating context propagation."""

    @staticmethod
    def validate_context_propagation():
        """Validate that context is properly propagated."""
        current_context = context.get_current()

        if not current_context:
            LOG.warning("No context available in current thread")
            return False

        required_fields = ['request_id', 'user_id', 'project_id']
        missing_fields = [f for f in required_fields
                         if not getattr(current_context, f, None)]

        if missing_fields:
            LOG.warning("Missing required context fields: %s", missing_fields)
            return False

        LOG.debug("Context validation passed: %s", current_context.request_id)
        return True

    @staticmethod
    def log_context_info(prefix=""):
        """Log current context information for debugging."""
        current_context = context.get_current()

        if current_context:
            LOG.debug("%sContext: request_id=%s, user_id=%s, project_id=%s",
                     prefix, current_context.request_id,
                     current_context.user_id, current_context.project_id)
        else:
            LOG.debug("%sNo context available", prefix)
```

### 2. Context Debugging Middleware

```python
import functools
from oslo_context import context
from oslo_log import log

LOG = log.getLogger(__name__)

def debug_context_propagation(func):
    """Decorator to debug context propagation."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        # Log context before execution
        LOG.debug("Before %s: context=%s", func.__name__, context.get_current())

        try:
            result = func(*args, **kwargs)

            # Log context after execution
            LOG.debug("After %s: context=%s", func.__name__, context.get_current())

            return result
        except Exception as e:
            LOG.error("Exception in %s with context %s: %s",
                     func.__name__, context.get_current(), e)
            raise

    return wrapper
```

## Best Practices

### 1. Context Capture Timing

**Do:**
```python
# Capture context immediately before thread creation
current_context = context.get_current()
future = executor.submit(work_with_context, current_context)
```

**Don't:**
```python
# Don't capture context inside the worker thread
def worker():
    current_context = context.get_current()  # May be None or stale
    # ... work ...
```

### 2. Context Cleanup

**Do:**
```python
def worker():
    context.set_context(captured_context)
    try:
        return do_work()
    finally:
        context.clear()  # Always clean up
```

**Don't:**
```python
def worker():
    context.set_context(captured_context)
    return do_work()  # Context not cleaned up - memory leak
```

### 3. Error Handling with Context

```python
def robust_worker_with_context(captured_context):
    try:
        context.set_context(captured_context)
        return do_work()
    except Exception as e:
        # Log with context information for debugging
        LOG.error("Work failed for request %s: %s",
                 context.get_current().request_id, e)
        raise
    finally:
        # Always clean up context
        context.clear()
```

## Testing Context Propagation

### 1. Unit Test Patterns

```python
import unittest
from oslo_context import context
from unittest import mock

class ContextPropagationTest(unittest.TestCase):
    def setUp(self):
        # Create test context
        self.test_context = context.RequestContext(
            user_id='test-user',
            project_id='test-project',
            request_id='test-request-123'
        )

    def test_context_propagation(self):
        """Test that context is properly propagated to threads."""
        context.set_context(self.test_context)

        # Test context propagation through executor
        executor = ContextAwareThreadPoolExecutor(max_workers=1)

        def check_context():
            current = context.get_current()
            self.assertIsNotNone(current)
            self.assertEqual(current.request_id, 'test-request-123')
            return current.user_id

        future = executor.submit(check_context)
        result = future.result()

        self.assertEqual(result, 'test-user')
        executor.shutdown()

    def test_context_cleanup(self):
        """Test that context is properly cleaned up."""
        context.set_context(self.test_context)

        executed = threading.Event()
        cleanup_verified = threading.Event()

        def worker():
            # Verify context is available
            self.assertIsNotNone(context.get_current())
            executed.set()

            # Wait for main thread to check cleanup
            cleanup_verified.wait()

        def cleanup_checker():
            # This should run after worker completes
            time.sleep(0.1)
            self.assertIsNone(context.get_current())
            cleanup_verified.set()

        thread = threading.Thread(target=worker)
        thread.start()

        executed.wait()
        cleanup_thread = threading.Thread(target=cleanup_checker)
        cleanup_thread.start()

        thread.join()
        cleanup_thread.join()
```

### 2. Integration Test Patterns

```python
class ContextIntegrationTest(unittest.TestCase):
    def test_end_to_end_context_flow(self):
        """Test context flow through a complete request cycle."""

        # Simulate incoming request with context
        request_context = context.RequestContext(
            user_id='integration-user',
            project_id='integration-project',
            request_id='integration-123'
        )
        context.set_context(request_context)

        # Process through service layers
        service = ContextAwareService()
        result = service.process_request({
            'action': 'test_action',
            'data': {'key': 'value'}
        })

        # Verify context was maintained
        self.assertIsNotNone(result)
        self.assertTrue(result.get('success'))

        # Verify context is still available in main thread
        current_context = context.get_current()
        self.assertEqual(current_context.request_id, 'integration-123')
```

## Migration Checklist

### Pre-Migration Verification
- [ ] Identify all context usage patterns in the codebase
- [ ] Document current context propagation flows
- [ ] Create baseline tests for context behavior
- [ ] Review OpenStack context configuration

### Implementation Steps
- [ ] Implement context-aware executor wrappers
- [ ] Update service base classes with context support
- [ ] Modify RPC endpoints for context propagation
- [ ] Add context validation and debugging utilities
- [ ] Update periodic task implementations

### Post-Migration Validation
- [ ] Verify context propagates correctly across all thread boundaries
- [ ] Confirm no context memory leaks
- [ ] Validate distributed tracing continues to work
- [ ] Test error handling with context information
- [ ] Performance test with realistic workloads

## Troubleshooting

### Common Issues and Solutions

**Issue**: Context is None in worker threads
**Solution**: Ensure context is captured before thread creation, not inside the worker

**Issue**: Memory leaks from uncleaned context
**Solution**: Always use try/finally blocks to clean up context

**Issue**: Request ID not appearing in logs from threads
**Solution**: Verify context is restored before logging operations

**Issue**: Authentication failures in threaded operations
**Solution**: Ensure user context is properly captured and restored

## Conclusion

Proper context propagation is essential for maintaining OpenStack service behavior when migrating from Eventlet to threading. The patterns and utilities described in this document provide a robust foundation for preserving request context, authentication information, and distributed tracing across thread boundaries while adhering to OpenStack standards.

Key success factors:
- Explicit context capture and restoration
- Consistent cleanup to prevent memory leaks
- Comprehensive testing of context flows
- Proper error handling with context information
- Integration with existing OpenStack context utilities

Following these guidelines ensures that threading migration maintains the reliability and observability characteristics expected in OpenStack deployments.