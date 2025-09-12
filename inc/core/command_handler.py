"""
Command handler integration for the main application.

This module provides the new command handling system that integrates
the command registry with the main application loop.
"""

from typing import Dict, Any, List, Tuple, Optional, Union

from inc.commands.base_command import CommandContext, CommandResult, command_registry
from inc.commands.task_commands import SwitchTaskCommand
from inc.helpers import t
from inc.utils.constants import VIEW_MAIN


class CommandHandler:
    """
    Handles command processing for the main application.
    """
    
    def __init__(self):
        self.switch_task_command = SwitchTaskCommand()
    
    def handle_command(self, 
                      command_buffer: str, 
                      data: Dict[str, Any],
                      context: CommandContext) -> 'CommandHandleResult':
        """
        Handle a command using the command registry system.
        
        Args:
            command_buffer: The raw command input
            data: Application data dictionary
            context: Command execution context
            
        Returns:
            CommandHandleResult with processing information
        """
        if not command_buffer.strip():
            return CommandHandleResult(success=False, message="Empty command")
        
        # Handle special view contexts first
        if context.current_view != VIEW_MAIN:
            return self._handle_non_main_view_command(command_buffer, data, context)
        
        command_parts = command_buffer.strip().split()
        command_name = command_parts[0].lower()
        
        # Try to execute the command through the registry
        result = command_registry.execute_command(command_buffer, data, context)
        
        if result.success:
            return self._convert_command_result(result, data)
        else:
            # If no explicit command found, try task switching
            return self._try_task_switching(command_parts, data, context)
    
    def _handle_non_main_view_command(self, command_buffer: str, data: Dict[str, Any], context: CommandContext) -> 'CommandHandleResult':
        """Handle commands in non-main views."""
        command_parts = command_buffer.strip().split()
        command_name = command_parts[0].lower() if command_parts else ""
        
        # Allow quit and help in all views
        if command_name == 'q':
            return CommandHandleResult(quit_requested=True)
        elif command_name == 'h':
            return CommandHandleResult(toggle_help=True)
        elif command_name == 'd' and getattr(context, 'selected_note_idx', -1) != -1:
            return CommandHandleResult(delete_note=True)
        
        # All other commands only work in main view
        return CommandHandleResult(
            success=False,
            message=t('cmd_exclusively_in_main_view')
        )
    
    def _convert_command_result(self, result: CommandResult, data: Dict[str, Any]) -> 'CommandHandleResult':
        """Convert CommandResult to CommandHandleResult."""
        handle_result = CommandHandleResult(
            success=result.success,
            message=result.message,
            data_modified=result.data_modified,
            request_redraw=result.request_redraw
        )
        
        # Handle special messages
        if result.message == "TOGGLE_HELP":
            handle_result.toggle_help = True
        elif result.message == "QUIT":
            handle_result.quit_requested = True
        elif result.message == "RESTART_FOR_LOGIN":
            handle_result.restart_for_login = True
        elif result.message == "DELETE_NOTE":
            handle_result.delete_note = True
        
        # Handle view changes
        if result.view_change:
            handle_result.view_change = result.view_change
        
        return handle_result
    
    def _try_task_switching(self, command_parts: List[str], data: Dict[str, Any], context: CommandContext) -> 'CommandHandleResult':
        """Try to switch tasks using the SwitchTaskCommand."""
        try:
            result = self.switch_task_command.execute(data, command_parts, context)
            return self._convert_command_result(result, data)
        except Exception as e:
            return CommandHandleResult(
                success=False,
                message=t('cmd_err_unknown_command_or_ticket', id=" ".join(command_parts))
            )


class CommandHandleResult:
    """
    Result object for command handling that's compatible with the old system.
    """
    
    def __init__(self,
                 success: bool = True,
                 message: Optional[str] = None,
                 data_modified: bool = False,
                 request_redraw: bool = False,
                 view_change: Optional[str] = None,
                 toggle_help: bool = False,
                 quit_requested: bool = False,
                 restart_for_login: bool = False,
                 delete_note: bool = False):
        self.success = success
        self.message = message
        self.data_modified = data_modified
        self.request_redraw = request_redraw
        self.view_change = view_change
        self.toggle_help = toggle_help
        self.quit_requested = quit_requested
        self.restart_for_login = restart_for_login
        self.delete_note = delete_note


# Global command handler instance
command_handler = CommandHandler()


def handle_input_new(data: Dict[str, Any], 
                     command_parts: List[str], 
                     stdscr, 
                     current_view_mode: str, 
                     selected_subtask_idx: int, 
                     selected_note_idx: int,
                     current_ticket_subtask_list: List[Tuple[str, Dict[str, Any]]], 
                     all_displayable_tickets: List[str]) -> Union[Dict[str, Any], str, None]:
    """
    New command handler that uses the command registry system.
    
    This replaces the old massive handle_input() function with a clean,
    modular approach using the command pattern.
    
    Args:
        data: Application data dictionary
        command_parts: Command split into parts
        stdscr: Curses screen object
        current_view_mode: Current view mode
        selected_subtask_idx: Currently selected subtask index
        selected_note_idx: Currently selected note index  
        current_ticket_subtask_list: List of current subtasks
        all_displayable_tickets: List of displayable tickets
        
    Returns:
        Same return format as old handle_input for compatibility:
        - None: Quit requested
        - "NO_CHANGE": No data changes
        - "TOGGLE_HELP": Toggle help display
        - "VIEW_TIME_LOG": Switch to time log view
        - "RESTART_FOR_LOGIN": Restart for login
        - Dict: Updated data dictionary
    """
    # Create command context
    context = CommandContext(
        stdscr=stdscr,
        selected_subtask_idx=selected_subtask_idx,
        current_view=current_view_mode,
        show_help_footer=True,
        current_ticket_subtask_list=current_ticket_subtask_list
    )
    # Add selected_note_idx as dynamic attribute since it's not in the dataclass
    context.selected_note_idx = selected_note_idx
    
    # Handle the command
    command_buffer = " ".join(command_parts)
    result = command_handler.handle_command(command_buffer, data, context)
    
    # Convert result to old format for compatibility
    if result.quit_requested:
        return None
    elif result.restart_for_login:
        return "RESTART_FOR_LOGIN"
    elif result.toggle_help:
        return "TOGGLE_HELP"
    elif result.view_change == "time_log":
        return "VIEW_TIME_LOG"
    elif result.delete_note:
        return "DELETE_NOTE"
    elif result.data_modified:
        return data
    else:
        return "NO_CHANGE"
