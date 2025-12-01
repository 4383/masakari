# Test Patterns for Thread Isolation and Leak Prevention

## Executive Summary

Testing threaded applications requires special attention to isolation, resource management, and leak prevention. This document provides comprehensive test patterns and utilities specifically designed for OpenStack services migrating from Eventlet to native Python threading, ensuring robust test suites that prevent interference and resource leaks.

## Background

### Testing Challenges in Threading Migration

**Eventlet Testing Characteristics:**
- Deterministic execution order with cooperative scheduling
- Automatic cleanup of green threads on test completion
- Minimal resource overhead per green thread
- Built-in isolation between test cases

**Threading Testing Challenges:**
- Non-deterministic execution order with preemptive scheduling
- Manual resource management and cleanup required
- Higher memory overhead per thread (8MB vs 4KB)
- Potential for thread leakage between test cases
- Context isolation requirements
- Race conditions and timing dependencies

### Core Testing Principles

1. **Isolation**: Each test must be completely independent
2. **Cleanup**: All resources must be explicitly cleaned up
3. **Determinism**: Tests must produce consistent results
4. **Monitoring**: Resource usage must be tracked and validated
5. **Timeout Protection**: Tests must handle hanging threads gracefully

## Base Test Infrastructure

### 1. Threading-Aware Test Base Class

```python
import threading
import time
import unittest
import weakref
from concurrent.futures import ThreadPoolExecutor, as_completed
from unittest import mock

from oslo_context import context
from oslo_log import log

LOG = log.getLogger(__name__)

class ThreadingTestCase(unittest.TestCase):
    """Base test class for threading-aware tests."""

    def setUp(self):
        super().setUp()

        # Track initial thread state
        self.initial_thread_count = threading.active_count()
        self.initial_threads = set(threading.enumerate())

        # Track created resources for cleanup
        self.created_executors = []
        self.created_threads = []
        self.context_stack = []

        # Set up test isolation
        self.test_context = self._create_test_context()
        context.set_context(self.test_context)

    def tearDown(self):
        # Clean up test context
        context.clear()

        # Clean up created executors
        for executor in self.created_executors:
            try:
                executor.shutdown(wait=True)
            except Exception as e:
                LOG.warning("Error shutting down executor: %s", e)

        # Wait for created threads to complete
        for thread in self.created_threads:
            if thread.is_alive():
                thread.join(timeout=5.0)
                if thread.is_alive():
                    LOG.warning("Thread %s did not terminate cleanly", thread.name)

        # Verify thread cleanup
        self._verify_thread_cleanup()

        super().tearDown()

    def _create_test_context(self):
        """Create an isolated test context."""
        return context.RequestContext(
            user_id=f'test-user-{self.id()}',
            project_id=f'test-project-{self.id()}',
            request_id=f'test-request-{int(time.time() * 1000)}'
        )

    def create_executor(self, max_workers=4, **kwargs):
        """Create a tracked thread pool executor."""
        executor = ThreadPoolExecutor(max_workers=max_workers, **kwargs)
        self.created_executors.append(executor)
        return executor

    def create_thread(self, target, *args, **kwargs):
        """Create a tracked thread."""
        thread = threading.Thread(target=target, *args, **kwargs)
        thread.daemon = True  # Ensure threads don't block test completion
        self.created_threads.append(thread)
        return thread

    def _verify_thread_cleanup(self):
        """Verify that no threads leaked from the test."""
        # Allow brief time for threads to finish
        time.sleep(0.1)

        current_threads = set(threading.enumerate())
        new_threads = current_threads - self.initial_threads

        if new_threads:
            thread_names = [t.name for t in new_threads if t.is_alive()]
            if thread_names:
                self.fail(f"Test leaked threads: {thread_names}")

    def assert_no_hanging_threads(self, timeout=5.0):
        """Assert that no threads are still running after timeout."""
        start_time = time.time()
        while time.time() - start_time < timeout:
            if threading.active_count() <= self.initial_thread_count:
                return
            time.sleep(0.1)

        current_count = threading.active_count()
        self.fail(f"Expected {self.initial_thread_count} threads, "
                 f"found {current_count} after {timeout}s")
```

### 2. Context Isolation Utilities

