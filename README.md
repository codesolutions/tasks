# Terminal Project Tracker

A terminal-based tool for tracking personal projects, their associated tickets (tasks), and their development lifecycle through pull request monitoring and notifications. It's designed for developers who spend a lot of time in the terminal and want a quick, keyboard-driven way to manage their current work.

---

## ✨ Features

* **Project & Ticket Management**: Organize your work into projects, each containing multiple tickets (tasks).
* **Real-time Timers**: Automatically tracks the time spent on the active project.
* **Pull Request Integration**: Associates pull request URLs with tickets and monitors their status in the background.
* **PR Status Highlighting**:
    * 🔴 **Red**: PR needs your attention (unhandled comments).
    * 🟢 **Green**: PR is approved and ready to merge.
    * No color: PR is merged or has no issues.
* **Desktop Notifications**: Get native desktop notifications for:
    * Upcoming meetings and events (built-in, no cron job needed).
    * New unhandled comments on your pull requests.
    * PR approvals and merges.
* **Note Taking**: Add notes to projects, tickets, or maintain a daily log.
* **Keyboard-Driven UI**: Designed for efficient use without leaving the keyboard.
* **Configurable & Translatable**: Settings are managed in a simple `config.json`, and all UI text can be translated via language files.

---

## ⚙️ Setup & Configuration

1.  **Dependencies**: Ensure you have Python 3 and the `requests` library installed.
    ```bash
    pip install requests
    ```
    For desktop notifications on Linux, the following tools are required:
    ```bash
    sudo apt-get install notify-send xdotool # Debian/Ubuntu
    ```

2.  **Configuration File**: The first time you run the script, it will create a `config.json` file in the same directory. You **must** edit this file before the application will run properly.

    ```json
    {
        "API_TOKEN": "PASTE_YOUR_BEARER_TOKEN_HERE",
        "USER_ID": 1234,
        "LANGUAGE": "fi",
        "NOTIFICATION_WINDOW_TITLE": "TODAYTASKS",
        "BROWSER_COMMAND": [
            "/usr/bin/google-chrome",
            "--profile-directory=Profile 1",
            "--new-window"
        ]
    }
    ```
    * `API_TOKEN`: Your Bearer token for the Stash/Bitbucket API.
    * `USER_ID`: Your numeric user ID from the API. This is used to distinguish your comments from others'.
    * `LANGUAGE`: Set the display language. Defaults to `"fi"`. Change to `"en"` for English.
    * `NOTIFICATION_WINDOW_TITLE`: The title of the terminal window to focus when a notification is sent.
    * `BROWSER_COMMAND`: A list containing the command and arguments to launch a web browser for meeting links.
    * NOTE: Change STASH_URL_CHANGE_ME from APP itself!!! @todo

3.  **Language Files**: The application looks for translations in a `lang` directory. Ensure `lang/en.json` and `lang/fi.json` exist.

---

## 🚀 Quick Start

1.  **Run the application**:
    To allow the app to focus itself for notifications, it's recommended to launch it with a specific terminal title.
    ```bash
    # Example for gnome-terminal
    gnome-terminal --title='TODAYTASKS' -- /usr/bin/python3 /path/to/your/jira_tracker.py
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

---

## ⌨️ Commands

| Command | Description | Context |
| :--- | :--- | :--- |
| `<name>`/`<idx>` + `Enter`| Switch the active project. | Main View |
| `n <project>` | Create a new project and make it active. | Main View |
| `a <ticket>` | Add a new ticket to the current project. | Main View |
| `pr <url>` | Add a Pull Request URL to the selected ticket. | Main View |
| `note <text>` | Add a note to the selected ticket or active project. | Main View |
| `Enter` (on ticket) | Toggle the 'done' status of the selected ticket. | Main View |
| `d` (on ticket) | Hide the selected ticket from view. | Main View |
| `x` | Mark the current project as complete. | Main View |
| `p [day] HH:MM <link>` | Add a one-time or recurring meeting. | Main View |
| `k [day] HH:MM <msg>` | Add a one-time or recurring event. | Main View |
| `h` | Toggle the visibility of the command help footer. | All Views |
| `q` | Quit the application. | All Views |
| `Shift+TAB` / `ESC` | Enter/Exit the dedicated notes view. | All Views |
| `<-` / `->` | Browse daily notes. | Main View |
| `Up`/`Down` | Select a note. | Notes Views |
| `d` | Delete the selected note. | Notes Views |
| `<text>` + `Enter` | Add a new note. | Notes Views |

*Note: `[day]` can be a two-letter abbreviation in English (`mo`, `tu`) or Finnish (`ma`, `ti`).*