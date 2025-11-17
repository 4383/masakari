# Proposed Architectural Decisions: Eventlet to Threading Migration

## Document Overview

This document outlines proposed architectural decisions, identified constraints, and design recommendations for the potential Eventlet to threading migration in Masakari. It serves as a foundation for technical planning and implementation guidance.

---

## Proposed Decision Record Format

Each proposed decision follows the format:
- **Decision ID**: Unique identifier
- **Date**: Analysis date
- **Status**: Proposed, Under Review
- **Context**: Background and problem statement
- **Proposed Decision**: What should be decided
- **Expected Consequences**: Anticipated implications and trade-offs
- **Recommended Implementation**: How it should be realized

---

## ADR-001: Threading Library Selection for WSGI Server

**Date**: 2025-11-14
**Status**: 🔄 Proposed
**Context**: Need to replace eventlet.wsgi with a threading-compatible WSGI server

### Proposed Decision
Adopt **cheroot** as the replacement WSGI server implementation.

### Rationale
- **Production-Grade**: Used by CherryPy, battle-tested in production
- **Thread-Safe**: Native threading support with proper concurrency handling
- **SSL Support**: Built-in SSL termination capabilities
- **Configuration**: Extensive configuration options matching eventlet.wsgi capabilities
- **Maintenance**: Active development and maintenance
- **OpenStack Compatibility**: Successfully used in other OpenStack projects

### Alternatives Considered
1. **Gunicorn**: Excellent but requires process-based workers, incompatible with oslo.service
2. **Waitress**: Good threading support but limited SSL configuration options
3. **uWSGI**: Complex configuration, overhead for our use case
4. **Custom Implementation**: Too much development effort and maintenance burden

### Expected Consequences
- **Positive**: Production-ready server, comprehensive SSL support, good performance expected
- **Negative**: Additional dependency, different configuration syntax
- **Mitigation**: Thorough testing required, configuration migration guides needed

### Recommended Implementation
```python
self._httpd = cheroot.wsgi.Server(
    bind_addr=(self.host, self.port),
    wsgi_app=wsgi_app,
    numthreads=self.pool_size,
    server_name=self.name
)
```

---

## ADR-002: Thread Pool Management Strategy

**Date**: 2025-11-14
**Status**: 🔄 Proposed
**Context**: Need robust thread pool management replacing eventlet.GreenPool

### Proposed Decision
Use **futurist.DynamicThreadPoolExecutor** for specialized thread pools and **concurrent.futures.ThreadPoolExecutor** for general usage.

### Rationale
- **futurist**: OpenStack native library, designed for Oslo integration
- **Dynamic Scaling**: Automatic thread pool size adjustment based on load
- **Context Propagation**: Built-in support for OpenStack request context
- **Resource Management**: Proper cleanup and shutdown mechanisms
- **Monitoring**: Built-in metrics and monitoring capabilities

### Proposed Thread Pool Architecture
```
Proposed Masakari Threading Architecture:
├── General Operations Pool (ThreadPoolExecutor)
│   └── Size: 64 threads (configurable)
├── Notification Processing Pool (DynamicThreadPoolExecutor)
│   └── Size: 32 threads (configurable)
├── Driver Operations Pool (DynamicThreadPoolExecutor)
│   └── Size: 16 threads (configurable)
└── WSGI Server Pool (cheroot internal)
    └── Size: 300 threads (configurable)
```

### Expected Consequences
- **Positive**: Robust thread management expected, OpenStack integration, monitoring capabilities
- **Negative**: More complex configuration, higher memory usage anticipated
- **Configuration**: Multiple pool size tuning parameters will be required

### Recommended Implementation
```python
_THREAD_POOL = ThreadPoolExecutor(max_workers=CONF.executor_thread_pool_size)
_NOTIFICATION_POOL = DynamicThreadPoolExecutor(max_workers=CONF.notification_thread_pool_size)
_DRIVER_POOL = DynamicThreadPoolExecutor(max_workers=CONF.driver_thread_pool_size)
```

---

## ADR-003: Context Propagation Mechanism

**Date**: 2025-11-14
**Status**: 🔄 Proposed
**Context**: Ensure OpenStack request context is properly propagated across threads

### Proposed Decision
Leverage **oslo.context** with thread-local storage and explicit context management in thread pool submissions.

### Critical Constraint Identified
**Important Consideration**: OpenStack context propagation will require explicit handling when crossing thread boundaries, unlike eventlet's automatic green thread context inheritance.

### Recommended Implementation Strategy
```python
def spawn(func, *args, **kwargs):
    current_context = context.get_current()

    def context_wrapper():
        with current_context:
            return func(*args, **kwargs)

    return _THREAD_POOL.submit(context_wrapper)
```

### Expected Consequences
- **Positive**: Proper security context isolation, audit trail preservation
- **Negative**: Explicit context management will be required in all background operations
- **Risk Mitigation**: Comprehensive testing of context-sensitive operations will be essential

