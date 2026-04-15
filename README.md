# Terminal Project Tracker

A modern, terminal-based project tracking application with a **modular command system** for managing projects, tickets, time tracking, and development workflows. Built for developers who live in the terminal and want efficient, keyboard-driven project management.

> 📖 **Detailed Documentation**: See [COMMAND_SYSTEM_INTEGRATION.md](COMMAND_SYSTEM_INTEGRATION.md) for the complete technical overview of the new command system architecture.

-----

## ✨ Key Features

### 🎯 **Modern Command Architecture**
  * **24 Commands** across 6 functional categories
  * **Modular & Extensible** - Easy to add new commands
  * **Enhanced Validation** and error handling
  * **Consistent Interface** across all operations
  * **Backward Compatible** with existing workflows

### 📋 **Core Functionality**
  * **Project & Task Management**: Create, switch, and complete projects with nested subtasks
  * **Time Tracking**: Built-in work sessions with start/pause/resume/log capabilities
  * **Pull Request Integration**: Monitor PR status with real-time updates
  * **Jira & Bitbucket Integration**: Automatic ticket synchronization, PR monitoring, and status updates
  * **Smart Focus System**: Focus on specific tasks for better productivity tracking
  * **Advanced Notifications**: Desktop alerts for meetings, PR updates, and events
  * **Multi-View Interface**: Main view, time log, daily notes, and dedicated note editing
  * **External Integrations**: Calendar polling, web monitoring, and browser automation

-----

## ⚙️ Setup & Configuration

1.  **Dependencies**: Ensure you have Python 3 and the required libraries installed.

    ```bash
    pip install requests selenium beautifulsoup4 lxml webdriver-manager
    # OR if that is not working, add virtual env (preferred):
    python3 -m venv .venv
    source .venv/bin/activate
    python3 -m pip install requests selenium beautifulsoup4 lxml webdriver-manager
    ```

    For desktop notifications on Linux, the following tools are required:

    ```bash
    sudo apt-get install notify-send xdotool # Debian/Ubuntu
    ```

