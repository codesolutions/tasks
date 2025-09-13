"""
Time tracking commands.

This module contains commands for managing work sessions and time logging:
starting/ending work days, pausing/resuming work, logging time, etc.
"""

from datetime import datetime, date
from typing import List

from inc.commands.base_command import BaseCommand, CommandContext, CommandResult
from inc.helpers import t
from inc.utils.constants import VIEW_TIME_LOG


class StartDayCommand(BaseCommand):
    """Command to start a work day session."""
    
    def execute(self, data, args: List[str], context: CommandContext) -> CommandResult:
        work_session = data.setdefault("work_session", {})
        
        if work_session.get("active"):
            return CommandResult(
                success=False,
                message=t('work_session_already_active')
            )
        
        work_session["active"] = True
        work_session["start_time"] = datetime.now().isoformat()
        work_session["current_timer_start_ts"] = datetime.now().timestamp()
        work_session["last_activity_ts"] = datetime.now().timestamp()
        
        return CommandResult(
            success=True,
            data_modified=True,
            message=t('work_session_started'),
            request_redraw=True
        )
    
    def get_usage(self) -> str:
        return "startday - Start a work day session"


class EndDayCommand(BaseCommand):
    """Command to end a work day session."""
    
    def execute(self, data, args: List[str], context: CommandContext) -> CommandResult:
        work_session = data.get("work_session", {})
        
        if not work_session.get("active"):
            return CommandResult(
                success=False,
                message=t('work_session_not_active')
            )
        
        # Log any remaining time if there's an active timer
        last_entry_logged = False
        if work_session.get("current_timer_start_ts") and data.get("focused_subtask"):
            elapsed_seconds = int(datetime.now().timestamp() - work_session["current_timer_start_ts"])
            if elapsed_seconds > 0:
                from inc.time_tracker import add_time_entry
                focused_ticket = data.get("focused_ticket")
                focused_subtask = data.get("focused_subtask")
                if focused_ticket and focused_subtask:
                    normalized_subtask = f"[{focused_ticket}] {focused_subtask}"
                    add_time_entry(data, entry_type="task", subtask=normalized_subtask, seconds=elapsed_seconds)
                    last_entry_logged = True
        
        # Properly end work session and clear all timers
        work_session["active"] = False
        work_session["end_time"] = datetime.now().isoformat()
        work_session.pop("current_timer_start_ts", None)
        work_session.pop("last_activity_ts", None)  # Clear to prevent scheduler from auto-ending again
        work_session.pop("paused", None)
        
        # Clear any pending check-ins to prevent scheduler from creating new ones
        data["pending_checkin"] = None
        
        # Clear focus since work day is ending
        data["focused_ticket"] = None
        data["focused_subtask"] = None
        
        # Prompt for comment on last entry if one was just logged
        if last_entry_logged:
            self._prompt_for_last_entry_comment(context.stdscr, data)
        
        # Show daily summary after ending the day
        from inc.views.daily_summary_view import show_daily_summary
        show_daily_summary(context.stdscr, data, auto_end=False)
        
        return CommandResult(
            success=True,
            data_modified=True,
            message=t('work_session_ended'),
            request_redraw=False  # Don't redraw immediately, summary handled it
        )
    
    def _prompt_for_last_entry_comment(self, stdscr, data):
        """Prompt user to add a comment to the last time entry."""
        import curses
        
        height, width = stdscr.getmaxyx()
        stdscr.clear()
        
        stdscr.addstr(1, 2, "💭 Add a comment to your last work session?")
        stdscr.addstr(3, 2, "Comment (or press ENTER to skip):")
        stdscr.addstr(4, 2, "> ")
        
        # Enable cursor and echo for input
        curses.curs_set(1)
        curses.echo()
        stdscr.refresh()
        
        try:
            # Get user input
            comment = stdscr.getstr(4, 4, width - 10).decode('utf-8').strip()
            
            if comment:
                from inc.time_tracker import add_comment_to_latest_entry
                success = add_comment_to_latest_entry(data, comment)
                if success:
                    stdscr.addstr(6, 2, f"✓ Comment added: {comment[:50]}..." if len(comment) > 50 else f"✓ Comment added: {comment}")
                else:
                    stdscr.addstr(6, 2, "❌ Failed to add comment to latest entry")
                stdscr.addstr(7, 2, "Press any key to continue...")
                stdscr.refresh()
                stdscr.getch()
        
        except (curses.error, UnicodeDecodeError):
            pass  # Skip comment if there's an error
        
        finally:
            # Restore normal curses settings
            curses.noecho()
            curses.curs_set(0)
    
    def get_usage(self) -> str:
        return "endday - End the current work day session"


