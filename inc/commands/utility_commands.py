"""
Utility and navigation commands.

This module contains commands for help, quit, notes management,
event scheduling, and other utility functions.
"""

from datetime import datetime, date, timedelta
from typing import List

from inc.commands.base_command import BaseCommand, CommandContext, CommandResult
from inc.helpers import t
from inc.utils.constants import WEEKDAY_MAP


class HelpCommand(BaseCommand):
    """Command to toggle help display."""
    
    def execute(self, data, args: List[str], context: CommandContext) -> CommandResult:
        return CommandResult(
            success=True,
            message="TOGGLE_HELP",  # Special return value to signal help toggle
            request_redraw=True
        )
    
    def get_usage(self) -> str:
        return "h - Toggle help display"


class QuitCommand(BaseCommand):
    """Command to quit the application."""
    
    def execute(self, data, args: List[str], context: CommandContext) -> CommandResult:
        return CommandResult(
            success=True,
            message="QUIT",  # Special return value to signal quit
        )
    
    def get_usage(self) -> str:
        return "q - Quit the application"


class LoginCommand(BaseCommand):
    """Command to restart for login/session refresh."""
    
    def execute(self, data, args: List[str], context: CommandContext) -> CommandResult:
        return CommandResult(
            success=True,
            message="RESTART_FOR_LOGIN",  # Special return value
        )
    
    def get_usage(self) -> str:
        return "login - Restart application to refresh sessions"


class AddNoteCommand(BaseCommand):
    """Command to add a note to current task or selected subtask."""
    
    def execute(self, data, args: List[str], context: CommandContext) -> CommandResult:
        current_ticket = data.get("current_ticket")
        if not current_ticket:
            return CommandResult(
                success=False,
                message=t('cmd_err_no_active_task_for_note')
            )
        
        if len(args) < 2:
            return CommandResult(
                success=False,
                message=t('cmd_usage_add_note')
            )
        
        note_text = " ".join(args[1:])
        
        # Check if a subtask is selected
        if (context.selected_subtask_idx != -1 and
            context.current_ticket_subtask_list and
            0 <= context.selected_subtask_idx < len(context.current_ticket_subtask_list)):
            
            # Add note to selected subtask
            subtask_name, _ = context.current_ticket_subtask_list[context.selected_subtask_idx]
            
            if (current_ticket in data.get("sub_tasks", {}) and
                subtask_name in data["sub_tasks"][current_ticket]):
                
                subtask_details = data["sub_tasks"][current_ticket][subtask_name]
                if isinstance(subtask_details, dict):
                    subtask_details.setdefault("notes", []).append(note_text)
                    return CommandResult(
                        success=True,
                        data_modified=True,
                        message=t('cmd_info_note_added_to_subtask', name=subtask_name),
                        request_redraw=True
                    )
                else:
                    return CommandResult(
                        success=False,
                        message=t('cmd_err_subtask_details_not_found', name=subtask_name)
                    )
            else:
                return CommandResult(
                    success=False,
                    message=t('cmd_err_main_task_details_not_found', name=current_ticket)
                )
        else:
            # Add note to main task
            data.setdefault("notes", {}).setdefault(current_ticket, []).append(note_text)
            return CommandResult(
                success=True,
                data_modified=True,
                message=t('cmd_info_note_added_to_task', name=current_ticket),
                request_redraw=True
            )
    
    def get_usage(self) -> str:
        return "note <text> - Add note to current task or selected subtask"