2.  **Configuration File**: The first time you run the script, it will create a `config.json` file in the same directory. You **must** edit this file before the application will run properly.

    ```json
    {
        "BB_USERNAME": "you@example.com",
        "BB_APP_PASSWORD": "PASTE_YOUR_BITBUCKET_APP_PASSWORD_OR_API_TOKEN_HERE",
        "BB_WORKSPACE": "your-workspace",
        "LANGUAGE": "fi",
        "NOTIFICATION_WINDOW_TITLE": "TODAYTASKS",
        "BROWSER_COMMAND": [
            "/usr/bin/google-chrome",
            "--profile-directory=Profile 1",
            "--new-window"
        ],
        "JIRA_URL": "https://your_org_here.atlassian.net",
        "JIRA_SESSION_FILE": "jira_session.pkl",
        "CHROME_DRIVER_PATH": "chromedriver-linux64/chromedriver",
        "CALENDAR_CSV": "https://docs.google.com/spreadsheets/d/e/YOUR_EXPORTED_CALENDAR_CSV_HERE/pub?gid=0&single=true&output=csv",
        "WEB_MONITORING": {
            "ENABLED": true,
            "CHECK_INTERVAL_MINUTES": 30,
            "PAGES": [
                {
                    "name": "Magento Security",
                    "url": "https://helpx.adobe.com/security/products/magento.html",
                    "selector": "#root_content_flex_items_position_position-par_table_copy > table > tbody > tr:nth-child(2) > td",
                    "description": "Security Updates for Magento"
                }
            ]
        }
    }
    ```

      * `BB_USERNAME`: Your Bitbucket username, or the email address of your Atlassian account if you are authenticating with an Atlassian API token (tokens starting with `ATATT3x...`).
      * `BB_APP_PASSWORD`: A Bitbucket app password (create at <https://bitbucket.org/account/settings/app-passwords/>) or an Atlassian API token.
      * `BB_WORKSPACE`: The slug of your Bitbucket workspace (the part in the URL between `bitbucket.org/` and the repository name).
      * `LANGUAGE`: Set the display language. Defaults to `"fi"`. Change to `"en"` for English.
      * `NOTIFICATION_WINDOW_TITLE`: The title of the terminal window to focus when a notification is sent.
      * `BROWSER_COMMAND`: A list containing the command and arguments to launch a web browser for meeting links.
      * `JIRA_URL`: The URL of your Jira instance.
      * `JIRA_SESSION_FILE`: The file to store your Jira session.
      * `CHROME_DRIVER_PATH`: (Optional) The path to your chromedriver executable. If not provided or invalid, webdriver-manager will automatically download the correct ChromeDriver version.
      * `CALENDAR_CSV`: The URL to your external calendar in CSV format.

3.  **Language Files**: The application looks for translations in a `lang` directory. Ensure `lang/en.json` and `lang/fi.json` exist.

-----

## 🚀 Quick Start

1.  **Run the application**:
    To allow the app to focus itself for notifications, it's recommended to launch it with a specific terminal title.

    ```bash
    # Example for gnome-terminal
    gnome-terminal --title='TODAYTASKS' -- /bin/bash -c 'source .venv/bin/activate && python3 jira_tracker.py'
    # Or if packages are global, then you can just try:
    ./jira_tracker.py
    ```

2.  **Create a new project**:
    Type `n Your-Project-Name` and press `Enter`.

3.  **Add a ticket (task)**:
    With a project active, type `a https://your.jira.com/browse/TICKET-123` and press `Enter`.

4.  **Add a Pull Request link**:

      * Use the `Up`/`Down` arrow keys to highlight the ticket you want to add a PR to.
      * Type `pr https://your.git.repo/pull-requests/42` and press `Enter`. The app will now monitor this PR.

5.  **Add a note**:

      * To add a note to the active project, type `note This is a project-level note.` and press `Enter`.
      * To add a note to a specific ticket, first highlight it with the arrow keys, then type your `note` command.

6.  **View, Edit & Delete Notes**:

      * Press `Shift+TAB` to enter the dedicated notes view for the selected item (project or ticket).
      * In the notes view, use `Up`/`Down` to select a note.
      * Press `d` to **delete** the selected note.
      * Type text and press `Enter` to add a new note.
      * Press `Shift+TAB` or `ESC` to return to the main view.

7.  **Mark items as done**:

      * Highlight a ticket and press `Enter` to toggle its done status `[ ]` / `[X]`.
      * To mark the entire active project as complete (and stop the timer), type `x` and press `Enter`.

-----

## ⌨️ Command Reference

> 💡 **Tip**: Type `h` in the application to see contextual help and available commands.

### 📋 **Project Management**
| Command | Description |
| :--- | :--- |
| `n <project>` | Create a new project and make it active |
| `x` | Mark the current project as complete |
| `switch <name/number>` | Switch to a project by name or number |
| `t` | Toggle visibility of hidden/completed tasks |
| `ok [number]` | Dismiss notifications |

### 🎯 **Task & Subtask Management**
| Command | Description |
| :--- | :--- |
| `a <task>` | Add a new task/subtask to current project |
| `d` | Hide the selected subtask |
| `f` | Focus/unfocus on the selected subtask |
| `focus <name>` | Focus on a specific task or subtask |
| `pr <url>` | Add Pull Request URL to selected subtask |

### ⏱️ **Time Tracking**
| Command | Description |
| :--- | :--- |
| `startday` | Start a work day session |
| `endday` | End the current work day session |
| `pause` | Pause the current work session |
| `resume` | Resume a paused work session |
| `logtime <minutes> [date]` | Log time to focused subtask |
| `logtime <subtask> <minutes> [date]` | Log time to specific subtask |
| `c <comment>` | Add comment to latest time entry |
| `timelog` | View time log |

### 📝 **Notes & Events**
| Command | Description |
| :--- | :--- |
| `note <text>` | Add note to selected task or active project |
| `delete_note` | Delete selected note (in notes view) |
| `p [day] HH:MM <link>` | Add meeting (one-time or recurring) |
| `k [day] HH:MM <msg>` | Add interruption/event |

### 🔧 **System & Navigation**
| Command | Description |
| :--- | :--- |
| `h` | Toggle command help footer |
| `q` | Quit the application |
| `login` | Restart for Jira login |
| `Shift+TAB` / `ESC` | Enter/Exit dedicated views |
| `←` / `→` | Navigate daily notes |
| `↑` / `↓` | Select items in lists |
| `Enter` | Toggle task status or execute action |

### 🏗️ **Architecture Notes**
- **24 total commands** across 6 categories
- **Modular system** - see `inc/commands/` for implementations  
- **Extensible** - easy to add new commands
- **Contextual help** - commands adapt to current view
- **Enhanced validation** - better error messages and input checking

*Note: `[day]` can be abbreviated in English (`mo`, `tu`) or Finnish (`ma`, `ti`)*
