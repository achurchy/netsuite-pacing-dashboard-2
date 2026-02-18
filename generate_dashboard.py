#!/usr/bin/env python3
"""
Generate a static HTML dashboard from Jira data.
This script is run by GitHub Actions every 15 minutes.
"""

import os
import json
import requests
from datetime import datetime, timedelta
from collections import defaultdict

# Configuration
START_DATE = datetime(2026, 1, 7)
END_DATE = datetime(2026, 3, 31)
PORTFOLIO_KEY = "CPF-74"

# Jira credentials from environment
JIRA_EMAIL = os.environ.get('JIRA_EMAIL', '')
JIRA_API_TOKEN = os.environ.get('JIRA_API_TOKEN', '')
JIRA_BASE_URL = os.environ.get('JIRA_BASE_URL', 'https://collectors.atlassian.net')

# Status mappings
COMPLETION_STATUSES = [
    'Done', 'DONE', 'Ready for Prod Release', 'Pending Deployment', 'Ready for Deploy/Push',
    'PM Review', 'Ready for Review', 'Ready for UAT', 'Ready for Prod'
]

STATUS_GROUPS = {
    'Not Started': ['To Do', 'Scoping'],
    'Blocked': ['Blocked'],
    'Dev In Prog': ['In Progress', 'Code Review', 'Fix Required'],
    'QA': ['In QA', 'QA In Progress', 'Ready for QA'],
    'Done': COMPLETION_STATUSES,
}

def get_status_group(status_name):
    """Get the status group for a given status"""
    if not status_name:
        return 'Other'
    status_lower = status_name.lower()
    for group, statuses in STATUS_GROUPS.items():
        if any(s.lower() == status_lower for s in statuses):
            return group
    return 'Other'

def is_completed(status):
    """Check if a status is a completion status"""
    return status in COMPLETION_STATUSES

def get_effective_date(issue):
    """Get the effective completion date for an issue"""
    fields = issue.get('fields', {})
    status = fields.get('status', {}).get('name', '')
    
    if status == 'Done':
        date_str = fields.get('resolutiondate')
    else:
        date_str = fields.get('updated')
    
    if date_str:
        try:
            # Parse ISO format with timezone
            if 'T' in date_str:
                date_str = date_str.split('T')[0]
            return datetime.strptime(date_str, '%Y-%m-%d')
        except:
            return None
    return None

def generate_intervals():
    """Generate monthly intervals from START_DATE to END_DATE"""
    from calendar import monthrange
    intervals = []
    current = START_DATE

    while current <= END_DATE:
        # Get the last day of the current month
        _, last_day = monthrange(current.year, current.month)
        month_end = datetime(current.year, current.month, last_day)

        # Clip to END_DATE if needed
        if month_end > END_DATE:
            month_end = END_DATE

        # Use month name as label
        month_name = current.strftime('%B %Y')  # e.g., "January 2026"

        intervals.append({
            'start': current.strftime('%Y-%m-%d'),
            'end': month_end.strftime('%Y-%m-%d'),
            'label': month_name
        })

        # Move to first day of next month
        if current.month == 12:
            current = datetime(current.year + 1, 1, 1)
        else:
            current = datetime(current.year, current.month + 1, 1)

    return intervals

def fetch_issues_by_jql(jql):
    """Fetch issues matching a JQL query"""
    all_issues = []
    next_page_token = None

    while True:
        url = f"{JIRA_BASE_URL}/rest/api/3/search/jql"
        headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}
        auth = (JIRA_EMAIL, JIRA_API_TOKEN)

        payload = {
            'jql': jql,
            'maxResults': 100,
            'fields': ['key', 'summary', 'status', 'project', 'resolutiondate', 'updated',
                      'assignee', 'issuetype', 'labels', 'parent']
        }
        if next_page_token:
            payload['nextPageToken'] = next_page_token

        try:
            response = requests.post(url, json=payload, headers=headers, auth=auth, timeout=30)
            if response.status_code != 200:
                return all_issues

            data = response.json()
            all_issues.extend(data.get('issues', []))

            next_page_token = data.get('nextPageToken')
            if not next_page_token:
                break
        except:
            return all_issues

    return all_issues

