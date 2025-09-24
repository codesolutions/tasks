# Pull Request Data Schema

This document defines the data structure for pull request information in the Terminal Project Tracker.

## Overview

Pull request data is stored under each subtask in the `pr_details` object, replacing the previous scattered approach of mixing PR information with notes.

## Schema Structure

```json
{
  "sub_tasks": {
    "PROJECT_NAME": {
      "SUBTASK_URL": {
        "pr_details": {
          "meta": {
            "id": 1573,
            "title": "Fix authentication bug in user validation",
            "description": "This PR addresses the authentication issue...",
            "author": {
              "id": "john.doe",
              "displayName": "John Doe",
              "emailAddress": "john.doe@company.com"
            },
            "created": "2024-01-15T10:30:00Z",
            "updated": "2024-01-17T14:22:00Z", 
            "url": "https://stash.company.com/projects/PROJ/repos/repo/pull-requests/1573",
            "state": "OPEN|MERGED|DECLINED",
            "merge_status": "CAN_MERGE|CANNOT_MERGE|UNKNOWN"
          },
          "reviewers": [
            {
              "id": "alice.smith",
              "displayName": "Alice Smith",
              "status": "APPROVED|NEEDS_WORK|UNAPPROVED", 
              "approved_date": "2024-01-16T09:15:00Z"
            }
          ],
          "comments": [
            {
              "id": "comment_123",
              "parent_id": null,
              "author": {
                "id": "alice.smith", 
                "displayName": "Alice Smith"
              },
              "text": "LGTM! Just one suggestion:\n```python\ndef validate():\n    return True\n```",
              "created": "2024-01-16T09:00:00Z",
              "updated": "2024-01-16T09:05:00Z",
              "imported": false
            }
          ],
          "diffs": [
            {
              "file_path": "src/auth.py",
              "additions": 5,
              "deletions": 2,
              "snippet": "@@ -15,7 +15,10 @@\n def validate_user():\n-    return user.is_valid\n+    return user.is_valid and check_auth()"
            }
          ],
          "last_synced": "2024-01-17T15:00:00Z",
          "version": 2
        },
        "pr_url": "https://stash.company.com/projects/PROJ/repos/repo/pull-requests/1573",
        "pr_status": "approved|attention_needed|merged|waiting"
      }
    }
  }
}
```

## Migration from V1

The old format stored PR information as:
- `pr_url`: Direct URL string
- `pr_status`: Simple status string  
- `notes`: Array containing `*PR* author: comment` entries
- `pr_details`: Minimal object with `status_text` and `approvers_formatted`

### Migration Process

1. **Extract PR comments from notes**: Find entries starting with `*PR*` and parse them
2. **Create new schema**: Build complete `pr_details` object
3. **Mark imported comments**: Set `imported: true` for migrated comments
4. **Preserve pr_url and pr_status**: Keep for backward compatibility
5. **Set version flag**: Mark as `version: 2`

## API Endpoints Used

- **PR Meta**: `GET /rest/api/1.0/projects/{project}/repos/{repo}/pull-requests/{id}`
- **Activities**: `GET /rest/api/1.0/projects/{project}/repos/{repo}/pull-requests/{id}/activities`  
- **Comments**: `GET /rest/api/1.0/projects/{project}/repos/{repo}/pull-requests/{id}/comments`
- **Changes**: `GET /rest/api/1.0/projects/{project}/repos/{repo}/pull-requests/{id}/changes?limit=10`

## Derived Fields

For UI display, the following fields are calculated from the schema:

- **Overall Status**: `waiting|approved(X/Y)|merged|declined`
- **Attention Needed**: Based on unhandled comments from other users
- **Approval Count**: Count of reviewers with `APPROVED` status
- **Comment Count**: Total number of comments including replies

## Color Coding

- **Green (approved)**: All required reviewers have approved
- **Red (attention_needed)**: Unhandled comments or needs work
- **Yellow (waiting)**: Pending reviewer responses
- **Gray (merged/declined)**: Final state reached

## Future Extensions

- **Inline diff viewing**: Show code changes in PR panel
- **Comment threading**: Proper reply chain visualization  
- **Quick reply**: Post comments directly from terminal
- **Build status**: CI/CD pipeline integration