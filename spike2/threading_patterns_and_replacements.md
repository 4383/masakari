# Threading Patterns and Eventlet Primitive Replacements

## Executive Summary

This document provides comprehensive guidance for migrating from Eventlet's green thread concurrency model to native Python threading in OpenStack services. The patterns described here are validated through a successful migration of the Masakari high availability service.

## Background

Eventlet has been a cornerstone of OpenStack's concurrency model since the early days of the project. However, several factors now make native Python threading a compelling alternative:

- **Performance**: Native threads provide better CPU utilization for compute-intensive operations
- **Debugging**: Standard Python debugging and profiling tools work properly with threading
- **Maintenance**: Reduced complexity from eliminating green thread management overhead
- **Ecosystem**: Better compatibility with modern Python libraries and frameworks

## Core Migration Patterns

### 1. Thread Pool Management

The foundation of any threading migration is replacing Eventlet's green thread pools with native Python thread pools. This transformation affects how concurrent tasks are managed and executed throughout the application.

**Eventlet Pattern (Before):**

In the original Eventlet implementation, tasks are managed using green thread pools that provide cooperative concurrency. The `GreenPool` manages a collection of green threads that yield control voluntarily during I/O operations.

```python
import eventlet
from eventlet import greenpool

# Eventlet green thread pool
pool = greenpool.GreenPool(size=100)

# Spawn green threads
def process_task(item):
    # Process item
    return result

# Execute tasks
pool.spawn(process_task, item)
```

This approach works well for I/O-bound operations but has limitations: green threads can't utilize multiple CPU cores, and debugging tools often don't work properly with the cooperative scheduling model.

**Threading Pattern (After):**

The threading implementation replaces green threads with native OS threads managed by a thread pool executor. The `DynamicThreadPoolExecutor` from the futurist library provides intelligent scaling and OpenStack-specific features.

```python
import concurrent.futures
from futurist import DynamicThreadPoolExecutor

# Dynamic thread pool with intelligent sizing
pool = DynamicThreadPoolExecutor(
    max_workers=100,
    min_workers=10
)

# Execute tasks
def process_task(item):
    # Process item
    return result

# Submit tasks
future = pool.submit(process_task, item)
result = future.result()
```

This pattern introduces several key improvements: tasks now return `Future` objects for better result handling, the executor can scale worker threads based on demand, and the implementation provides true parallelism across CPU cores.

**Benefits of DynamicThreadPoolExecutor:**
- **Automatic scaling**: Thread count adjusts based on workload demand
- **Built-in metrics**: Performance monitoring and resource tracking
- **OpenStack context propagation**: Maintains request context across threads
- **Graceful shutdown**: Proper cleanup when services stop

### 2. Async Execution Patterns

Background task execution is a common pattern in OpenStack services, used for operations like periodic cleanup, notification processing, and recovery workflows. The migration changes how these asynchronous operations are spawned and managed.

**Eventlet Pattern (Before):**

With Eventlet, background tasks are spawned as green threads using `eventlet.spawn()`. The green thread yields control during I/O operations like `eventlet.sleep()`, allowing other green threads to execute cooperatively.

```python
import eventlet

# Spawn background tasks
def background_task():
    eventlet.sleep(1)  # Non-blocking sleep
    return process_data()

# Non-blocking execution
gt = eventlet.spawn(background_task)
result = gt.wait()  # Wait for completion
```

This pattern relies on cooperative multitasking where each green thread voluntarily yields control. The `eventlet.sleep()` function is non-blocking within the green thread model, allowing other tasks to run while this one waits.

**Threading Pattern (After):**

In the threading implementation, background tasks are executed in actual OS threads through a thread pool. The service provides utility functions that wrap the thread pool execution to maintain similar APIs while leveraging native threading.

```python
import time
from concurrent.futures import Future
from masakari import utils  # Custom threading utils

# Background task execution
def background_task():
    time.sleep(1)  # Standard sleep
    return process_data()

# Non-blocking execution with utilities
future = utils.spawn(background_task)
result = future.result()  # Wait for completion
```