---

## ADR-004: Configuration Backwards Compatibility

**Date**: 2025-11-14
**Status**: 🔄 Proposed
**Context**: Minimize operational impact during migration

### Proposed Decision
Maintain full backward compatibility while introducing new threading-specific configuration options.

### Configuration Strategy
```python
# Deprecated but supported
[DEFAULT]
monkey_patch = True  # Ignored, logged as deprecated

# New threading configuration
[DEFAULT]
executor_thread_pool_size = 64
notification_thread_pool_size = 32
driver_thread_pool_size = 16

[wsgi]
default_pool_size = 300
```

### Expected Consequences
- **Positive**: Zero-downtime migration possibility, gradual transition
- **Negative**: Temporary configuration complexity
- **Migration Path**: Clear deprecation and migration timeline

---

## ADR-005: Error Handling and Exception Propagation

**Date**: 2025-10-21
**Status**: ✅ Accepted
**Context**: Eventlet and threading have different exception handling characteristics

### Architectural Constraint Discovered
**Critical Finding**: Thread-based exception handling requires explicit error propagation mechanisms, unlike eventlet's shared exception context.

### Proposed Decision
Implement explicit exception handling with proper logging and context preservation.

### Recommended Implementation Pattern
```python
def safe_thread_wrapper(func, *args, **kwargs):
    try:
        with context.get_current():
            return func(*args, **kwargs)
    except Exception as e:
        LOG.exception("Thread execution failed: %s", e)
        raise
```

### Expected Consequences
- **Positive**: Better error isolation, improved debugging
- **Negative**: Explicit error handling required
- **Monitoring**: Enhanced error tracking capabilities

---

## ADR-006: Testing Strategy for Threading Migration

**Date**: 2025-10-21
**Status**: ✅ Accepted
**Context**: Need comprehensive testing approach for concurrency model changes

### Proposed Decision
Multi-layered testing approach with specific threading considerations.

### Testing Architecture
```
Testing Strategy:
├── Unit Tests
│   ├── Thread-safe utility functions
│   ├── Context propagation validation
│   └── Exception handling verification
├── Integration Tests
│   ├── WSGI server functionality
│   ├── RPC message processing
│   └── Background task execution
├── Performance Tests
│   ├── Throughput benchmarking
│   ├── Resource utilization monitoring
│   └── Latency measurement
└── Concurrency Tests
    ├── Thread safety validation
    ├── Deadlock detection
    └── Resource leak testing
```

### Key Testing Discoveries
1. **Thread Safety**: All shared state requires proper synchronization
2. **Resource Cleanup**: Explicit thread pool shutdown required
3. **Context Isolation**: Each thread must have proper context setup

---

## ADR-007: Deployment and Rollback Strategy

**Date**: 2025-10-21
**Status**: ✅ Accepted
**Context**: Minimize operational risk during production deployment

### Proposed Decision
Blue-green deployment with configuration-based switching capability.

### Deployment Constraint Discovered
**Critical Finding**: Threading and eventlet cannot coexist in the same process due to monkey patching conflicts.

### Recommended Implementation Strategy
```
Deployment Phases:
1. Code Deployment (threading compatible)
2. Configuration Switch (enable threading)
3. Service Restart (activate threading mode)
4. Validation and Monitoring
5. Old Version Cleanup
```

### Rollback Mechanism
- Configuration-based rollback to previous version
- Requires service restart (no hot-swapping possible)
- Automated health checks and rollback triggers

---

## ADR-008: Memory Management and Resource Planning

**Date**: 2025-10-21
**Status**: ✅ Accepted
**Context**: Threading uses significantly more memory than eventlet

### Architectural Constraint Discovered
**Critical Finding**: OS threads consume ~8MB stack space vs. ~8KB for green threads, requiring resource planning adjustments.

### Proposed Decision
Implement conservative thread pool sizing with monitoring-based optimization.

### Resource Planning Formula
```
Memory Calculation:
Base Memory: ~100MB (application baseline)
Thread Memory: pool_size × 8MB (stack space)
Total Memory: Base + (WSGI_pool × 8MB) + (Background_pools × 8MB)

Example Configuration:
WSGI Pool: 300 threads → 2.4GB
Background Pools: 112 threads → 896MB
Total Additional: ~3.3GB
```

### Expected Consequences
- **Positive**: Predictable memory usage, better capacity planning
- **Negative**: Higher memory requirements, pool size constraints
- **Monitoring**: Memory usage alerts and thread pool utilization metrics

---

## ADR-009: SSL Configuration and Security

**Date**: 2025-10-21
**Status**: ✅ Accepted
**Context**: Migrate SSL termination from eventlet.wrap_ssl to cheroot

### Proposed Decision
Use cheroot's built-in SSL support with configuration compatibility mapping.

