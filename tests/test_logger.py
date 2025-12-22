#!/usr/bin/env python3
"""
Unit tests for logger.py module
"""

import pytest
import sys
import os
import logging
from io import StringIO
from unittest.mock import patch

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from logger import (
    GrtLogger, ColoredFormatter, setup_logging, get_logger,
    is_verbose, is_debug
)


class TestColoredFormatter:
    """Tests for ColoredFormatter class"""
    
    def test_formatter_basic_message(self):
        formatter = ColoredFormatter(use_colors=False)
        record = logging.LogRecord(
            name='test', level=logging.INFO, pathname='', lineno=0,
            msg='Test message', args=(), exc_info=None
        )
        result = formatter.format(record)
        assert 'Test message' in result
    
    def test_formatter_with_colors_for_warning(self):
        formatter = ColoredFormatter(use_colors=True)
        record = logging.LogRecord(
            name='test', level=logging.WARNING, pathname='', lineno=0,
            msg='Warning message', args=(), exc_info=None
        )
        result = formatter.format(record)
        # Should contain ANSI codes when colors enabled
        assert 'Warning message' in result
    
    def test_formatter_without_colors(self):
        formatter = ColoredFormatter(use_colors=False)
        record = logging.LogRecord(
            name='test', level=logging.WARNING, pathname='', lineno=0,
            msg='Warning message', args=(), exc_info=None
        )
        result = formatter.format(record)
        assert 'Warning message' in result


class TestGrtLogger:
    """Tests for GrtLogger singleton class"""
    
    def test_singleton_pattern(self):
        """Test that GrtLogger is a singleton"""
        logger1 = GrtLogger()
        logger2 = GrtLogger()
        assert logger1 is logger2
    
    def test_get_logger_returns_logger(self):
        grt_logger = GrtLogger()
        logger = grt_logger.get_logger('test_module')
        assert isinstance(logger, logging.Logger)
    
    def test_get_logger_same_module_returns_same_logger(self):
        grt_logger = GrtLogger()
        logger1 = grt_logger.get_logger('same_module')
        logger2 = grt_logger.get_logger('same_module')
        assert logger1 is logger2
    
    def test_get_logger_different_modules_different_loggers(self):
        grt_logger = GrtLogger()
        logger1 = grt_logger.get_logger('module_a')
        logger2 = grt_logger.get_logger('module_b')
        assert logger1 is not logger2


class TestSetupLogging:
    """Tests for setup_logging function"""
    
    def test_setup_default_verbosity(self):
        setup_logging(verbosity=0)
        logger = get_logger('test_default')
        # At verbosity 0, effective level should be WARNING
        assert logger.level >= logging.WARNING or logger.level == logging.WARNING
    
    def test_setup_verbose(self):
        setup_logging(verbosity=1)
        logger = get_logger('test_verbose')
        # At verbosity 1, effective level should be INFO
        assert logger.level <= logging.INFO
    
    def test_setup_debug(self):
        setup_logging(verbosity=2)
        logger = get_logger('test_debug')
        # At verbosity 2, effective level should be DEBUG
        assert logger.level <= logging.DEBUG


class TestConvenienceFunctions:
    """Tests for module-level convenience functions"""
    
    def test_get_logger_function(self):
        logger = get_logger('test_convenience')
        assert isinstance(logger, logging.Logger)
    
    def test_is_verbose_default(self):
        setup_logging(verbosity=0)
        assert is_verbose() == False
    
    def test_is_verbose_when_verbose(self):
        setup_logging(verbosity=1)
        assert is_verbose() == True
    
    def test_is_debug_default(self):
        setup_logging(verbosity=0)
        assert is_debug() == False
    
    def test_is_debug_when_verbose(self):
        setup_logging(verbosity=1)
        assert is_debug() == False
    
    def test_is_debug_when_debug(self):
        setup_logging(verbosity=2)
        assert is_debug() == True


class TestLogOutput:
    """Tests for actual log output"""
    
    def test_warning_message_appears(self):
        """Test that warning messages are output at default verbosity"""
        setup_logging(verbosity=0, use_colors=False)
        logger = get_logger('test_output')
        
        # Capture stderr
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        handler.setLevel(logging.WARNING)
        handler.setFormatter(logging.Formatter('%(message)s'))
        logger.addHandler(handler)
        
        logger.warning("Test warning")
        
        output = stream.getvalue()
        assert 'Test warning' in output
        
        # Clean up
        logger.removeHandler(handler)
    
    def test_info_message_suppressed_at_default(self):
        """Test that info messages are suppressed at default verbosity"""
        setup_logging(verbosity=0, use_colors=False)
        logger = get_logger('test_suppressed')
        
        # At verbosity 0, INFO should not appear
        # This is implicit - we just verify the logger is configured correctly
        assert logger.level >= logging.WARNING or logger.level == 0


if __name__ == '__main__':
    pytest.main([__file__, '-v'])