```python
class ContextIsolationMixin:
    """Mixin for context isolation in tests."""

    def setUp(self):
        super().setUp()
        # Store original context to restore later
        self.original_context = context.get_current()

    def tearDown(self):
        # Restore original context
        context.set_context(self.original_context)
        super().tearDown()

    def with_isolated_context(self, func, *args, **kwargs):
        """Execute function with isolated context."""
        test_context = context.RequestContext(
            user_id='isolated-test-user',
            project_id='isolated-test-project',
            request_id=f'isolated-{time.time()}'
        )

        original = context.get_current()
        try:
            context.set_context(test_context)
            return func(*args, **kwargs)
        finally:
            context.set_context(original)

    def assert_context_isolated(self):
        """Assert that context hasn't leaked between operations."""
        current_context = context.get_current()
        if current_context:
            # Should be test context or None
            self.assertTrue(
                current_context.request_id.startswith(('test-', 'isolated-')),
                f"Unexpected context: {current_context.request_id}"
            )
```

### 3. Resource Leak Detection

```python
import gc
import psutil
import os

class ResourceLeakDetector:
    """Utility for detecting resource leaks in tests."""

    def __init__(self):
        self.initial_memory = None
        self.initial_threads = None
        self.initial_file_descriptors = None

    def snapshot_baseline(self):
        """Take baseline resource measurements."""
        process = psutil.Process(os.getpid())
        self.initial_memory = process.memory_info().rss
        self.initial_threads = len(process.threads())

        try:
            self.initial_file_descriptors = process.num_fds()
        except (AttributeError, psutil.AccessDenied):
            # Not available on all platforms
            self.initial_file_descriptors = None

    def check_for_leaks(self, memory_threshold_mb=50):
        """Check for resource leaks since baseline."""
        gc.collect()  # Force garbage collection
        time.sleep(0.1)  # Allow cleanup to complete

        process = psutil.Process(os.getpid())
        current_memory = process.memory_info().rss
        current_threads = len(process.threads())

        leaks = []

        # Check memory leak
        memory_increase = (current_memory - self.initial_memory) / 1024 / 1024
        if memory_increase > memory_threshold_mb:
            leaks.append(f"Memory leak: {memory_increase:.1f}MB increase")

        # Check thread leak
        thread_increase = current_threads - self.initial_threads
        if thread_increase > 0:
            leaks.append(f"Thread leak: {thread_increase} threads")

        # Check file descriptor leak
        if self.initial_file_descriptors is not None:
            try:
                current_fds = process.num_fds()
                fd_increase = current_fds - self.initial_file_descriptors
                if fd_increase > 10:  # Allow some variation
                    leaks.append(f"File descriptor leak: {fd_increase} FDs")
            except (AttributeError, psutil.AccessDenied):
                pass

        return leaks

class ResourceLeakTestMixin:
    """Mixin for automatic resource leak detection."""

    def setUp(self):
        super().setUp()
        self.leak_detector = ResourceLeakDetector()
        self.leak_detector.snapshot_baseline()

    def tearDown(self):
        # Check for resource leaks
        leaks = self.leak_detector.check_for_leaks()
        if leaks:
            self.fail("Resource leaks detected: " + "; ".join(leaks))
        super().tearDown()
```

## Specific Test Patterns

### 1. Concurrent Operation Testing

```python
class ConcurrentOperationTest(ThreadingTestCase):
    """Test patterns for concurrent operations."""

    def test_concurrent_execution_isolation(self):
        """Test that concurrent operations don't interfere."""
        results = []
        errors = []

        def worker(worker_id):
            try:
                # Simulate work with some delay
                time.sleep(0.1 * (worker_id % 3))

                # Verify context isolation
                current_context = context.get_current()
                self.assertIsNotNone(current_context)

                results.append(f"worker-{worker_id}")
            except Exception as e:
                errors.append(e)

        # Create executor and submit concurrent tasks
        executor = self.create_executor(max_workers=10)
        futures = []

        for i in range(20):
            future = executor.submit(worker, i)
            futures.append(future)

        # Wait for all tasks to complete
        for future in as_completed(futures, timeout=10):
            future.result()  # Re-raise any exceptions

        # Verify results
        self.assertEqual(len(results), 20)
        self.assertEqual(len(errors), 0)
        self.assertEqual(len(set(results)), 20)  # All unique

    def test_exception_isolation(self):
        """Test that exceptions in one thread don't affect others."""
        success_count = 0
        exception_count = 0

        def worker(should_fail):
            if should_fail:
                raise ValueError("Intentional test failure")
            else:
                nonlocal success_count
                success_count += 1

        executor = self.create_executor(max_workers=5)
        futures = []

        # Submit mixed success/failure tasks
        for i in range(10):
            should_fail = i % 3 == 0  # Every 3rd task fails
            future = executor.submit(worker, should_fail)
            futures.append((future, should_fail))

        # Check results
        for future, should_fail in futures:
            try:
                future.result()
                self.assertFalse(should_fail, "Expected failure but succeeded")
            except ValueError:
                self.assertTrue(should_fail, "Unexpected failure")
                exception_count += 1

        # Verify isolation worked
        self.assertEqual(success_count, 7)  # 10 - 3 failures
        self.assertEqual(exception_count, 3)
```