def fetch_initiatives():
    """Fetch all initiatives under the portfolio"""
    jql = f'parent = {PORTFOLIO_KEY} AND issuetype = Initiative'
    initiatives = fetch_issues_by_jql(jql)
    print(f"Fetched {len(initiatives)} initiatives")
    return initiatives

def fetch_epics_for_initiatives(initiative_keys):
    """Fetch all epics under the given initiatives"""
    if not initiative_keys:
        return []

    all_epics = []
    batch_size = 20
    for i in range(0, len(initiative_keys), batch_size):
        batch = initiative_keys[i:i+batch_size]
        jql = f'parent in ({",".join(batch)}) AND issuetype = Epic'
        epics = fetch_issues_by_jql(jql)
        all_epics.extend(epics)

    print(f"Fetched {len(all_epics)} epics")
    return all_epics

def fetch_jira_issues():
    """Fetch all issues from Jira using the specified JQL"""
    if not JIRA_EMAIL or not JIRA_API_TOKEN:
        print("Error: Jira credentials not set")
        return [], [], []

    # Fetch initiatives first
    print("Fetching initiatives...")
    initiatives = fetch_initiatives()

    # Fetch epics
    print("Fetching epics...")
    initiative_keys = [i['key'] for i in initiatives]
    epics = fetch_epics_for_initiatives(initiative_keys)

    # JQL query for pacing dashboard (child issues)
    jql = f'''parent in portfolioChildIssuesOf("{PORTFOLIO_KEY}")
AND issuetype NOT IN (
  Initiative, Epic, "Sub Test Execution", "Test Execution",
  Test, "Test Plan", "Test Case", "Test Automation", Sub-task
)
AND status NOT IN (Canceled, Cancelled, "Will Not Do", "Resolved as Duplicate")
AND project NOT IN (
  "QA Shared Services", "QA Enterprise Systems",
  "QA Grading & Operations", "QA PCGS", "QA PSA Platform")
AND parent not in (NS-1095)
AND key NOT IN (ESB-30,ESB-68)
AND NOT (
  (status = DONE
   AND resolutiondate <= "2026-01-06")
  OR
  (status IN ("Ready for Prod Release",
              "Pending Deployment",
              "Ready for Deploy/Push")
   AND updated <= "2026-01-06")
)
ORDER BY resolutiondate DESC, updatedDate ASC'''

    print("Fetching issues from Jira...")
    all_issues = []
    next_page_token = None

    while True:
        url = f"{JIRA_BASE_URL}/rest/api/3/search/jql"
        headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}
        auth = (JIRA_EMAIL, JIRA_API_TOKEN)

        payload = {
            'jql': jql,
            'maxResults': 100,
            'fields': ['key', 'summary', 'status', 'project', 'resolutiondate', 'updated',
                      'assignee', 'issuetype', 'labels', 'parent']
        }
        if next_page_token:
            payload['nextPageToken'] = next_page_token

        try:
            response = requests.post(url, json=payload, headers=headers, auth=auth, timeout=30)
            if response.status_code != 200:
                print(f"Jira API error: {response.status_code} - {response.text}")
                return [], [], []

            data = response.json()
            all_issues.extend(data.get('issues', []))

            next_page_token = data.get('nextPageToken')
            if not next_page_token:
                break
        except Exception as e:
            print(f"Error fetching from Jira: {e}")
            return [], [], []

    print(f"Fetched {len(all_issues)} child issues from Jira")
    return all_issues, initiatives, epics

def process_initiative(issue):
    """Process an initiative into a simplified format"""
    fields = issue.get('fields', {})
    return {
        'key': issue.get('key'),
        'summary': fields.get('summary', ''),
        'status': fields.get('status', {}).get('name', ''),
    }

def process_epic(issue):
    """Process an epic into a simplified format"""
    fields = issue.get('fields', {})
    parent_key = None
    if fields.get('parent'):
        parent_key = fields['parent'].get('key')
    return {
        'key': issue.get('key'),
        'summary': fields.get('summary', ''),
        'status': fields.get('status', {}).get('name', ''),
        'parent': parent_key,
    }

