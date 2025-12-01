# Developer Guidance: Thread Pool Sizing and Performance Considerations

## Executive Summary

Thread pool sizing is critical for optimal performance when migrating OpenStack services from Eventlet to native Python threading. This document provides comprehensive guidance for developers on selecting appropriate thread pool sizes, monitoring performance, and tuning threading parameters based on workload characteristics and deployment requirements.

## Background

### Threading vs Eventlet Performance Characteristics

**Eventlet Characteristics:**
- Lightweight green threads (~4KB memory per thread)
- Cooperative scheduling with no preemption overhead
- Single-threaded execution (no true parallelism)
- Excellent for I/O-bound workloads with high concurrency

**Threading Characteristics:**
- Heavier native threads (~8MB memory per thread)
- Preemptive scheduling with context switching overhead
- True parallelism across CPU cores
- Better for CPU-bound workloads and mixed workloads

### Performance Trade-offs

| Aspect | Eventlet | Threading | Impact |
|--------|----------|-----------|---------|
| Memory Usage | Low (4KB/thread) | High (8MB/thread) | Limits maximum concurrency |
| CPU Utilization | Single core | Multi-core | Better performance for CPU-bound tasks |
| Context Switching | Cooperative | Preemptive | Higher overhead but more responsive |
| Debugging | Limited | Full support | Better development experience |
| I/O Performance | Excellent | Good | Similar for most I/O patterns |

## Thread Pool Sizing Guidelines

### 1. Workload Classification

**I/O-Bound Workloads:**
- Database queries
- HTTP API calls
- File system operations
- Network communications

*Characteristics:* High wait time, low CPU usage
*Thread Pool Size:* `min(200, cpu_count * 20)`

**CPU-Bound Workloads:**
- Data processing and computation
- Cryptographic operations
- Image/video processing
- Scientific calculations

*Characteristics:* High CPU usage, minimal wait time
*Thread Pool Size:* `cpu_count` (or slightly higher for pipelining)

**Mixed Workloads:**
- Most OpenStack service operations
- Request processing with both I/O and computation
- Workflow orchestration

*Characteristics:* Variable I/O and CPU usage
*Thread Pool Size:* `min(64, cpu_count * 4)`

### 2. Service-Specific Recommendations

#### API Services (masakari-api)

```python
# Configuration for API request handling
[DEFAULT]
api_workers = 4  # Number of worker processes
executor_thread_pool_size = 64  # Threads per worker
```

**Rationale:**
- API requests are typically I/O-bound (database queries)
- Higher thread count compensates for I/O wait times

#### Engine Services (masakari-engine)

```python
# Configuration for engine operations
[DEFAULT]
notification_thread_pool_size = 32   # Notification processing
driver_thread_pool_size = 16         # Recovery operations
executor_thread_pool_size = 64       # General operations

[engine]
engine_workers = 1  # Single engine worker for coordination
```

**Rationale:**
- Recovery operations involve both I/O and computation
- Need coordination between operations (single engine worker)
- Moderate thread pools to balance resources

#### Database Operations

```python
# Configuration for database-heavy components
[DEFAULT]
db_thread_pool_size = min(100, cpu_count * 10)

[database]
max_pool_size = 30
max_overflow = 60
pool_timeout = 30
```

**Rationale:**
- Database operations are primarily I/O-bound
- Connection pooling limits actual database connections
- Higher thread count improves concurrency

### 3. Dynamic Sizing Strategy

Rather than using fixed thread pool sizes, adaptive sizing allows thread pools to respond to changing workloads while maintaining resource efficiency. This approach is particularly valuable in OpenStack environments where load patterns can vary significantly.