### 2. Context Propagation Testing

```python
class ContextPropagationTest(ThreadingTestCase, ContextIsolationMixin):
    """Test patterns for context propagation."""

    def test_context_propagation_to_threads(self):
        """Test context is properly propagated to worker threads."""
        original_request_id = self.test_context.request_id
        propagated_contexts = []

        def context_worker():
            current_context = context.get_current()
            self.assertIsNotNone(current_context)
            propagated_contexts.append(current_context.request_id)

        # Test with context-aware executor
        executor = ContextAwareThreadPoolExecutor(max_workers=3)
        self.created_executors.append(executor)

        futures = [executor.submit(context_worker) for _ in range(5)]
        for future in futures:
            future.result()

        # Verify context was propagated correctly
        self.assertEqual(len(propagated_contexts), 5)
        self.assertTrue(all(ctx_id == original_request_id
                          for ctx_id in propagated_contexts))

    def test_context_isolation_between_threads(self):
        """Test that different contexts remain isolated."""
        results = {}

        def isolated_worker(worker_id, test_context):
            context.set_context(test_context)
            try:
                current = context.get_current()
                results[worker_id] = current.request_id
            finally:
                context.clear()

        # Create different contexts for each worker
        contexts = []
        for i in range(3):
            ctx = context.RequestContext(
                user_id=f'test-user-{i}',
                project_id=f'test-project-{i}',
                request_id=f'test-request-{i}'
            )
            contexts.append(ctx)

        # Run workers with different contexts
        threads = []
        for i, ctx in enumerate(contexts):
            thread = self.create_thread(isolated_worker, i, ctx)
            thread.start()
            threads.append(thread)

        for thread in threads:
            thread.join()

        # Verify context isolation
        self.assertEqual(len(results), 3)
        expected_ids = [f'test-request-{i}' for i in range(3)]
        self.assertEqual(sorted(results.values()), sorted(expected_ids))
```

### 3. Resource Management Testing

```python
class ResourceManagementTest(ThreadingTestCase, ResourceLeakTestMixin):
    """Test patterns for resource management."""

    def test_executor_cleanup(self):
        """Test that executors are properly cleaned up."""
        # Create multiple executors
        executors = []
        for i in range(3):
            executor = ThreadPoolExecutor(max_workers=2)
            executors.append(executor)
            self.created_executors.append(executor)

        # Submit some work
        futures = []
        for executor in executors:
            future = executor.submit(time.sleep, 0.1)
            futures.append(future)

        # Wait for completion
        for future in futures:
            future.result()

        # Cleanup happens in tearDown - resource leak detector will verify

    def test_thread_pool_resource_limits(self):
        """Test thread pool respects resource limits."""
        max_workers = 5
        executor = self.create_executor(max_workers=max_workers)

        # Submit more tasks than max_workers
        active_workers = []
        completion_times = []

        def timed_worker(worker_id):
            start_time = time.time()
            active_workers.append(worker_id)
            time.sleep(0.2)  # Hold thread briefly
            end_time = time.time()
            completion_times.append(end_time - start_time)
            active_workers.remove(worker_id)

        futures = []
        start_time = time.time()
        for i in range(max_workers * 2):  # Submit 10 tasks to 5-worker pool
            future = executor.submit(timed_worker, i)
            futures.append(future)

        # Wait for all to complete
        for future in futures:
            future.result()

        total_time = time.time() - start_time

        # Should take at least 2 batch executions (0.4s) due to worker limit
        self.assertGreater(total_time, 0.35)

        # Verify we never exceeded max_workers
        # Note: This is hard to test reliably due to timing,
        # but resource monitoring should catch violations

    def test_context_cleanup_prevents_leaks(self):
        """Test that context cleanup prevents memory leaks."""
        # Create many contexts and verify cleanup
        contexts_created = []
        weak_refs = []

        def context_worker(worker_id):
            # Create context with substantial data
            test_ctx = context.RequestContext(
                user_id=f'leak-test-user-{worker_id}',
                project_id=f'leak-test-project-{worker_id}',
                request_id=f'leak-test-request-{worker_id}',
                user_domain_name='large-domain-name' * 100  # Make it bigger
            )

            context.set_context(test_ctx)
            contexts_created.append(worker_id)
            weak_refs.append(weakref.ref(test_ctx))

            try:
                # Do some work
                time.sleep(0.01)
            finally:
                context.clear()

        # Create many context-using threads
        executor = self.create_executor(max_workers=10)
        futures = [executor.submit(context_worker, i) for i in range(50)]

        for future in futures:
            future.result()

        # Force garbage collection
        gc.collect()
        time.sleep(0.1)

        # Check that contexts were cleaned up
        live_contexts = sum(1 for ref in weak_refs if ref() is not None)
        self.assertLess(live_contexts, len(weak_refs) * 0.1,
                       f"Too many contexts still alive: {live_contexts}/{len(weak_refs)}")
```