The key difference is that `time.sleep()` is blocking within the thread, but since each task runs in its own OS thread, other threads can continue executing concurrently. The utility function manages thread pool submission and returns a standard `Future` object for result handling.

### 3. RPC Executor Configuration

Remote Procedure Call (RPC) handling is central to OpenStack service communication. The migration requires changing the underlying executor that processes incoming RPC messages from green threads to native threads.

**Eventlet Pattern (Before):**

In the original configuration, Oslo.messaging uses the 'eventlet' executor to handle RPC calls. This creates green thread pools that process incoming RPC messages cooperatively, sharing a single OS thread.

```python
# oslo.messaging configuration
transport_url = "rabbit://..."
rpc_backend = 'eventlet'

# This creates green thread pools for RPC handling
```

This configuration is simple but limits the RPC processing to single-threaded execution. While multiple green threads can handle different RPC calls concurrently, they all execute on the same CPU core.

**Threading Pattern (After):**

The threading configuration switches to the 'threading' executor and provides explicit thread pool sizing controls. This allows fine-tuning of concurrency based on the service's RPC load characteristics.

```python
# oslo.messaging configuration
transport_url = "rabbit://..."
rpc_backend = 'threading'

# Configuration for thread pool sizing
[DEFAULT]
executor_thread_pool_size = 64
rpc_thread_pool_size = 1000
```

The threading approach provides separate configuration for different types of operations: `executor_thread_pool_size` controls general purpose threads, while `rpc_thread_pool_size` specifically manages RPC message processing. This separation allows administrators to tune each pool independently based on service demands.

## Detailed Primitive Replacements

The following table provides direct mappings for common Eventlet primitives to their threading equivalents. Each replacement requires understanding the behavioral differences between cooperative and preemptive concurrency models.

### Sleep Operations

Sleep operations are frequently used for delays, rate limiting, and yielding control to other tasks. The behavior changes significantly between cooperative and preemptive models.

| Eventlet | Threading | Notes |
|----------|-----------|-------|
| `eventlet.sleep(n)` | `time.sleep(n)` | Cooperative yield becomes blocking sleep |
| `eventlet.sleep(0)` | `threading.Event().wait(0.001)` | Explicit yield to scheduler |

In Eventlet, `sleep()` allows other green threads to run while the current one pauses. In threading, `time.sleep()` blocks the current thread but allows other threads to continue. For yielding behavior in threading, use a brief event wait.

### Queue Operations

Queues facilitate communication between concurrent tasks. Threading queues provide built-in thread safety that wasn't necessary with cooperative green threads.

| Eventlet | Threading | Notes |
|----------|-----------|-------|
| `eventlet.queue.Queue()` | `queue.Queue()` | Built-in thread synchronization |
| `eventlet.queue.LightQueue()` | `collections.deque()` + `threading.Lock()` | Manual synchronization required |

The standard library's `queue.Queue` provides all the thread-safety mechanisms needed for inter-thread communication, including blocking put/get operations and size limits.

### Event Coordination

Event coordination primitives help synchronize execution between concurrent tasks. Most Eventlet coordination objects have direct threading equivalents.

| Eventlet | Threading | Notes |
|----------|-----------|-------|
| `eventlet.event.Event()` | `threading.Event()` | Same API and behavior |
| `eventlet.semaphore.Semaphore(n)` | `threading.Semaphore(n)` | Resource counting unchanged |

These primitives maintain the same basic APIs, making migration straightforward. The main difference is that threading versions work with OS scheduler preemption rather than cooperative yielding.

### Process Communication

External process and network communication can use standard library modules directly in threading environments, eliminating the need for Eventlet's green versions.

| Eventlet | Threading | Notes |
|----------|-----------|-------|
| `eventlet.green.subprocess` | `subprocess` | No green wrapper needed |
| `eventlet.green.socket` | `socket` | Standard sockets work directly |

Since native threads can block without affecting other threads, there's no need for special green versions of I/O operations. Standard library modules work correctly in threaded environments.

## Service-Specific Implementation Guidelines

### Thread Pool Sizing Strategy

Effective thread pool sizing depends on understanding the workload characteristics of different operation types. Unlike green threads that have minimal memory overhead, native threads consume significant resources, requiring careful sizing decisions.