```python
import os
from futurist import DynamicThreadPoolExecutor

def create_adaptive_thread_pool(workload_type, base_size=None):
    """Create thread pool with adaptive sizing."""
    cpu_count = os.cpu_count() or 1

    if base_size is None:
        sizing_rules = {
            'io_heavy': min(200, cpu_count * 20),
            'cpu_heavy': cpu_count,
            'mixed': min(64, cpu_count * 4),
            'api': min(300, cpu_count * 25),
            'notification': min(32, cpu_count * 2),
            'driver': min(16, cpu_count)
        }
        base_size = sizing_rules.get(workload_type, sizing_rules['mixed'])

    # Use dynamic executor for automatic scaling
    return DynamicThreadPoolExecutor(
        max_workers=base_size,
        min_workers=min(4, base_size // 2)
    )
```

This factory function encapsulates best-practice sizing rules for different workload types while providing flexibility for specific deployments. The `DynamicThreadPoolExecutor` automatically adjusts the actual number of active threads based on queue depth and task completion rates, starting with a minimal number of threads and scaling up to the maximum as needed.

## Performance Monitoring and Tuning

### 1. Key Metrics to Monitor

Effective thread pool management requires continuous monitoring of both resource usage and performance characteristics. Understanding these metrics helps identify when thread pools are under-sized, over-sized, or experiencing other performance issues.

```python
import threading
import time
import psutil
import os

class ThreadPoolMonitor:
    """Monitor thread pool performance and resource usage."""

    def __init__(self, executor):
        self.executor = executor
        self.metrics = {
            'submitted_tasks': 0,
            'completed_tasks': 0,
            'failed_tasks': 0,
            'average_execution_time': 0,
            'queue_depth': 0,
            'thread_utilization': 0
        }

    def collect_metrics(self):
        """Collect current performance metrics."""
        process = psutil.Process(os.getpid())

        return {
            'memory_usage_mb': process.memory_info().rss / 1024 / 1024,
            'cpu_percent': process.cpu_percent(),
            'thread_count': len(process.threads()),
            'active_thread_count': threading.active_count(),
            'queue_size': getattr(self.executor, '_work_queue', {}).qsize(),
            'pool_size': getattr(self.executor, '_max_workers', 0)
        }

    def log_performance_summary(self):
        """Log performance summary for analysis."""
        metrics = self.collect_metrics()

        LOG.info("Thread Pool Performance Summary:")
        LOG.info("  Memory Usage: %.1f MB", metrics['memory_usage_mb'])
        LOG.info("  CPU Usage: %.1f%%", metrics['cpu_percent'])
        LOG.info("  Active Threads: %d", metrics['active_thread_count'])
        LOG.info("  Queue Size: %d", metrics['queue_size'])
        LOG.info("  Pool Size: %d", metrics['pool_size'])
```

This monitoring class tracks both system-level resources (memory, CPU) and thread pool specific metrics (queue depth, pool utilization). Key indicators of thread pool health include: stable memory usage, appropriate CPU utilization for the workload type, and minimal queue buildup during normal operations.

### 2. Performance Benchmarking

```python
import time
import concurrent.futures
from statistics import mean, stdev

class ThreadPoolBenchmark:
    """Benchmark thread pool performance under different configurations."""

    def __init__(self):
        self.results = {}

    def benchmark_pool_size(self, work_function, pool_sizes, task_count=100):
        """Benchmark different pool sizes for a given workload."""
        results = {}

        for pool_size in pool_sizes:
            start_time = time.time()

            with concurrent.futures.ThreadPoolExecutor(max_workers=pool_size) as executor:
                # Submit all tasks
                futures = [executor.submit(work_function) for _ in range(task_count)]

                # Wait for completion
                for future in concurrent.futures.as_completed(futures):
                    future.result()

            total_time = time.time() - start_time
            throughput = task_count / total_time

            results[pool_size] = {
                'total_time': total_time,
                'throughput': throughput,
                'avg_time_per_task': total_time / task_count
            }

            LOG.info("Pool size %d: %.2f tasks/sec (%.3f sec total)",
                    pool_size, throughput, total_time)

        return results

    def recommend_optimal_size(self, benchmark_results):
        """Recommend optimal pool size based on benchmark results."""
        # Find pool size with best throughput
        best_throughput = 0
        optimal_size = None

        for pool_size, metrics in benchmark_results.items():
            if metrics['throughput'] > best_throughput:
                best_throughput = metrics['throughput']
                optimal_size = pool_size

        # Check if larger pool sizes don't significantly improve throughput
        # (indicating optimal size reached)
        throughput_values = [m['throughput'] for m in benchmark_results.values()]
        if len(throughput_values) > 1:
            improvement_threshold = 0.05  # 5% improvement threshold

            sorted_results = sorted(benchmark_results.items(),
                                  key=lambda x: x[1]['throughput'], reverse=True)

            best_size, best_metrics = sorted_results[0]
            second_size, second_metrics = sorted_results[1]

            improvement = (best_metrics['throughput'] - second_metrics['throughput']) / second_metrics['throughput']

            if improvement < improvement_threshold:
                # Not much improvement, recommend smaller size for resource efficiency
                optimal_size = min(best_size, second_size)

        return optimal_size
```

