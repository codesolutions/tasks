"""
Command registry initialization.

This module registers all commands with the global command registry
and provides a function to initialize the command system.
"""

from inc.commands.base_command import command_registry
from inc.commands.task_commands import (
    NewTaskCommand, CompleteTaskCommand, SwitchTaskCommand,
    ToggleHiddenTasksCommand, DismissNotificationCommand
)
from inc.commands.subtask_commands import (
    AddSubtaskCommand, HideSubtaskCommand, FocusSubtaskCommand,
    FocusCommand, AddPRCommand
)
from inc.commands.time_commands import (
    StartDayCommand, EndDayCommand, PauseCommand, ResumeCommand,
    LogTimeCommand, CommentCommand, ViewTimeLogCommand
)
from inc.commands.utility_commands import (
    HelpCommand, QuitCommand, LoginCommand, AddNoteCommand,
    AddMeetingCommand, DeleteNoteCommand, CompletedTasksCommand
)


def initialize_commands():
    """Initialize and register all commands with the command registry."""
    
    # Task management commands
    command_registry.register("n", NewTaskCommand(), aliases=["new"])
    command_registry.register("x", CompleteTaskCommand(), aliases=["complete", "done"])
    command_registry.register("switch", SwitchTaskCommand())
    command_registry.register("t", ToggleHiddenTasksCommand(), aliases=["toggle"])
    command_registry.register("ok", DismissNotificationCommand())
    
    # Subtask management commands
    command_registry.register("a", AddSubtaskCommand(), aliases=["add"])
    command_registry.register("d", HideSubtaskCommand(), aliases=["hide"])
    command_registry.register("f", FocusSubtaskCommand())
    command_registry.register("focus", FocusCommand())
    command_registry.register("pr", AddPRCommand())
    
    # Time tracking commands
    command_registry.register("startday", StartDayCommand())
    command_registry.register("endday", EndDayCommand())
    command_registry.register("pause", PauseCommand())
    command_registry.register("resume", ResumeCommand())
    command_registry.register("logtime", LogTimeCommand())
    command_registry.register("c", CommentCommand(), aliases=["comment"])
    command_registry.register("timelog", ViewTimeLogCommand(), aliases=["log"])
    
    # Utility commands
    command_registry.register("h", HelpCommand(), aliases=["help"])
    command_registry.register("q", QuitCommand(), aliases=["quit", "exit"])
    command_registry.register("login", LoginCommand())
    command_registry.register("note", AddNoteCommand())
    command_registry.register("p", AddMeetingCommand('meeting'))
    command_registry.register("k", AddMeetingCommand('interruption'))
    command_registry.register("completed", CompletedTasksCommand())
    
    # Special commands for different view contexts
    # These will be handled with additional logic in the main loop
    command_registry.register("delete_note", DeleteNoteCommand())
    
    # The SwitchTaskCommand will handle numeric and text-based task switching
    # This is registered separately to handle fallback behavior
    
    return command_registry


def get_command_help() -> str:
    """
    Generate a help text with all available commands.
    
    Returns:
        Formatted help text listing all commands
    """
    help_lines = ["Available Commands:", ""]
    
    # Get all commands and their usage
    all_commands = command_registry.get_all_commands()
    
    # Group commands by category
    categories = {
        "Task Management": ["n", "x", "t", "ok", "completed"],
        "Subtask Management": ["a", "d", "f", "focus", "pr"], 
        "Time Tracking": ["startday", "endday", "pause", "resume", "logtime", "c", "timelog"],
        "Notes & Events": ["note", "p", "k"],
        "Utility": ["h", "q", "login"]
    }
    
    for category, command_names in categories.items():
        help_lines.append(f"{category}:")
        for cmd_name in command_names:
            if cmd_name in all_commands:
                usage = all_commands[cmd_name].get_usage()
                help_lines.append(f"  {usage}")
        help_lines.append("")
    
    # Add special cases
    help_lines.append("Task Switching:")
    help_lines.append("  <number> - Switch to task by number")
    help_lines.append("  <name> - Switch to task by name pattern")
    help_lines.append("")
    
    return "\n".join(help_lines)