class PauseCommand(BaseCommand):
    """Command to pause the current work session."""
    
    def execute(self, data, args: List[str], context: CommandContext) -> CommandResult:
        work_session = data.get("work_session", {})
        
        if not work_session.get("active"):
            return CommandResult(
                success=False,
                message=t('work_session_not_active')
            )
        
        if work_session.get("paused"):
            return CommandResult(
                success=False,
                message=t('work_session_already_paused')
            )
        
        # Log current timer if running
        if work_session.get("current_timer_start_ts") and data.get("focused_subtask"):
            elapsed_seconds = int(datetime.now().timestamp() - work_session["current_timer_start_ts"])
            if elapsed_seconds > 0:
                from inc.time_tracker import add_time_entry
                focused_ticket = data.get("focused_ticket")
                focused_subtask = data.get("focused_subtask")
                if focused_ticket and focused_subtask:
                    normalized_subtask = f"[{focused_ticket}] {focused_subtask}"
                    add_time_entry(data, entry_type="task", subtask=normalized_subtask, seconds=elapsed_seconds)
        
        work_session["paused"] = True
        work_session["pause_time"] = datetime.now().isoformat()
        work_session.pop("current_timer_start_ts", None)
        
        return CommandResult(
            success=True,
            data_modified=True,
            message=t('work_session_paused'),
            request_redraw=True
        )
    
    def get_usage(self) -> str:
        return "pause - Pause the current work session"


class ResumeCommand(BaseCommand):
    """Command to resume a paused work session."""
    
    def execute(self, data, args: List[str], context: CommandContext) -> CommandResult:
        work_session = data.get("work_session", {})
        
        if not work_session.get("active"):
            return CommandResult(
                success=False,
                message=t('work_session_not_active')
            )
        
        if not work_session.get("paused"):
            return CommandResult(
                success=False,
                message=t('work_session_not_paused')
            )
        
        work_session["paused"] = False
        work_session["resume_time"] = datetime.now().isoformat()
        work_session["current_timer_start_ts"] = datetime.now().timestamp()
        work_session["last_activity_ts"] = datetime.now().timestamp()
        
        return CommandResult(
            success=True,
            data_modified=True,
            message=t('work_session_resumed'),
            request_redraw=True
        )
    
    def get_usage(self) -> str:
        return "resume - Resume a paused work session"


class LogTimeCommand(BaseCommand):
    """Command to manually log time to a task."""
    
    def execute(self, data, args: List[str], context: CommandContext) -> CommandResult:
        if len(args) < 2:
            if data.get("focused_subtask"):
                usage = "Usage: logtime <minutes> [date] OR logtime <subtask> <minutes> [date]"
            else:
                usage = "Usage: logtime <subtask> <minutes> [date] (no subtask focused)"
            return CommandResult(success=False, message=usage)
        
        try:
            subtask_name = None
            minutes = None
            target_date = date.today().isoformat()
            
            # Try to parse first argument as minutes (focused subtask mode)
            try:
                minutes = int(args[1])
                # First arg is minutes, use focused subtask
                if data.get("focused_subtask") and data.get("focused_ticket"):
                    subtask_name = f"[{data['focused_ticket']}] {data['focused_subtask']}"
                else:
                    return CommandResult(
                        success=False,
                        message="No subtask currently focused. Use: logtime <subtask> <minutes> [date]"
                    )
                
                # Check for optional date in 3rd position
                if len(args) >= 3:
                    target_date = args[2]
                    date.fromisoformat(target_date)  # Validate date format
                    
            except ValueError:
                # First arg is not minutes, assume it's subtask name
                if len(args) >= 3:
                    subtask_name = args[1]
                    minutes = int(args[2])
                    
                    # Check for optional date in 4th position
                    if len(args) >= 4:
                        target_date = args[3]
                        date.fromisoformat(target_date)  # Validate date format
                else:
                    return CommandResult(
                        success=False,
                        message="Usage: logtime <minutes> [date] OR logtime <subtask> <minutes> [date]"
                    )
            
            # Convert minutes to seconds
            seconds = minutes * 60
            
            # Add the time entry
            from inc.time_tracker import add_time_entry, normalize_subtask_identifier
            add_time_entry(data, entry_type="task", subtask=subtask_name, seconds=seconds, entry_date_iso=target_date)
            
            # Show appropriate notification
            display_name = normalize_subtask_identifier(subtask_name)
            message = f"Logged {minutes} minutes for {display_name}"
            
            return CommandResult(
                success=True,
                data_modified=True,
                message=message,
                request_redraw=True
            )
            
        except ValueError as e:
            return CommandResult(
                success=False,
                message=f"Invalid time or date format: {str(e)}"
            )
    
    def get_usage(self) -> str:
        return "logtime <minutes> [date] OR logtime <subtask> <minutes> [date] - Log time manually"


class CommentCommand(BaseCommand):
    """Command to add a comment to the latest time entry."""
    
    def execute(self, data, args: List[str], context: CommandContext) -> CommandResult:
        if len(args) < 2:
            return CommandResult(
                success=False,
                message="Usage: c <comment text>"
            )
        
        comment_text = " ".join(args[1:])
        
        from inc.time_tracker import add_comment_to_latest_entry
        success = add_comment_to_latest_entry(data, comment_text)
        
        if success:
            return CommandResult(
                success=True,
                data_modified=True,
                message=f"Comment added to latest entry: {comment_text[:30]}...",
                request_redraw=True
            )
        else:
            return CommandResult(
                success=False,
                message="No recent time entries found to comment on"
            )
    
    def get_usage(self) -> str:
        return "c <comment_text> - Add comment to latest time entry"


class ViewTimeLogCommand(BaseCommand):
    """Command to switch to the time log view."""
    
    def execute(self, data, args: List[str], context: CommandContext) -> CommandResult:
        return CommandResult(
            success=True,
            view_change=VIEW_TIME_LOG,
            request_redraw=True
        )
    
    def get_usage(self) -> str:
        return "timelog|log - View time log"
