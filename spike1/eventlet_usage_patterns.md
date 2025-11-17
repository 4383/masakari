# Eventlet Usage Patterns in Masakari

## Executive Summary

This document details the Eventlet usage patterns currently present in the Masakari codebase as part of the preliminary investigation for potential migration to native Python threading. This analysis provides the foundation for understanding integration points, complexity, and migration requirements.

## Methodology

The analysis is being conducted through:
1. Code scanning for eventlet imports and usage patterns
2. Identifying green thread implementations
3. Mapping WSGI server integration points
4. Analyzing RPC executor configurations
5. Tracing context propagation mechanisms

## Identified Eventlet Usage Patterns

### 1. Monkey Patching (`masakari/__init__.py` and `masakari/cmd/__init__.py`)

**Pattern**: Global environment setup and monkey patching
- **File**: `masakari/__init__.py:25-35`
- **Usage**:
  ```python
  import os
  os.environ['EVENTLET_NO_GREENDNS'] = 'yes'
  import eventlet
  ```
- **File**: `masakari/cmd/__init__.py:16-17`
- **Usage**:
  ```python
  import eventlet
  eventlet.monkey_patch()
  ```
- **Purpose**: Initialize eventlet environment and monkey patch standard library modules for green threading compatibility

### 2. WSGI Server Integration (`masakari/api/wsgi.py`)

**Pattern**: Eventlet-based WSGI server implementation
- **File**: `masakari/api/wsgi.py:22-26, 57-179`
- **Key Components**:
  - `eventlet.wsgi.server` for HTTP request handling
  - `eventlet.GreenPool` for connection management
  - `eventlet.listen` for socket binding
  - `eventlet.wrap_ssl` for SSL termination
  - `eventlet.wsgi.HttpProtocol` for HTTP protocol handling
- **Usage Pattern**:
  ```python
  self._pool = eventlet.GreenPool(self.pool_size)
  wsgi_kwargs = {
      'func': eventlet.wsgi.server,
      'sock': dup_socket,
      'site': self.app,
      'protocol': self._protocol,
      'custom_pool': self._pool,
  }
  ```

### 3. Green Thread Pool Management

**Pattern**: Concurrent execution using green threads
- **File**: `masakari/api/wsgi.py:67`
- **Usage**: `self._pool = eventlet.GreenPool(self.pool_size)`
- **Purpose**:
  - Manage concurrent HTTP request processing
  - Control resource usage through pool sizing
  - Provide non-blocking I/O operations

### 4. Socket Management and Network I/O

**Pattern**: Green socket creation and configuration
- **File**: `masakari/api/wsgi.py:78-85`
- **Usage**:
  ```python
  self._socket = eventlet.listen(bind_addr, family, backlog=backlog)
  dup_socket = self._socket.dup()
  ```
- **Purpose**:
  - Create non-blocking sockets
  - Handle concurrent connections
  - Manage socket lifecycle

### 5. SSL Integration

**Pattern**: SSL wrapper using eventlet SSL handling
- **File**: `masakari/api/wsgi.py:126-140`
- **Usage**:
  ```python
  dup_socket = eventlet.wrap_ssl(dup_socket, **ssl_kwargs)
  ```
- **Purpose**: Secure HTTP connections using eventlet's SSL implementation

### 6. RPC Executor Configuration

**Pattern**: RPC backend selection for message processing
- **File**: `masakari/rpc.py:46`
- **Usage**: `rpc_executor = 'eventlet'` in RPC configuration
- **Purpose**: Use eventlet-based RPC executor for oslo.messaging

### 7. Utility Functions for Async Operations

**Pattern**: Spawn functions for background operations
- **File**: `masakari/utils.py:128-142`
- **Usage**:
  ```python
  def spawn(func, *args, **kwargs):
      # Eventlet-based spawning for background tasks
  ```
- **Purpose**: Create green threads for background processing

### 8. Context Propagation

**Pattern**: OpenStack request context handling across green threads
- **Integration**: Throughout service layer for maintaining request context
- **Purpose**: Ensure proper context propagation in async operations

### 9. Monkey Patch Configuration

**Pattern**: Selective monkey patching through configuration
- **File**: `masakari/conf/base.py:22-47`
- **Configuration Options**:
  - `monkey_patch`: Boolean flag to enable/disable monkey patching
  - `monkey_patch_modules`: List of modules to patch
- **Default**: `['masakari.api:masakari.cmd']`

### 10. Test Framework Integration

**Pattern**: Eventlet integration in testing infrastructure
- **File**: `masakari/tests/unit/__init__.py:16-19`
- **Usage**:
  ```python
  import eventlet
  eventlet.monkey_patch(os=False)
  ```
- **Purpose**: Enable eventlet in test environment while avoiding OS module conflicts

## Integration Points Summary

| Component | Files Affected | Usage Type | Complexity |
|-----------|---------------|------------|------------|
| WSGI Server | `masakari/api/wsgi.py` | Core infrastructure | High |
| Service Initialization | `masakari/__init__.py`, `masakari/cmd/__init__.py` | Environment setup | Medium |
| RPC System | `masakari/rpc.py` | Message processing | Low |
| Utilities | `masakari/utils.py` | Background operations | Medium |
| Configuration | `masakari/conf/base.py` | Runtime behavior | Low |
| Testing | `masakari/tests/unit/__init__.py` | Test environment | Low |

## Dependencies Identified

### Direct Dependencies
- **eventlet**: Core green threading library
- **greenlet**: Low-level coroutine support (dependency of eventlet)

### Indirect Dependencies
- **oslo.service**: Service framework with eventlet integration
- **oslo.messaging**: RPC system with eventlet executor option

## Architectural Patterns

### 1. Green Thread Pool Pattern
- Central pool management for concurrent operations
- Resource control through pool sizing
- Non-blocking I/O operations

### 2. Monkey Patching Pattern
- Global modification of standard library behavior
- Transparent green thread integration
- Module-level configuration control

### 3. WSGI Server Pattern
- Eventlet-based HTTP server implementation
- Custom protocol handling
- SSL termination integration

### 4. Context Preservation Pattern
- OpenStack context propagation across green threads
- Request tracing and correlation
- Security context maintenance

## Migration Readiness Assessment

Based on this analysis, the following areas are identified as candidates for migration:

1. **WSGI Server**: Well-defined interface, appears to have clear migration path to threading-based server
2. **RPC Executor**: Simple configuration change to threading executor
3. **Utility Functions**: Direct mapping to ThreadPoolExecutor appears feasible
4. **Service Initialization**: Removal of monkey patching infrastructure should be straightforward

## Conclusion

The analysis reveals that Eventlet is deeply integrated into Masakari's core infrastructure, particularly in the WSGI server implementation. However, the usage patterns are well-defined and follow standard OpenStack conventions, suggesting that migration would be feasible with proper planning and execution.

This comprehensive mapping of Eventlet usage provides the foundation for detailed risk assessment and migration planning. The next phase should focus on evaluating specific migration strategies for each identified integration point.