### 3. Workload-Specific Tuning

#### I/O-Heavy Workload Tuning

```python
def tune_io_heavy_workload():
    """Tuning guidelines for I/O-heavy workloads."""

    # Benchmark different pool sizes
    def io_simulation():
        time.sleep(0.1)  # Simulate I/O wait
        return "completed"

    benchmark = ThreadPoolBenchmark()
    pool_sizes = [10, 25, 50, 100, 200]
    results = benchmark.benchmark_pool_size(io_simulation, pool_sizes)

    optimal_size = benchmark.recommend_optimal_size(results)

    LOG.info("Recommended thread pool size for I/O workload: %d", optimal_size)

    return {
        'recommended_pool_size': optimal_size,
        'configuration': {
            'max_workers': optimal_size,
            'min_workers': optimal_size // 4,
            'queue_size': optimal_size * 2  # Allow queuing for burst traffic
        }
    }
```

#### CPU-Heavy Workload Tuning

```python
def tune_cpu_heavy_workload():
    """Tuning guidelines for CPU-heavy workloads."""

    cpu_count = os.cpu_count() or 1

    def cpu_simulation():
        # Simulate CPU-intensive work
        result = sum(i * i for i in range(10000))
        return result

    benchmark = ThreadPoolBenchmark()
    # Test pool sizes around CPU count
    pool_sizes = [cpu_count // 2, cpu_count, cpu_count * 2]
    results = benchmark.benchmark_pool_size(cpu_simulation, pool_sizes)

    optimal_size = benchmark.recommend_optimal_size(results)

    LOG.info("Recommended thread pool size for CPU workload: %d", optimal_size)

    return {
        'recommended_pool_size': optimal_size,
        'configuration': {
            'max_workers': optimal_size,
            'min_workers': optimal_size,  # Keep threads alive for CPU work
            'queue_size': optimal_size    # Limited queuing for CPU-bound work
        }
    }
```

## Configuration Templates

### 1. Development Environment

```ini
# masakari-dev.conf
[DEFAULT]
# Smaller pools for development
executor_thread_pool_size = 8
notification_thread_pool_size = 4
driver_thread_pool_size = 2

[api]
api_workers = 1  # Single worker for development
```

### 2. Production Environment

```ini
# masakari-production.conf
[DEFAULT]
# Tuned for production workload
executor_thread_pool_size = 64
notification_thread_pool_size = 32
driver_thread_pool_size = 16

[api]
api_workers = 4  # Multiple workers for redundancy
```

### 3. High-Load Environment

```ini
# masakari-high-load.conf
[DEFAULT]
# Increased pools for high-load scenarios
executor_thread_pool_size = 128
notification_thread_pool_size = 64
driver_thread_pool_size = 32

[api]
api_workers = 8  # More workers for high load
```

## Memory Management and Optimization

### 1. Memory Usage Calculation

