"""
Logging configuration for void-tray.

Provides structured logging with debug/trace levels controlled by
RSS_TRAY_DEBUG environment variable.
"""

import logging
import os
import sys
from typing import Optional


def get_logger(name: str) -> logging.Logger:
    """
    Get a configured logger instance.

    Args:
        name: Logger name (typically __name__)

    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(name)

    # Only configure if handlers haven't been added yet
    if not logger.handlers:
        # Set level based on environment variable
        debug_level = os.environ.get('RSS_TRAY_DEBUG', '0')
        if debug_level in ('1', 'true', 'yes', '2'):
            level = logging.DEBUG
        else:
            level = logging.INFO

        logger.setLevel(level)

        # Create console handler if none exists
        if not logger.handlers:
            handler = logging.StreamHandler(sys.stderr)
            formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                datefmt='%H:%M:%S'
            )
            handler.setFormatter(formatter)
            logger.addHandler(handler)

        # Prevent propagation to root logger to avoid duplicate messages
        logger.propagate = False

    return logger