### 4. Timing and Synchronization Testing

```python
class TimingSynchronizationTest(ThreadingTestCase):
    """Test patterns for timing and synchronization."""

    def test_synchronized_start(self):
        """Test synchronized start of multiple threads."""
        start_barrier = threading.Barrier(5)  # 5 threads + 1 main thread
        start_times = []

        def synchronized_worker(worker_id):
            # Wait for all threads to be ready
            start_barrier.wait()
            start_times.append(time.time())

        threads = []
        for i in range(4):  # 4 worker threads
            thread = self.create_thread(synchronized_worker, i)
            thread.start()
            threads.append(thread)

        # Release all threads simultaneously
        start_barrier.wait()

        for thread in threads:
            thread.join()

        # Verify synchronized start (within 10ms)
        if len(start_times) > 1:
            time_range = max(start_times) - min(start_times)
            self.assertLess(time_range, 0.01, "Start times too spread out")

    def test_timeout_handling(self):
        """Test proper timeout handling in threaded operations."""
        def slow_worker():
            time.sleep(2.0)  # Longer than timeout
            return "completed"

        executor = self.create_executor(max_workers=1)
        future = executor.submit(slow_worker)

        # Test timeout behavior
        with self.assertRaises(TimeoutError):
            future.result(timeout=0.5)

        # Future should still be running
        self.assertFalse(future.done())

        # Allow completion
        result = future.result(timeout=2.0)
        self.assertEqual(result, "completed")

    def test_race_condition_detection(self):
        """Test for race conditions in shared resource access."""
        shared_counter = {"value": 0}
        lock = threading.Lock()

        def increment_worker(use_lock=True):
            for _ in range(100):
                if use_lock:
                    with lock:
                        shared_counter["value"] += 1
                else:
                    # Intentional race condition
                    current = shared_counter["value"]
                    time.sleep(0.0001)  # Increase chance of race
                    shared_counter["value"] = current + 1

        # Test with proper locking
        shared_counter["value"] = 0
        executor = self.create_executor(max_workers=5)
        futures = [executor.submit(increment_worker, True) for _ in range(5)]
        for future in futures:
            future.result()

        self.assertEqual(shared_counter["value"], 500)  # 5 threads * 100 increments

        # Test without locking (should detect race conditions)
        shared_counter["value"] = 0
        futures = [executor.submit(increment_worker, False) for _ in range(5)]
        for future in futures:
            future.result()

        # Race condition should cause value to be less than expected
        self.assertLess(shared_counter["value"], 500)
```

## Advanced Testing Utilities

### 1. Thread Pool Monitor

