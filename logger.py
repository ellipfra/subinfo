#!/usr/bin/env python3
"""
Structured logging for grtinfo CLI tools

Provides a centralized logging configuration with:
- Colored console output
- Configurable verbosity levels
- Optional file logging
- Structured log format
"""

import logging
import sys
import os
from typing import Optional
from pathlib import Path


class ColoredFormatter(logging.Formatter):
    """Custom formatter with ANSI color codes for terminal output"""
    
    COLORS = {
        'DEBUG': '\033[36m',     # Cyan
        'INFO': '\033[32m',      # Green
        'WARNING': '\033[33m',   # Yellow
        'ERROR': '\033[91m',     # Bright Red
        'CRITICAL': '\033[95m',  # Bright Magenta
    }
    RESET = '\033[0m'
    DIM = '\033[2m'
    
    def __init__(self, fmt: str = None, use_colors: bool = True):
        super().__init__(fmt or '%(message)s')
        self.use_colors = use_colors and sys.stderr.isatty()
    
    def format(self, record: logging.LogRecord) -> str:
        if self.use_colors:
            color = self.COLORS.get(record.levelname, '')
            # For DEBUG, show the level name; for others, just the message
            if record.levelno == logging.DEBUG:
                record.msg = f"{self.DIM}[{record.levelname}] {record.msg}{self.RESET}"
            elif record.levelno >= logging.WARNING:
                record.msg = f"{color}{record.msg}{self.RESET}"
        return super().format(record)


class GrtLogger:
    """Centralized logger for grtinfo tools
    
    Usage:
        from logger import get_logger
        log = get_logger(__name__)
        
        log.debug("Fetching data...")      # Only shown with -vv
        log.info("Found 5 indexers")       # Only shown with -v
        log.warning("Rate limit reached")  # Always shown
        log.error("Connection failed")     # Always shown
    """
    
    _instance: Optional['GrtLogger'] = None
    _initialized: bool = False
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if GrtLogger._initialized:
            return
        
        self._verbosity = 0
        self._loggers = {}
        self._handler = None
        self._file_handler = None
        GrtLogger._initialized = True
    
    def setup(
        self,
        verbosity: int = 0,
        log_file: Optional[str] = None,
        use_colors: bool = True
    ):
        """Configure logging settings
        
        Args:
            verbosity: 0=WARNING+, 1=INFO+, 2=DEBUG+
            log_file: Optional path to log file
            use_colors: Whether to use colored output (default True)
        """
        self._verbosity = verbosity
        
        # Map verbosity to log level
        if verbosity >= 2:
            level = logging.DEBUG
        elif verbosity >= 1:
            level = logging.INFO
        else:
            level = logging.WARNING
        
        # Create console handler
        self._handler = logging.StreamHandler(sys.stderr)
        self._handler.setLevel(level)
        self._handler.setFormatter(ColoredFormatter(use_colors=use_colors))
        
        # Create file handler if requested
        if log_file:
            log_path = Path(log_file)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            self._file_handler = logging.FileHandler(log_file)
            self._file_handler.setLevel(logging.DEBUG)
            self._file_handler.setFormatter(logging.Formatter(
                '%(asctime)s [%(levelname)s] %(name)s: %(message)s'
            ))
        
        # Update all existing loggers
        for logger in self._loggers.values():
            self._configure_logger(logger, level)
    
    def _configure_logger(self, logger: logging.Logger, level: int):
        """Configure a logger with current settings"""
        logger.setLevel(level)
        logger.handlers.clear()
        
        if self._handler:
            logger.addHandler(self._handler)
        if self._file_handler:
            logger.addHandler(self._file_handler)
        
        # Prevent propagation to root logger
        logger.propagate = False
    
    def get_logger(self, name: str) -> logging.Logger:
        """Get or create a logger for the given module name"""
        if name not in self._loggers:
            logger = logging.getLogger(f"grtinfo.{name}")
            
            # Set default level if not yet configured
            level = logging.WARNING
            if self._verbosity >= 2:
                level = logging.DEBUG
            elif self._verbosity >= 1:
                level = logging.INFO
            
            self._configure_logger(logger, level)
            self._loggers[name] = logger
        
        return self._loggers[name]
    
    @property
    def verbosity(self) -> int:
        return self._verbosity


# Module-level convenience functions
_grt_logger = GrtLogger()


def setup_logging(
    verbosity: int = 0,
    log_file: Optional[str] = None,
    use_colors: bool = True
):
    """Configure global logging settings
    
    Call this early in main() to set up logging.
    
    Args:
        verbosity: 0=WARNING+, 1=INFO+, 2=DEBUG+
        log_file: Optional path to log file
        use_colors: Whether to use colored output
    """
    _grt_logger.setup(verbosity, log_file, use_colors)


def get_logger(name: str) -> logging.Logger:
    """Get a logger for the given module
    
    Args:
        name: Module name (typically __name__)
    
    Returns:
        Configured logger instance
    """
    return _grt_logger.get_logger(name)


def is_verbose() -> bool:
    """Check if verbose mode is enabled (verbosity >= 1)"""
    return _grt_logger.verbosity >= 1


def is_debug() -> bool:
    """Check if debug mode is enabled (verbosity >= 2)"""
    return _grt_logger.verbosity >= 2

