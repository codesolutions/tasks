"""
Core application modules.

This package contains the core application logic including data management,
state management, and the main application class.
"""

from .data_manager import data_manager, DataManager
from .command_handler import command_handler, handle_input_new

__all__ = [
    'data_manager',
    'DataManager',
    'command_handler',
    'handle_input_new'
]