```python
import threading
from concurrent.futures import ThreadPoolExecutor

class MonitoredThreadPoolExecutor(ThreadPoolExecutor):
    """ThreadPoolExecutor with monitoring capabilities for testing."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.submitted_tasks = 0
        self.completed_tasks = 0
        self.failed_tasks = 0
        self.active_tasks = 0
        self._monitor_lock = threading.Lock()

    def submit(self, fn, *args, **kwargs):
        def monitored_fn(*args, **kwargs):
            with self._monitor_lock:
                self.active_tasks += 1

            try:
                result = fn(*args, **kwargs)
                with self._monitor_lock:
                    self.completed_tasks += 1
                return result
            except Exception as e:
                with self._monitor_lock:
                    self.failed_tasks += 1
                raise
            finally:
                with self._monitor_lock:
                    self.active_tasks -= 1

        with self._monitor_lock:
            self.submitted_tasks += 1

        return super().submit(monitored_fn, *args, **kwargs)

    def get_stats(self):
        """Get current execution statistics."""
        with self._monitor_lock:
            return {
                'submitted': self.submitted_tasks,
                'completed': self.completed_tasks,
                'failed': self.failed_tasks,
                'active': self.active_tasks,
                'pending': self.submitted_tasks - self.completed_tasks - self.failed_tasks
            }
```

### 2. Deterministic Test Scheduler

```python
import queue
import threading

class DeterministicTestScheduler:
    """Scheduler for deterministic testing of concurrent operations."""

    def __init__(self):
        self.step_queue = queue.Queue()
        self.waiting_threads = {}
        self.current_step = 0
        self.lock = threading.Lock()

    def wait_for_step(self, thread_id, step):
        """Block thread until specified step is reached."""
        with self.lock:
            if step <= self.current_step:
                return  # Step already passed

            if step not in self.waiting_threads:
                self.waiting_threads[step] = []

            event = threading.Event()
            self.waiting_threads[step].append(event)

        event.wait()

    def advance_to_step(self, step):
        """Advance to the specified step and wake waiting threads."""
        with self.lock:
            self.current_step = step

            # Wake threads waiting for this step or earlier
            for waiting_step in list(self.waiting_threads.keys()):
                if waiting_step <= step:
                    events = self.waiting_threads.pop(waiting_step)
                    for event in events:
                        event.set()

class DeterministicTestMixin:
    """Mixin for deterministic concurrent testing."""

    def setUp(self):
        super().setUp()
        self.scheduler = DeterministicTestScheduler()

    def synchronized_operation(self, thread_id, step_before, operation, step_after):
        """Execute operation with deterministic scheduling."""
        self.scheduler.wait_for_step(thread_id, step_before)
        result = operation()
        self.scheduler.advance_to_step(step_after)
        return result
```

## Integration with Existing Test Frameworks

### 1. TestTools Integration

```python
import testtools
from testtools import matchers

class ThreadingTestToolsCase(ThreadingTestCase, testtools.TestCase):
    """Integration with testtools for enhanced assertions."""

    def assert_threads_complete_within(self, threads, timeout):
        """Assert all threads complete within timeout."""
        incomplete_threads = []
        for thread in threads:
            thread.join(timeout=timeout)
            if thread.is_alive():
                incomplete_threads.append(thread.name)

        self.assertThat(incomplete_threads, matchers.HasLength(0),
                       f"Threads did not complete within {timeout}s")

    def assert_no_deadlock(self, callable_obj, *args, **kwargs):
        """Assert that callable doesn't cause deadlock."""
        result_queue = queue.Queue()
        exception_queue = queue.Queue()

        def wrapper():
            try:
                result = callable_obj(*args, **kwargs)
                result_queue.put(result)
            except Exception as e:
                exception_queue.put(e)

        thread = self.create_thread(wrapper)
        thread.start()
        thread.join(timeout=10.0)

        if thread.is_alive():
            self.fail("Operation appears to have deadlocked")

        if not exception_queue.empty():
            raise exception_queue.get()

        return result_queue.get() if not result_queue.empty() else None
```

### 2. Fixtures for Resource Management