```python
def calculate_memory_requirements(thread_pools):
    """Calculate expected memory usage for thread pool configuration."""

    # Base memory per thread (approximately 8MB for native threads)
    THREAD_MEMORY_MB = 8

    # Calculate total thread memory
    total_threads = sum(pool_config['max_workers']
                       for pool_config in thread_pools.values())

    thread_memory_mb = total_threads * THREAD_MEMORY_MB

    # Add base application memory (estimated)
    base_memory_mb = 200

    # Add buffer for Python object overhead
    buffer_memory_mb = thread_memory_mb * 0.2

    total_estimated_mb = base_memory_mb + thread_memory_mb + buffer_memory_mb

    return {
        'total_threads': total_threads,
        'thread_memory_mb': thread_memory_mb,
        'total_estimated_mb': total_estimated_mb,
        'recommendation': {
            'minimum_system_memory_gb': total_estimated_mb / 1024 * 1.5,
            'optimal_system_memory_gb': total_estimated_mb / 1024 * 2.0
        }
    }
```

### 2. Memory Monitoring

```python
import gc
import tracemalloc

class MemoryMonitor:
    """Monitor memory usage in threaded applications."""

    def __init__(self):
        self.baseline_memory = None
        tracemalloc.start()

    def snapshot_baseline(self):
        """Take baseline memory snapshot."""
        gc.collect()  # Force garbage collection
        process = psutil.Process(os.getpid())
        self.baseline_memory = process.memory_info().rss

    def check_memory_growth(self):
        """Check for memory growth since baseline."""
        if self.baseline_memory is None:
            self.snapshot_baseline()
            return

        gc.collect()
        process = psutil.Process(os.getpid())
        current_memory = process.memory_info().rss

        growth_mb = (current_memory - self.baseline_memory) / 1024 / 1024
        growth_percent = growth_mb / (self.baseline_memory / 1024 / 1024) * 100

        LOG.info("Memory growth: %.1f MB (%.1f%%)", growth_mb, growth_percent)

        if growth_percent > 20:  # Alert if growth exceeds 20%
            LOG.warning("Significant memory growth detected: %.1f%%", growth_percent)

        return {
            'growth_mb': growth_mb,
            'growth_percent': growth_percent,
            'current_memory_mb': current_memory / 1024 / 1024
        }
```

## Deployment Scaling Guidelines

### 1. Horizontal vs Vertical Scaling

**Horizontal Scaling (Multiple Processes):**
- Pros: Better fault isolation, can utilize multiple cores
- Cons: More complex coordination, higher resource overhead
- Use when: High availability required, large deployment

**Vertical Scaling (Larger Thread Pools):**
- Pros: Simpler coordination, lower resource overhead
- Cons: Single point of failure, limited by single process limits
- Use when: Moderate load, development environments

### 2. Auto-Scaling Configuration

```python
class AutoScalingThreadPool:
    """Thread pool with automatic scaling based on queue depth."""

    def __init__(self, min_workers=4, max_workers=64, scale_threshold=10):
        self.min_workers = min_workers
        self.max_workers = max_workers
        self.scale_threshold = scale_threshold
        self.current_workers = min_workers
        self.executor = None
        self.monitoring_interval = 30  # seconds

    def start_monitoring(self):
        """Start automatic scaling monitoring."""
        def monitor():
            while True:
                self._adjust_pool_size()
                time.sleep(self.monitoring_interval)

        monitor_thread = threading.Thread(target=monitor, daemon=True)
        monitor_thread.start()

    def _adjust_pool_size(self):
        """Adjust pool size based on current load."""
        if not self.executor:
            return

        queue_size = self.executor._work_queue.qsize()

        # Scale up if queue is growing
        if queue_size > self.scale_threshold and self.current_workers < self.max_workers:
            new_size = min(self.current_workers * 2, self.max_workers)
            self._resize_pool(new_size)
            LOG.info("Scaling up thread pool: %d -> %d", self.current_workers, new_size)

        # Scale down if queue is consistently empty
        elif queue_size == 0 and self.current_workers > self.min_workers:
            new_size = max(self.current_workers // 2, self.min_workers)
            self._resize_pool(new_size)
            LOG.info("Scaling down thread pool: %d -> %d", self.current_workers, new_size)
```