class AddMeetingCommand(BaseCommand):
    """Command to add a meeting or interruption event."""
    
    def __init__(self, event_type: str = 'meeting'):
        self.event_type = event_type  # 'meeting' or 'interruption'
    
    def execute(self, data, args: List[str], context: CommandContext) -> CommandResult:
        if len(args) < 3:
            usage_msg = t('cmd_usage_add_meeting_event', command=args[0] if args else 'p')
            return CommandResult(success=False, message=usage_msg)
        
        arg1 = args[1].lower()
        is_recurring = arg1 in WEEKDAY_MAP
        
        if is_recurring:
            return self._handle_recurring_event(data, args)
        else:
            return self._handle_single_event(data, args)
    
    def get_usage(self) -> str:
        cmd = 'p' if self.event_type == 'meeting' else 'k'
        return f"{cmd} <time> <details> OR {cmd} <weekday> <time> <details> - Add {self.event_type}"
    
    def _handle_recurring_event(self, data, args):
        """Handle recurring event creation."""
        if len(args) < 4:
            usage_msg = t('cmd_usage_add_meeting_event', command=args[0])
            return CommandResult(success=False, message=usage_msg)
        
        weekday_str = args[1].lower()
        time_str = args[2]
        details = " ".join(args[3:])
        
        try:
            datetime.strptime(time_str, "%H:%M")  # Validate time format
            weekday_int = WEEKDAY_MAP[weekday_str]
            
            recurring_event = {
                'type': self.event_type,
                'weekday': weekday_int,
                'time': time_str,
                'details': details
            }
            data.setdefault("recurring_events", []).append(recurring_event)
            
            return CommandResult(
                success=True,
                data_modified=True,
                message=t('cmd_info_recurring_event_added',
                         type=self.event_type,
                         day=weekday_str.upper(),
                         time=time_str),
                request_redraw=True
            )
        except ValueError:
            return CommandResult(
                success=False,
                message=t('cmd_err_invalid_time', time=time_str)
            )
    
    def _handle_single_event(self, data, args):
        """Handle single event creation."""
        time_str = args[1]
        details = " ".join(args[2:])
        target_list_key = "meetings" if self.event_type == 'meeting' else "interruptions"
        
        try:
            time_obj = datetime.strptime(time_str, "%H:%M").time()
            event_datetime = datetime.combine(date.today(), time_obj)
            
            # If time is in the past (with 5 minute buffer), schedule for tomorrow
            if event_datetime < datetime.now() - timedelta(minutes=5):
                event_datetime += timedelta(days=1)
            
            details_key = 'link' if self.event_type == 'meeting' else 'message'
            event_data = {
                "datetime": event_datetime.isoformat(),
                details_key: details
            }
            data.setdefault(target_list_key, []).append(event_data)
            
            return CommandResult(
                success=True,
                data_modified=True,
                message=t('cmd_info_event_added',
                         type=self.event_type,
                         datetime=event_datetime.strftime('%Y-%m-%d %H:%M')),
                request_redraw=True
            )
        except ValueError:
            return CommandResult(
                success=False,
                message=t('cmd_err_invalid_time', time=time_str)
            )


class DeleteNoteCommand(BaseCommand):
    """Command to delete a selected note (used in note views)."""
    
    def execute(self, data, args: List[str], context: CommandContext) -> CommandResult:
        # This command is handled specially in the main loop for different views
        return CommandResult(
            success=True,
            message="DELETE_NOTE",  # Special return value
        )
    
    def get_usage(self) -> str:
        return "d - Delete selected note (in notes views)"


class ViewSwitchCommand(BaseCommand):
    """Generic command for switching views."""
    
    def __init__(self, target_view: str, aliases: List[str] = None):
        self.target_view = target_view
        self.aliases = aliases or []
    
    def execute(self, data, args: List[str], context: CommandContext) -> CommandResult:
        return CommandResult(
            success=True,
            view_change=self.target_view,
            request_redraw=True
        )
    
    def get_usage(self) -> str:
        main_cmd = self.aliases[0] if self.aliases else "view"
        return f"{main_cmd} - Switch to {self.target_view.replace('_', ' ')} view"


class CompletedTasksCommand(BaseCommand):
    """Command to show completed tasks."""
    
    def execute(self, data, args: List[str], context: CommandContext) -> CommandResult:
        completed = data.get("completed_tickets", [])
        if not completed:
            return CommandResult(
                success=True,
                message="No completed tasks found."
            )
        
        # Create a nice formatted list
        completed_list = "\n".join([f"✅ {task}" for task in completed])
        message = f"Completed Tasks ({len(completed)}):" + "\n" + completed_list
        
        return CommandResult(
            success=True,
            message=message
        )
    
    def get_usage(self) -> str:
        return "completed - Show list of completed tasks"


# Convenience function to create view switch commands
def create_view_switch_commands():
    """Create all view switch command instances."""
    from inc.utils.constants import (
        VIEW_MAIN, VIEW_DEDICATED_NOTES, VIEW_DAILY_NOTES, 
        VIEW_TIME_LOG, VIEW_HOURLY_CHECKIN
    )
    
    return {
        'main': ViewSwitchCommand(VIEW_MAIN, ['main']),
        'notes': ViewSwitchCommand(VIEW_DEDICATED_NOTES, ['notes']),
        'daily': ViewSwitchCommand(VIEW_DAILY_NOTES, ['daily']),
        'timelog': ViewSwitchCommand(VIEW_TIME_LOG, ['timelog', 'log']),
        'checkin': ViewSwitchCommand(VIEW_HOURLY_CHECKIN, ['checkin'])
    }
