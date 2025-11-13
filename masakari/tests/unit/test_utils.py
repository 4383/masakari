#    Copyright 2016 NTT DATA
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

from unittest import mock

from oslo_config import cfg
from oslo_context import context as common_context
from oslo_context import fixture as context_fixture

from masakari import context
from masakari import exception
from masakari.tests.unit import base
from masakari import utils

CONF = cfg.CONF


class UTF8TestCase(base.NoDBTestCase):
    def test_none_value(self):
        self.assertIsInstance(utils.utf8(None), type(None))

    def test_bytes_value(self):
        some_value = b"fake data"
        return_value = utils.utf8(some_value)
        # check that type of returned value doesn't changed
        self.assertIsInstance(return_value, type(some_value))
        self.assertEqual(some_value, return_value)

    def test_not_text_type(self):
        return_value = utils.utf8(1)
        self.assertEqual(b"1", return_value)
        self.assertIsInstance(return_value, bytes)

    def test_text_type_with_encoding(self):
        some_value = 'test\u2026config'
        self.assertEqual(some_value, utils.utf8(some_value).decode("utf-8"))


class ExpectedArgsTestCase(base.NoDBTestCase):
    def test_passes(self):
        @utils.expects_func_args('foo', 'baz')
        def dec(f):
            return f

        @dec
        def func(foo, bar, baz="lol"):
            pass

        # Call to ensure nothing errors
        func(None, None)

    def test_raises(self):
        @utils.expects_func_args('foo', 'baz')
        def dec(f):
            return f

        def func(bar, baz):
            pass

        self.assertRaises(TypeError, dec, func)

    def test_var_no_of_args(self):
        @utils.expects_func_args('foo')
        def dec(f):
            return f

        @dec
        def func(bar, *args, **kwargs):
            pass

        # Call to ensure nothing errors
        func(None)

    def test_more_layers(self):
        @utils.expects_func_args('foo', 'baz')
        def dec(f):
            return f

        def dec_2(f):
            def inner_f(*a, **k):
                return f()
            return inner_f

        @dec_2
        def func(bar, baz):
            pass

        self.assertRaises(TypeError, dec, func)


class SpawnNTestCase(base.NoDBTestCase):
    def setUp(self):
        super(SpawnNTestCase, self).setUp()
        self.useFixture(context_fixture.ClearRequestContext())
        self.spawn_name = 'spawn_n'

    def tearDown(self):
        super(SpawnNTestCase, self).tearDown()
        # Clean up global thread pools to prevent test hangs
        cleanup_thread_pools()

    def test_spawn_n_no_context(self):
        self.assertIsNone(common_context.get_current())

        def _fake_spawn(func, *args, **kwargs):
            # call the method to ensure no error is raised
            func(*args, **kwargs)
            self.assertEqual('test', args[0])

        def fake(arg):
            pass

        with mock.patch(
                'masakari.utils._get_general_executor') as mock_executor:
            mock_executor.return_value.submit = _fake_spawn
            getattr(utils, self.spawn_name)(fake, 'test')
        self.assertIsNone(common_context.get_current())

    def test_spawn_n_context(self):
        self.assertIsNone(common_context.get_current())
        ctxt = context.RequestContext('user', 'project')

        def _fake_spawn(func, *args, **kwargs):
            # call the method to ensure no error is raised
            func(*args, **kwargs)
            self.assertEqual(ctxt, args[0])
            self.assertEqual('test', kwargs['kwarg1'])

        def fake(context, kwarg1=None):
            pass

        with mock.patch(
                'masakari.utils._get_general_executor') as mock_executor:
            mock_executor.return_value.submit = _fake_spawn
            getattr(utils, self.spawn_name)(fake, ctxt, kwarg1='test')
        self.assertEqual(ctxt, common_context.get_current())

    def test_spawn_n_context_different_from_passed(self):
        self.assertIsNone(common_context.get_current())
        ctxt = context.RequestContext('user', 'project')
        ctxt_passed = context.RequestContext('user', 'project',
                overwrite=False)
        self.assertEqual(ctxt, common_context.get_current())

        def _fake_spawn(func, *args, **kwargs):
            # call the method to ensure no error is raised
            func(*args, **kwargs)
            self.assertEqual(ctxt_passed, args[0])
            self.assertEqual('test', kwargs['kwarg1'])

        def fake(context, kwarg1=None):
            pass

        with mock.patch(
                'masakari.utils._get_general_executor') as mock_executor:
            mock_executor.return_value.submit = _fake_spawn
            getattr(utils, self.spawn_name)(fake, ctxt_passed, kwarg1='test')
        self.assertEqual(ctxt, common_context.get_current())


class SpawnTestCase(SpawnNTestCase):
    def setUp(self):
        super(SpawnTestCase, self).setUp()
        self.spawn_name = 'spawn'


class ValidateIntegerTestCase(base.NoDBTestCase):
    def test_exception_converted(self):
        self.assertRaises(exception.InvalidInput,
                          utils.validate_integer,
                          "im-not-an-int", "not-an-int")
        self.assertRaises(exception.InvalidInput,
                          utils.validate_integer,
                          3.14, "Pie")
        self.assertRaises(exception.InvalidInput,
                          utils.validate_integer,
                          "299", "Sparta no-show",
                          min_value=300, max_value=300)
        self.assertRaises(exception.InvalidInput,
                          utils.validate_integer,
                          55, "doing 55 in a 54",
                          max_value=54)
        self.assertRaises(exception.InvalidInput,
                          utils.validate_integer,
                          chr(129), "UnicodeError",
                          max_value=1000)


# Test utility functions for thread pool cleanup

def cleanup_thread_pools():
    """Cleanup global thread pool executors.

    This function is intended for testing to ensure proper cleanup between
    test runs. It performs aggressive cleanup to prevent test interference.

    Note: This aggressive cleanup is necessary for tests because:
    - Thread pools may have long-running tasks that don't finish cleanly
    - Residual threads from previous tests can interfere with new tests
    - Some tests may not properly wait for async operations to complete
    - Global executors are shared across test cases and need forced reset
    """
    # Import here to avoid circular dependencies
    from masakari import utils

    # Access the global variables directly from utils module
    with utils._executor_lock:
        # Shutdown executors if they exist
        for executor_name, executor in [
            ('_general_executor', utils._general_executor),
            ('_notification_executor', utils._notification_executor),
            ('_driver_executor', utils._driver_executor)
        ]:
            if executor is not None:
                try:
                    # Use proper shutdown method with wait=True for graceful cleanup
                    # This ensures all pending tasks complete before proceeding
                    executor.shutdown(wait=True)
                except Exception:
                    # Ignore shutdown errors - executor may already be shutdown
                    pass

        # Reset global variables
        utils._general_executor = None
        utils._notification_executor = None
        utils._driver_executor = None

        # Force garbage collection to clean up any remaining references
        import gc
        gc.collect()