## Troubleshooting Performance Issues

### 1. Common Performance Problems

**High Memory Usage:**
- Symptom: Memory usage grows continuously
- Causes: Thread leaks, context not cleaned up, large thread pools
- Solution: Monitor thread count, implement proper cleanup, reduce pool sizes

**Low Throughput:**
- Symptom: Tasks take longer than expected
- Causes: Thread pool too small, CPU contention, I/O bottlenecks
- Solution: Benchmark pool sizes, monitor CPU usage, optimize I/O patterns

**High CPU Usage:**
- Symptom: CPU usage consistently high
- Causes: Thread pool too large, excessive context switching, CPU-bound tasks
- Solution: Reduce pool size for I/O workloads, optimize algorithms

### 2. Diagnostic Tools

```python
class PerformanceDiagnostics:
    """Diagnostic tools for threading performance issues."""

    def diagnose_thread_pool(self, executor, sample_duration=60):
        """Diagnose thread pool performance issues."""
        start_time = time.time()
        initial_metrics = self._collect_metrics()

        # Sample for specified duration
        time.sleep(sample_duration)

        final_metrics = self._collect_metrics()
        duration = time.time() - start_time

        # Calculate deltas
        memory_growth = final_metrics['memory_mb'] - initial_metrics['memory_mb']
        cpu_avg = final_metrics['cpu_percent']

        # Generate recommendations
        recommendations = []

        if memory_growth > 100:  # > 100MB growth
            recommendations.append("High memory growth detected - check for memory leaks")

        if cpu_avg > 80:
            recommendations.append("High CPU usage - consider reducing thread pool size")

        if final_metrics['queue_size'] > 50:
            recommendations.append("High queue depth - consider increasing thread pool size")

        return {
            'duration': duration,
            'memory_growth_mb': memory_growth,
            'avg_cpu_percent': cpu_avg,
            'final_queue_size': final_metrics['queue_size'],
            'recommendations': recommendations
        }
```

## Best Practices Summary

### 1. Configuration Best Practices

- **Start Conservative**: Begin with smaller thread pools and scale up based on monitoring
- **Monitor Continuously**: Track memory usage, CPU utilization, and queue depths
- **Test Thoroughly**: Benchmark different configurations under realistic workloads
- **Document Decisions**: Record the rationale for thread pool sizing decisions

### 2. Development Best Practices

- **Use Dynamic Executors**: Prefer `DynamicThreadPoolExecutor` for automatic scaling
- **Implement Monitoring**: Include performance monitoring in all threaded components
- **Test Memory Usage**: Regularly test for memory leaks in threaded code
- **Profile Performance**: Use profiling tools to identify bottlenecks

### 3. Operational Best Practices

- **Gradual Rollout**: Deploy threading changes gradually with careful monitoring
- **Have Rollback Plan**: Keep eventlet configuration available for quick rollback
- **Monitor Resource Usage**: Track system-level metrics during and after migration
- **Document Configuration**: Maintain clear documentation of threading configurations

## Conclusion

Successful thread pool sizing requires understanding workload characteristics, monitoring performance metrics, and iterative tuning based on real-world usage patterns. The guidelines in this document provide a starting point for migration from Eventlet to threading while maintaining or improving performance characteristics.

Key principles for success:
- Match thread pool size to workload characteristics
- Monitor performance continuously
- Test configurations under realistic loads
- Start conservative and scale based on data
- Maintain documentation and monitoring

Following these practices ensures that threading migration delivers the expected performance benefits while maintaining the reliability and responsiveness of OpenStack services.