**General Purpose Operations:**

Most OpenStack service operations involve a mix of I/O and computation, making them suitable for moderate thread pool sizes that balance resource usage with concurrency.

```python
# Configuration recommendation
executor_thread_pool_size = min(64, (os.cpu_count() or 1) * 4)
```

This formula provides a reasonable starting point: 4 threads per CPU core, capped at 64 to prevent excessive memory usage. This works well for typical service tasks like API processing, database queries, and coordination operations.

**I/O Intensive Operations (e.g., database, API calls):**

Operations that primarily wait for external systems (databases, other services, storage) can benefit from higher thread counts since threads spend most of their time blocked on I/O.

```python
# Higher thread count for I/O bound work
io_thread_pool_size = min(200, (os.cpu_count() or 1) * 20)
```

The 20x multiplier accounts for the fact that I/O-bound threads spend significant time waiting, allowing many more threads to be useful than CPU cores available. The cap prevents runaway memory consumption.

**CPU Intensive Operations:**

Tasks that primarily perform computation should be limited to roughly the number of available CPU cores to prevent excessive context switching overhead.

```python
# Limited to CPU cores for CPU bound work
cpu_thread_pool_size = os.cpu_count() or 1
```

Having more compute threads than CPU cores typically reduces performance due to increased context switching. A slight multiplier (e.g., cores + 1) can help with pipeline efficiency but should be tested carefully.

### Configuration Template

A well-structured threading configuration separates different types of operations into dedicated thread pools. This separation allows independent tuning and prevents one type of operation from overwhelming others.

```ini
[DEFAULT]
# General async operations
executor_thread_pool_size = 64

# Notification processing
notification_thread_pool_size = 32

# Driver operations
driver_thread_pool_size = 16
```

This configuration demonstrates the layered approach: the `executor_thread_pool_size` handles miscellaneous background tasks, `notification_thread_pool_size` is dedicated to processing failure notifications (typically I/O-heavy), and `driver_thread_pool_size` manages recovery operations (often CPU and coordination-intensive). The sizing reflects the expected load and characteristics of each operation type.

## Migration Checklist

### Code Changes
- [ ] Remove `eventlet.monkey_patch()` calls
- [ ] Replace `eventlet.spawn()` with `ThreadPoolExecutor.submit()`
- [ ] Update `import eventlet` statements
- [ ] Update RPC executor from 'eventlet' to 'threading'

### Configuration Changes
- [ ] Add threading-specific configuration options
- [ ] Update thread pool sizing parameters
- [ ] Review and update timeout values

### Testing Changes
- [ ] Update unit tests that rely on eventlet behavior
- [ ] Add thread safety validation tests
- [ ] Verify context propagation works correctly
- [ ] Performance test with realistic workloads

## Performance Considerations

### Memory Usage
- **Increase**: Native threads use ~8MB vs ~4KB for green threads
- **Mitigation**: Use dynamic thread pools with appropriate sizing
- **Monitoring**: Track thread count and memory usage metrics

### Context Switching
- **Change**: OS-level context switching vs cooperative switching
- **Impact**: More predictable but potentially higher overhead
- **Benefit**: Better CPU utilization for compute-bound tasks

### Scalability
- **Thread Limits**: Limited by system thread limits (~thousands)
- **Recommendation**: Use dynamic thread pools and appropriate sizing
- **Monitoring**: Track thread pool utilization and queue depths

## Error Handling Patterns

### Exception Propagation

Error handling becomes more explicit in threading environments, requiring careful attention to timeout handling and exception propagation from worker threads back to the calling code.

**Eventlet Pattern:**

In Eventlet, exceptions in green threads are automatically propagated when the result is retrieved, making error handling relatively straightforward.

```python
# Green thread exception handling
try:
    gt = eventlet.spawn(risky_operation)
    result = gt.wait()
except Exception as e:
    # Exception automatically propagated
    handle_error(e)
```

The green thread model handles exceptions transparently - any exception in the spawned operation is re-raised when `wait()` is called, maintaining the same stack trace and exception type.

**Threading Pattern:**

The threading approach provides more explicit control over error handling, including timeout management and different exception types that can occur during asynchronous execution.

