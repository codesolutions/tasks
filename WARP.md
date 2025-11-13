# WARP.md

This file provides guidance to WARP (warp.dev) when working with code in this repository.

## Project Overview

This is a **Terminal Project Tracker** - a curses-based Python application for developers to manage projects, tickets (tasks), and their development lifecycle. It integrates with Jira, Trello, and Git repositories to provide real-time status updates, PR monitoring, and desktop notifications.

## Development Setup

### Dependencies
```bash
# Install Python dependencies
pip install requests selenium webdriver-manager

# Preferred: Use virtual environment
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install requests selenium webdriver-manager

# Linux desktop notifications
sudo apt-get install notify-send xdotool  # Debian/Ubuntu
```

### Configuration
1. Copy `config.json.sample` to `config.json`
2. Update the configuration with your API tokens and URLs
3. Run the application once to create language files in `lang/`

### Running the Application
```bash
# With virtual environment
source .venv/bin/activate
python3 jira_tracker.py

# Or with specific terminal title for notifications
gnome-terminal --title='TODAYTASKS' -- /bin/bash -c 'source .venv/bin/activate && python3 jira_tracker.py'

# Direct execution (if executable)
./jira_tracker.py
```

## Code Architecture

### Main Application Structure
- **`jira_tracker.py`** - Main application entry point containing the curses UI, event loop, and command processing
- **`inc/`** - Internal modules directory
  - **`config_manager.py`** - Configuration loading and translation system
  - **`helpers.py`** - Utility functions and translation helpers
  - **`jira.py`** - Jira/Trello integration, session management, and API calls

### Key Architectural Patterns

**Multi-threaded Design**: 
- Main thread handles UI rendering and user input
- Background threads for API polling (Jira, Trello, PR status)
- Thread-safe queuing system for Jira requests (`jira_request_queue`, `jira_in_flight`)
- Shared cache with locking (`jira_cache_lock`)

**View System**:
- `VIEW_MAIN` - Primary project/ticket management interface
- `VIEW_DEDICATED_NOTES` - Focused notes editing for projects/tickets
- `VIEW_DAILY_NOTES` - Daily note-taking with date navigation

**Data Persistence**:
- JSON-based data storage (`jira_data.json`)
- Session cookies stored as pickle files for web integrations
- Cache system for API responses to minimize network calls

**Internationalization**:
- Language files in `lang/` directory (`en.json`, `fi.json`)
- Translation function `t(key, **kwargs)` with template formatting
- Configurable language via `config.json`

### State Management
The application maintains state in a single `data` dictionary with these key structures:
- `current_ticket` - Active project name
- `sub_tasks` - Nested dict of projects → tickets → ticket details
- `focused_ticket`/`focused_subtask` - Current focus for keyboard shortcuts
- `notes` - Project-level notes
- `meetings`/`interruptions` - Calendar events
- `paused_tasks` - Suspended projects with their state

### Integration Points
- **Jira**: Fetches ticket status, comments, and metadata via session cookies
- **Trello**: Card details and comments for tickets with Trello links  
- **Stash/Bitbucket**: Enhanced PR monitoring with full comment threads, reviewer status, and detailed notifications
- **Desktop**: Native notifications via `notify-send`
- **Browser**: Automatic link opening for meetings and external resources

## Testing and Validation

### Manual Testing
Since this is a terminal UI application, testing is primarily interactive:
```bash
# Test basic functionality
python3 jira_tracker.py

# Test with clean state
rm jira_data.json jira_cache.pkl
python3 jira_tracker.py
```

### Key Test Scenarios
- Project creation and switching
- Ticket management (add, mark done, hide)
- Note-taking in different views
- PR link addition and status updates
- Meeting/event scheduling
- Configuration validation

## Common Development Tasks

### Adding New Commands
1. Update command parsing in `handle_input()` function
2. Add command logic and data modification
3. Update help text in language files
4. Test command in main view and validate state changes

### Modifying UI Elements
1. Main UI rendering happens in `display_ui()` 
2. Use `_draw_wrapped_text()` for text that may overflow
3. Respect view modes and content height calculations
4. Update color pairs if adding new visual states

### API Integration Changes  
1. Modify `inc/jira.py` for new endpoints or data formats
2. Update caching logic if response structure changes
3. Handle authentication errors and session refresh
4. Consider thread safety for shared cache access

### Adding Translations
1. Add new keys to both `lang/en.json` and `lang/fi.json`
2. Use `t('key_name', param=value)` syntax in code
3. Test with both languages via config setting

## Data Files and Persistence

- `jira_data.json` - Main application state (projects, tickets, notes)
- `jira_cache.pkl` - Cached API responses
- `jira_session.pkl` / `trello_session.pkl` - Authentication cookies
- `config.json` - Application configuration
- `debug.log` - Application logs

## Enhanced Pull Request Handling

**Version 2.0** introduces a completely refactored PR handling system that separates PR data from regular notes and provides much better readability:

### New PR Features
- **Dedicated PR Comments Section**: PR comments are no longer mixed with your personal notes
- **Rich Status Display**: Clear reviewer status with emoji indicators (✅ ❌ ❓)
- **Enhanced API Integration**: Fetches complete PR metadata, reviewer details, and comment threads
- **Code Block Formatting**: Proper syntax highlighting for code snippets in comments
- **Time Stamps**: Relative time display ("2h ago", "3 days ago") for all PR activities
- **Better Visual Layout**: Bordered sections with clear separation between different data types

### PR Status Indicators
- 🟢 **Green Background**: PR approved and ready to merge
- 🔴 **Red Background**: PR needs attention (unhandled comments or needs work)
- 🟡 **Yellow Background**: PR waiting for reviews
- 🔘 **Gray Background**: PR merged or declined

### Data Migration
Existing PR data is automatically migrated to the new format:
- Old `*PR* author: comment` entries are extracted from notes
- Converted to structured comment objects with proper metadata  
- Your personal notes remain unchanged
- Migration creates automatic backup before changes

## External Dependencies and Integration Requirements

The application expects these external tools to be available:
- Chrome/Chromium browser for session capture
- **ChromeDriver** - Automatically managed by webdriver-manager (downloads correct version automatically)
- `notify-send` for desktop notifications
- `xdotool` for window management

### ChromeDriver Management
The application now uses **webdriver-manager** to automatically handle ChromeDriver versions:
- Automatically downloads the correct ChromeDriver version for your Chrome installation
- No manual ChromeDriver setup required
- Falls back to `CHROME_DRIVER_PATH` configuration if webdriver-manager fails
- Eliminates version compatibility issues