def process_issues(issues, intervals, initiatives=None, epics=None):
    """Process issues into a structured format for the dashboard"""
    processed = []
    projects = {}
    issue_types = set()
    status_groups = set()

    # Process initiatives
    processed_initiatives = []
    if initiatives:
        for init in initiatives:
            processed_initiatives.append(process_initiative(init))

    # Process epics
    processed_epics = []
    if epics:
        for epic in epics:
            processed_epics.append(process_epic(epic))

    for issue in issues:
        fields = issue.get('fields', {})
        project_key = fields.get('project', {}).get('key', 'Unknown')
        project_name = fields.get('project', {}).get('name', project_key)
        status_name = fields.get('status', {}).get('name', '')
        issue_type = fields.get('issuetype', {}).get('name', 'Unknown')

        projects[project_key] = project_name
        issue_types.add(issue_type)

        status_group = get_status_group(status_name)
        status_groups.add(status_group)

        effective_date = get_effective_date(issue)
        effective_date_str = effective_date.strftime('%Y-%m-%d') if effective_date else None

        # Determine which week interval this falls into (if completed)
        week_label = None
        if is_completed(status_name) and effective_date:
            for interval in intervals:
                start = datetime.strptime(interval['start'], '%Y-%m-%d')
                end = datetime.strptime(interval['end'], '%Y-%m-%d')
                if start <= effective_date <= end:
                    week_label = interval['label']
                    break
            # If completed before start date, put in first week
            if not week_label and effective_date < START_DATE:
                week_label = intervals[0]['label'] if intervals else None

        # Get parent key for drilldown
        parent_key = None
        if fields.get('parent'):
            parent_key = fields['parent'].get('key')

        processed.append({
            'key': issue.get('key'),
            'summary': fields.get('summary', ''),
            'status': status_name,
            'statusGroup': status_group,
            'project': project_key,
            'projectName': project_name,
            'issueType': issue_type,
            'assignee': fields.get('assignee', {}).get('displayName', 'Unassigned') if fields.get('assignee') else 'Unassigned',
            'isCompleted': is_completed(status_name),
            'effectiveDate': effective_date_str,
            'weekLabel': week_label,
            'parent': parent_key
        })

    return {
        'issues': processed,
        'initiatives': processed_initiatives,
        'epics': processed_epics,
        'projects': projects,
        'issueTypes': sorted(list(issue_types)),
        'statusGroups': sorted(list(status_groups)),
        'intervals': intervals,
        'startDate': START_DATE.strftime('%Y-%m-%d'),
        'endDate': END_DATE.strftime('%Y-%m-%d'),
        'lastUpdated': datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
    }

def generate_html(data):
    """Generate the static HTML dashboard"""
    # Read the template
    template_path = os.path.join(os.path.dirname(__file__), 'dashboard_template.html')
    with open(template_path, 'r') as f:
        template = f.read()

    # Inject the data as JSON
    data_json = json.dumps(data, indent=2)
    html = template.replace('__DASHBOARD_DATA__', data_json)

    return html

def main():
    """Main entry point"""
    print("=" * 60)
    print("Generating Pacing Dashboard")
    print("=" * 60)

    # Fetch issues from Jira
    issues, initiatives, epics = fetch_jira_issues()
    if not issues:
        print("No issues fetched, exiting")
        return False

    # Generate intervals
    intervals = generate_intervals()

    # Process issues
    data = process_issues(issues, intervals, initiatives, epics)
    print(f"Processed {len(data['issues'])} issues")
    print(f"Initiatives: {len(data['initiatives'])}")
    print(f"Epics: {len(data['epics'])}")
    print(f"Projects: {list(data['projects'].keys())}")
    print(f"Issue types: {data['issueTypes']}")
    print(f"Status groups: {data['statusGroups']}")

    # Generate HTML
    html = generate_html(data)

    # Write to docs folder (for GitHub Pages)
    docs_dir = os.path.join(os.path.dirname(__file__), 'docs')
    os.makedirs(docs_dir, exist_ok=True)

    output_path = os.path.join(docs_dir, 'index.html')
    with open(output_path, 'w') as f:
        f.write(html)

    print(f"Dashboard generated: {output_path}")
    print(f"Last updated: {data['lastUpdated']}")
    print("=" * 60)
    return True

if __name__ == '__main__':
    success = main()
    exit(0 if success else 1)

