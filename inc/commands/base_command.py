"""
Base command class and command system interfaces.

This module defines the command pattern interfaces used throughout
the application for handling user commands.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass


@dataclass
class CommandContext:
    """
    Context object passed to commands containing UI and state information.
    
    Attributes:
        stdscr: The curses screen object
        selected_subtask_idx: Currently selected subtask index
        current_view: Current view mode
        entity_for_dedicated_notes: Entity context for notes view
        show_help_footer: Whether to show help footer
        data_lock: Threading lock for data access
    """
    stdscr: Any
    selected_subtask_idx: int = -1
    current_view: str = "main"
    entity_for_dedicated_notes: Optional[Dict[str, Any]] = None
    show_help_footer: bool = True
    data_lock: Optional[Any] = None
    current_ticket_subtask_list: Optional[List[Tuple[str, Dict[str, Any]]]] = None


@dataclass
class CommandResult:
    """
    Result object returned by command execution.
    
    Attributes:
        success: Whether the command executed successfully
        data_modified: Whether the data was modified and needs saving
        view_change: New view to switch to, if any
        message: Success/error message to display
        request_redraw: Whether UI should be redrawn
    """
    success: bool = True
    data_modified: bool = False
    view_change: Optional[str] = None
    message: Optional[str] = None
    request_redraw: bool = False


class BaseCommand(ABC):
    """
    Abstract base class for all commands.
    
    All commands should inherit from this class and implement the execute method.
    """
    
    @abstractmethod
    def execute(self, data: Dict[str, Any], args: List[str], context: CommandContext) -> CommandResult:
        """
        Execute the command.
        
        Args:
            data: The application data dictionary
            args: Command arguments (first element is the command name)
            context: Command execution context
            
        Returns:
            CommandResult indicating the outcome
        """
        pass
    
    @abstractmethod
    def get_usage(self) -> str:
        """
        Get usage information for this command.
        
        Returns:
            Usage string for help display
        """
        pass
    
    def validate_args(self, args: List[str]) -> Tuple[bool, Optional[str]]:
        """
        Validate command arguments.
        
        Args:
            args: Command arguments to validate
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        return True, None


class CommandRegistry:
    """
    Registry for managing command instances and dispatch.
    """
    
    def __init__(self):
        self._commands: Dict[str, BaseCommand] = {}
        self._aliases: Dict[str, str] = {}
    
    def register(self, command_name: str, command: BaseCommand, aliases: Optional[List[str]] = None):
        """
        Register a command with optional aliases.
        
        Args:
            command_name: Primary command name
            command: Command instance
            aliases: Optional list of alias names
        """
        self._commands[command_name] = command
        
        if aliases:
            for alias in aliases:
                self._aliases[alias] = command_name
    
    def get_command(self, name: str) -> Optional[BaseCommand]:
        """
        Get a command by name or alias.
        
        Args:
            name: Command name or alias
            
        Returns:
            Command instance or None if not found
        """
        # Check if it's an alias first
        actual_name = self._aliases.get(name, name)
        return self._commands.get(actual_name)
    
    def get_all_commands(self) -> Dict[str, BaseCommand]:
        """
        Get all registered commands.
        
        Returns:
            Dictionary mapping command names to command instances
        """
        return self._commands.copy()
    
    def execute_command(self, command_line: str, data: Dict[str, Any], context: CommandContext) -> CommandResult:
        """
        Parse and execute a command line.
        
        Args:
            command_line: Raw command line input
            data: Application data
            context: Command context
            
        Returns:
            CommandResult from the executed command
        """
        if not command_line.strip():
            return CommandResult(success=False, message="Empty command")
        
        parts = command_line.strip().split()
        command_name = parts[0].lower()
        
        command = self.get_command(command_name)
        if not command:
            return CommandResult(success=False, message=f"Unknown command: {command_name}")
        
        # Validate arguments
        is_valid, error_msg = command.validate_args(parts)
        if not is_valid:
            return CommandResult(success=False, message=error_msg or "Invalid arguments")
        
        try:
            return command.execute(data, parts, context)
        except Exception as e:
            return CommandResult(success=False, message=f"Command error: {str(e)}")


# Global command registry instance
command_registry = CommandRegistry()
