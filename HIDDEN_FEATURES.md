# 🔍 **Hidden & Undocumented Features Guide**

## **📅 Recurring Events System** ✅ WORKING
**Command**: `p <weekday> <time> <details>` or `k <weekday> <time> <details>`

**Examples**:
```bash
p monday 14:00 Weekly team meeting
k friday 16:00 Weekly cleanup reminder
```

**Supported weekdays**: monday, tuesday, wednesday, thursday, friday, saturday, sunday

**What it does**:
- Creates recurring events that show up every week
- Displays in main view under meetings/events 
- Sends 10-minute and 5-minute notifications
- Automatically opens meeting links at 5-minute warning

---

## **🗑️ Note Deletion in Views** ✅ WORKING
**Command**: `d` (when in notes view with a note selected)

**How to use**:
1. Press `TAB` to enter notes view for current task/subtask
2. Use arrow keys to select a note
3. Type `d` and press Enter to delete the selected note

---

## **💬 Time Entry Comments** ✅ WORKING
**Command**: `c <comment>` 

**Example**:
```bash
c Debugging integration issues with payment gateway
```

**What it does**:
- Adds a comment to your most recent time entry
- Shows up in time log view for better tracking
- Helpful for detailed time tracking and reporting

---

## **🔄 Task Status Cycling** ✅ WORKING
**Method**: Press Enter on selected subtask (without typing any command)

**What it does**:
- Cycles through: todo → in_progress → done → todo
- Quick way to update task status without commands

---

## **⏰ Advanced Time Tracking Features** ✅ WORKING

### **Start/End Work Day**:
```bash
startday  # Begin time tracking for the day
endday    # End work day and show summary
```

### **Pause/Resume Work**:
```bash
pause     # Pause current work session  
resume    # Resume work session
```

### **Manual Time Logging**:
```bash
logtime 2h30m DCMIN-906 Fixed payment integration
logtime 45m break
```

---

## **📊 Time Analysis** ✅ WORKING
**Command**: `timelog` or press Left Arrow key

**Features**:
- Daily time summaries
- Work/break/meeting time breakdown  
- Task-specific time tracking
- Weekly and monthly views
- Productivity scoring
- Daily summary at end of work day

---

## **🔔 Advanced Notifications** ✅ WORKING

### **Meeting Notifications**:
- 10-minute and 5-minute warnings
- Automatic browser opening for meeting links
- Focus window activation

### **Jira/Trello Comments**:  
- Desktop notifications for new comments
- Real-time polling every 2 minutes
- Comments marked as read when viewing task

### **PR Status Notifications**:
- Notifications when PRs need attention
- Approval status tracking
- Integration with Stash/Bitbucket

---

## **🎯 Focus System** ✅ WORKING
**Commands**: 
```bash
focus <task-name>      # Set main task focus
f <subtask-number>     # Focus on specific subtask  
```

**Features**:
- Automatic time tracking for focused tasks
- Context preservation when switching
- Smart timer management
- Hourly check-ins with focus suggestions

---

## **📱 External Integrations** ✅ WORKING

### **Calendar Integration**:
- Google Calendar CSV import
- External meeting display
- Automatic notifications

### **Web Monitoring**:
- Monitor websites for changes (e.g., security updates)
- Configurable check intervals
- Selector-based content monitoring
- Desktop notifications on changes

### **Jira/Trello Real-time Sync**:
- Live ticket status updates
- Comment synchronization  
- Automatic cache refresh
- PR status integration

---

## **🗂️ Data Management** ✅ WORKING

### **Task Pausing System**:
```bash
pause                  # Pause current task with full context
resume <task-name>     # Resume paused task
```

### **Task Completion**:
```bash  
x <task-name>          # Mark task as completed
```

### **Notification Management**:
```bash
ok                     # Dismiss current notification
```

---

## **⚡ Quick Tips**

1. **Hidden Shortcuts**:
   - `TAB`: Quick switch to notes view
   - `ESC`: Return to main view from any view  
   - Arrow keys: Navigate between subtasks
   - Enter (no command): Toggle subtask status

2. **Time Tracking**:
   - Hourly check-ins help maintain focus
   - Comments make time logs more valuable
   - Auto-end prevents overtime tracking

3. **Integration Power**:
   - Set up browser automation for meetings
   - Use calendar CSV for external scheduling
   - Configure web monitoring for critical updates

4. **Focus Management**:
   - Use focus system for deep work
   - Paused tasks preserve full context
   - Hourly reminders keep you on track

---

## **🚀 Pro User Features**

### **Multi-Project Management**:
- Seamless task switching preserves context
- Right panel shows all projects with visual status
- Paused task system for context juggling

### **Advanced Time Analytics**:
- Daily productivity scoring
- Break time optimization
- Meeting load analysis
- Focus pattern identification

### **Smart Notifications**:
- Context-aware meeting reminders  
- Intelligent work day boundaries
- Proactive PR attention alerts
- Real-time comment notifications

The system is far more sophisticated than it appears at first glance!