### Security Constraint Discovered
**Critical Finding**: SSL certificate validation and configuration syntax differs between eventlet and cheroot implementations.

### Configuration Mapping
```python
# Eventlet SSL configuration
ssl_kwargs = {
    'server_side': True,
    'certfile': cert_file,
    'keyfile': key_file,
    'cert_reqs': ssl.CERT_NONE
}

# Cheroot SSL configuration
ssl_adapter = cheroot.ssl.builtin.BuiltinSSLAdapter(
    certificate=cert_file,
    private_key=key_file
)
```

### Security Validation
- Certificate chain validation preserved
- SSL protocol support maintained
- Configuration security review completed

---

## ADR-010: Monitoring and Observability

**Date**: 2025-10-22
**Status**: ✅ Accepted
**Context**: Threading provides different monitoring capabilities than eventlet

### Proposed Decision
Implement enhanced monitoring with thread-specific metrics.

### Monitoring Strategy
```
Threading Metrics:
├── Thread Pool Utilization
│   ├── Active threads count
│   ├── Queue depth monitoring
│   └── Thread lifecycle tracking
├── Performance Metrics
│   ├── Request processing latency
│   ├── Thread switching overhead
│   └── Resource utilization patterns
└── Error Tracking
    ├── Thread-specific exception rates
    ├── Context propagation failures
    └── Resource leak detection
```

### Observability Benefits
- Standard Python profiling tools compatibility
- Better stack trace clarity
- Enhanced debugging capabilities

---

## Key Architectural Constraints and Considerations

### Constraint 1: Thread Pool Sizing Limitations
**Consideration**: OS thread limitations will require careful pool sizing versus eventlet's near-unlimited green thread capacity.
**Expected Impact**: Configuration planning and resource management strategy changes required.
**Recommended Approach**: Dynamic thread pool sizing with monitoring-based optimization.

### Constraint 2: Context Propagation Complexity
**Consideration**: OpenStack context will require explicit thread boundary management.
**Expected Impact**: All background operations will need context wrapper implementation.
**Recommended Approach**: Standardized context propagation utilities and comprehensive testing.

### Constraint 3: Memory Usage Scaling
**Consideration**: Linear memory scaling with thread count versus logarithmic with green threads.
**Expected Impact**: Infrastructure capacity planning adjustments will be required.
**Recommended Approach**: Conservative initial sizing with monitoring-based scaling.

### Constraint 4: Exception Handling Model
**Consideration**: Thread isolation should provide better fault isolation but will require explicit error handling.
**Expected Impact**: Enhanced error handling patterns and monitoring requirements.
**Recommended Approach**: Standardized exception handling with proper logging and context preservation.

### Constraint 5: Debugging and Profiling Capabilities
**Consideration**: Standard Python debugging tools should work significantly better with threading.
**Expected Impact**: Improved development and operational troubleshooting efficiency anticipated.
**Recommended Approach**: Training and tooling updates to leverage enhanced capabilities.

---

## Implementation Guidance and Recommendations

### Technical Recommendations
1. **Library Selection**: Choose mature, well-maintained libraries with OpenStack community adoption
2. **Configuration Management**: Plan to maintain backward compatibility during transition periods
3. **Testing Coverage**: Plan for extensive concurrency testing due to thread safety requirements
4. **Resource Planning**: Account for significant memory usage differences in planning

### Operational Recommendations
1. **Deployment Strategy**: Blue-green deployment should be planned for migration safety
2. **Monitoring**: Plan for enhanced observability to provide better operational insight
3. **Documentation**: Prepare clear migration guides to reduce operational risk
4. **Training**: Team preparation for threading model differences will be critical

### Strategic Considerations
1. **Ecosystem Alignment**: Following OpenStack community direction should pay long-term dividends
2. **Technical Debt**: Removing eventlet complexity should significantly improve maintainability
3. **Performance Characteristics**: Threading should provide more predictable operational behavior
4. **Future Compatibility**: Standard threading model should ensure better library ecosystem access

---

## Future Considerations

### Short-term (0-6 months)
- Monitor thread pool utilization and optimize sizing
- Gather performance metrics for further optimization
- Collect operational feedback and refine procedures

### Medium-term (6-18 months)
- Evaluate additional threading optimizations
- Consider async/await patterns for specific use cases
- Assess community feedback and lessons learned

### Long-term (18+ months)
- Evaluate next-generation concurrency models (async/await)
- Consider further performance optimizations
- Share experiences with OpenStack community

---

## Decision Summary

The proposed migration from Eventlet to native Python threading would represent a significant architectural evolution for Masakari. The decisions proposed here provide a foundation for implementation planning while addressing the critical constraints and considerations identified during analysis.

**Overall Assessment**: The proposed migration approach should address the identified constraints while delivering significant operational and development benefits. The decision framework and implementation strategy appear well-founded and technically sound.