```python
import fixtures

class ThreadPoolFixture(fixtures.Fixture):
    """Fixture for managing thread pools in tests."""

    def __init__(self, max_workers=4, **kwargs):
        super().__init__()
        self.max_workers = max_workers
        self.kwargs = kwargs
        self.executor = None

    def _setUp(self):
        self.executor = ThreadPoolExecutor(
            max_workers=self.max_workers,
            **self.kwargs
        )
        self.addCleanup(self._cleanup_executor)

    def _cleanup_executor(self):
        if self.executor:
            self.executor.shutdown(wait=True)

    def submit(self, fn, *args, **kwargs):
        return self.executor.submit(fn, *args, **kwargs)

class ContextFixture(fixtures.Fixture):
    """Fixture for managing test context."""

    def __init__(self, user_id='test-user', project_id='test-project'):
        super().__init__()
        self.user_id = user_id
        self.project_id = project_id
        self.test_context = None
        self.original_context = None

    def _setUp(self):
        self.original_context = context.get_current()
        self.test_context = context.RequestContext(
            user_id=self.user_id,
            project_id=self.project_id,
            request_id=f'test-{int(time.time() * 1000)}'
        )
        context.set_context(self.test_context)
        self.addCleanup(self._cleanup_context)

    def _cleanup_context(self):
        context.set_context(self.original_context)
```

## Performance and Load Testing

### 1. Concurrent Load Testing

```python
class ConcurrentLoadTest(ThreadingTestCase):
    """Load testing patterns for threaded components."""

    def test_sustained_load(self):
        """Test component under sustained concurrent load."""
        duration = 10  # seconds
        target_rps = 50  # requests per second

        executor = self.create_executor(max_workers=20)
        start_time = time.time()
        submitted_count = 0
        completed_count = 0
        errors = []

        def load_worker():
            nonlocal completed_count
            try:
                # Simulate work
                time.sleep(0.01)
                completed_count += 1
            except Exception as e:
                errors.append(e)

        # Submit requests at target rate
        while time.time() - start_time < duration:
            if submitted_count < (time.time() - start_time) * target_rps:
                executor.submit(load_worker)
                submitted_count += 1
            time.sleep(0.001)  # Small delay to control rate

        # Wait for completion
        executor.shutdown(wait=True)

        # Verify performance
        actual_duration = time.time() - start_time
        actual_rps = completed_count / actual_duration

        self.assertGreater(actual_rps, target_rps * 0.8,
                          f"Performance too low: {actual_rps:.1f} < {target_rps * 0.8:.1f}")
        self.assertEqual(len(errors), 0, f"Errors during load test: {errors}")

    def test_memory_usage_under_load(self):
        """Test memory usage remains stable under load."""
        if not hasattr(self, 'leak_detector'):
            self.leak_detector = ResourceLeakDetector()
            self.leak_detector.snapshot_baseline()

        executor = self.create_executor(max_workers=10)

        # Submit many tasks with some delay
        def memory_intensive_worker():
            # Create some objects that should be cleaned up
            data = [i for i in range(1000)]
            time.sleep(0.01)
            return len(data)

        futures = []
        for _ in range(100):
            future = executor.submit(memory_intensive_worker)
            futures.append(future)

        # Wait for all to complete
        for future in futures:
            future.result()

        # Check for memory leaks
        leaks = self.leak_detector.check_for_leaks(memory_threshold_mb=100)
        self.assertEqual(len(leaks), 0, f"Memory leaks detected: {leaks}")
```

## Migration Testing Strategy

### 1. Compatibility Testing

```python
class CompatibilityTest(ThreadingTestCase):
    """Test compatibility between eventlet and threading implementations."""

    def test_api_compatibility(self):
        """Test that API remains compatible after migration."""
        # This would test that the same public APIs work
        # in both eventlet and threading versions
        pass

    def test_behavior_compatibility(self):
        """Test that behavior remains compatible after migration."""
        # This would verify that the same inputs produce
        # the same outputs in both implementations
        pass

class RegressionTest(ThreadingTestCase):
    """Regression testing for threading migration."""

    def test_no_performance_regression(self):
        """Test that performance doesn't regress significantly."""
        pass

    def test_no_functional_regression(self):
        """Test that functionality doesn't regress."""
        pass
```

## Conclusion

Effective testing of threaded applications requires careful attention to isolation, resource management, and deterministic behavior. The patterns and utilities provided in this document enable robust testing of OpenStack services during and after migration from Eventlet to native Python threading.

Key testing principles:
- Complete isolation between test cases
- Explicit resource management and cleanup
- Comprehensive leak detection
- Context propagation validation
- Performance and load testing under realistic conditions

Following these patterns ensures that threading migration maintains the reliability and performance characteristics expected in production OpenStack deployments while providing confidence in the correctness of the migration.