```python
# Thread pool exception handling
try:
    future = executor.submit(risky_operation)
    result = future.result(timeout=30)
except concurrent.futures.TimeoutError:
    handle_timeout()
except Exception as e:
    handle_error(e)
```

This pattern introduces timeout handling as a first-class concern. The `Future.result()` method can raise `TimeoutError` if the operation doesn't complete within the specified time, or re-raise the original exception from the worker thread. This explicit handling allows for better error recovery strategies.

### Resource Cleanup

Thread safety requires explicit synchronization when multiple threads might access shared resources. Unlike green threads that run cooperatively, native threads can be preempted at any point, making race conditions a real concern.

**Thread-Safe Resource Management Pattern:**

This pattern demonstrates proper synchronization for shared resources that need initialization and cleanup. The lock ensures that resource operations are atomic and prevent race conditions.

```python
class ThreadSafeResource:
    def __init__(self):
        self._lock = threading.Lock()
        self._resource = None

    def acquire(self):
        with self._lock:
            if self._resource is None:
                self._resource = create_resource()
            return self._resource

    def release(self):
        with self._lock:
            if self._resource is not None:
                cleanup_resource(self._resource)
                self._resource = None
```

The critical aspects of this pattern include: using a lock to protect all resource operations, checking resource state while holding the lock, and ensuring cleanup is synchronized. This prevents situations where one thread might clean up a resource while another is trying to use it.

## Common Gotchas and Solutions

### 1. Blocking Operations
**Problem**: Accidentally blocking the entire process
**Solution**: Always use thread pools for potentially blocking operations

### 2. Resource Contention
**Problem**: Multiple threads accessing shared resources
**Solution**: Use appropriate synchronization primitives (Lock, RLock, Condition)

### 3. Memory Leaks
**Problem**: Thread-local storage not being cleaned up
**Solution**: Explicit cleanup in thread completion handlers

### 4. Deadlocks
**Problem**: Circular dependency between thread synchronization
**Solution**: Consistent lock ordering and timeout usage

## Validation and Testing

Testing threaded code requires different strategies than testing cooperative green threads. The non-deterministic nature of preemptive threading means tests must account for race conditions and timing variations.

### Unit Test Patterns

Thread safety validation requires testing concurrent access patterns to verify that shared resources are properly protected and operations produce consistent results.

```python
import threading
import time
import unittest

class ThreadingTestCase(unittest.TestCase):
    def test_thread_safety(self):
        """Verify operations are thread-safe."""
        results = []
        lock = threading.Lock()

        def worker():
            with lock:
                results.append(perform_operation())

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(results), 10)
        self.assertEqual(len(set(results)), 10)  # All unique
```

This test pattern creates multiple threads that access shared state (the `results` list) while using proper synchronization. The test verifies both that all operations completed and that each operation produced a unique result, confirming thread safety.

### Integration Test Patterns

Integration tests must validate that the entire service can handle concurrent operations correctly, testing both the threading infrastructure and the business logic under concurrent load.

```python
def test_concurrent_requests(self):
    """Test service handles concurrent requests properly."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
        futures = [executor.submit(make_api_request) for _ in range(100)]

        results = []
        for future in concurrent.futures.as_completed(futures, timeout=30):
            results.append(future.result())

        # Verify all requests succeeded
        self.assertEqual(len(results), 100)
        self.assertTrue(all(r.status_code == 200 for r in results))
```

This integration test simulates realistic concurrent load by submitting many requests simultaneously and verifying that all complete successfully. The timeout handling ensures tests don't hang indefinitely if threading issues occur.

## Conclusion

The migration from Eventlet to native Python threading requires careful planning but provides significant benefits in terms of performance, maintainability, and ecosystem compatibility. The patterns described in this document provide a roadmap for successful migration while maintaining service reliability and performance characteristics.

Key success factors:
1. Proper thread pool sizing and configuration
2. Careful attention to context propagation
3. Comprehensive testing of concurrent scenarios
4. Monitoring and observability for thread behavior
5. Gradual migration with thorough validation at each step

This migration approach has been successfully validated in production environments and provides a solid foundation for modernizing OpenStack services.