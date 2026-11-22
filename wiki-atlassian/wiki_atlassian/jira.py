"""Project-scoped Jira Cloud issue reads and verified mutations."""
from __future__ import annotations

from html import escape
import re

from document_destination import JIRA_PROJECT_KEY

from .models import RemoteError, native_id
from .policy import reject_secrets
from .remote import Remote


ISSUE_KEY = re.compile(rf'({JIRA_PROJECT_KEY})-[1-9][0-9]{{0,19}}')
ISSUE_FIELDS = 'project,summary,status,issuetype,description,updated'
RECENT_COMMENTS = 10


class Jira(Remote):
    product = 'Jira'
    api_paths = ('/rest/api/3/',)
    wiki_aliases = ()

    def __init__(self, credentials, projects, transport=None):
        super().__init__(credentials, transport)
        self.projects = frozenset(projects)

    def _issue(self, key, project, fields):
        _, issue = self._request('GET', self._endpoint(
            f'/rest/api/3/issue/{key}', {'fields': fields, 'updateHistory': 'false'}))
        metadata = issue.get('fields')
        native_project = metadata.get('project') if isinstance(metadata, dict) else None
        # Jira can resolve an old issue key to an issue moved to another project.
        if issue.get('key') != key or not isinstance(native_project, dict) or native_project.get('key') != project:
            raise RemoteError('jira_issue_scope_changed', 'Jira did not return the exact issue in its configured project')
        return native_id(issue.get('id')), metadata

    def _issue_url(self, key):
        return f'{self.origin}/browse/{key}'

    def _get_issue(self, key, project, include_comments):
        issue_id, metadata = self._issue(key, project, ISSUE_FIELDS)
        result = dict(status='read', id=issue_id, key=key, project=project, url=self._issue_url(key),
                      fields={k: metadata[k] for k in ISSUE_FIELDS.split(',') if k in metadata and k != 'project'})
        if include_comments:
            _, response = self._request('GET', self._endpoint(f'/rest/api/3/issue/{issue_id}/comment', {
                'maxResults': RECENT_COMMENTS, 'orderBy': '-created',
            }))
            comments = response.get('comments')
            if not isinstance(comments, list) or len(comments) > RECENT_COMMENTS:
                raise RemoteError('malformed_response', 'Jira returned an invalid comment page')
            result['comments'] = [{k: row[k] for k in ('id', 'body', 'created', 'updated') if k in row}
                                  for row in comments if isinstance(row, dict)]
            current_id, _ = self._issue(key, project, 'project')
            if current_id != issue_id:
                raise RemoteError('jira_issue_scope_changed', 'Issue identity changed during comment retrieval')
            result['commentTotal'] = response.get('total')
        return result

    def _add_comment(self, key, project, text):
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        if not text.strip() or len(text) > 10000 or any(ord(c) < 32 and c not in '\n\t' for c in text):
            raise RemoteError('invalid_request', 'Use 1 to 10,000 characters of plain comment text')
        reject_secrets(f'<p>{escape(text)}</p>', self.credentials.api_token)
        body = {'type': 'doc', 'version': 1, 'content': [
            {'type': 'paragraph', 'content': [{'type': 'text', 'text': line}]} if line else {'type': 'paragraph'}
            for line in text.split('\n')
        ]}
        issue_id, _ = self._issue(key, project, 'project')
        endpoint = f'/rest/api/3/issue/{issue_id}/comment'
        _, created = self._request('POST', endpoint, {'body': body})
        comment_id = None
        try:
            comment_id = native_id(created.get('id'))
            _, saved = self._request('GET', f'{endpoint}/{comment_id}')
            current_id, _ = self._issue(key, project, 'project')
            if saved.get('id') == comment_id and saved.get('body') == body and current_id == issue_id:
                return dict(status='created', key=key, commentId=comment_id,
                            url=f'{self._issue_url(key)}?focusedCommentId={comment_id}')
        except RemoteError:
            pass
        return self._unknown(key, comment_id)

    @staticmethod
    def _status(value):
        if not isinstance(value, dict) or not isinstance(value.get('name'), str) or not value['name'].strip():
            raise RemoteError('malformed_response', 'Jira returned invalid workflow status metadata')
        return {'id': native_id(value.get('id')), 'name': value['name']}

    def _issue_status(self, key, project):
        issue_id, fields = self._issue(key, project, 'project,status')
        return issue_id, self._status(fields.get('status'))

    def _available_transitions(self, key, project):
        issue_id, current = self._issue_status(key, project)
        endpoint = f'/rest/api/3/issue/{issue_id}/transitions'
        _, response = self._request('GET', endpoint)
        rows = response.get('transitions')
        if not isinstance(rows, list):
            raise RemoteError('malformed_response', 'Jira returned invalid workflow transitions')
        transitions = {}
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get('name'), str):
                raise RemoteError('malformed_response', 'Jira returned an invalid workflow transition')
            if row.get('isAvailable') is False:
                continue
            transition_id = native_id(row.get('id'))
            if transition_id in transitions:
                raise RemoteError('malformed_response', 'Jira returned duplicate workflow transition IDs')
            transitions[transition_id] = dict(id=transition_id, name=row['name'], to=self._status(row.get('to')))
        if self._issue_status(key, project) != (issue_id, current):
            raise RemoteError('jira_status_changed', 'The issue or status changed; list transitions again')
        return issue_id, current, transitions

    def _transition_issue(self, key, project, transition_id, from_status_id, target_status_id):
        issue_id, current, transitions = self._available_transitions(key, project)
        if current['id'] != from_status_id:
            raise RemoteError('jira_status_changed', 'The current status differs from fromStatusId; list transitions again')
        selected = transitions.get(transition_id)
        if selected is None:
            raise RemoteError('jira_transition_unavailable', 'Choose a currently available transition ID')
        target = selected['to']
        if target['id'] != target_status_id:
            raise RemoteError('jira_transition_destination_changed', 'The transition destination changed; list transitions again')
        if target['id'] == current['id']:
            raise RemoteError('jira_already_in_status', 'The issue is already in the transition destination status')
        unknown = dict(status='unknown', code='jira_transition_unverified', key=key, targetStatus=target,
                       next='Read this issue to verify its current status before taking any further action.')
        try:
            self._request('POST', f'/rest/api/3/issue/{issue_id}/transitions',
                          {'transition': {'id': transition_id}}, allow_no_content=True)
        except RemoteError as error:
            if error.outcome_unknown:
                return unknown
            raise
        try:
            saved_id, saved = self._issue_status(key, project)
            if saved_id == issue_id and saved['id'] == target['id']:
                return dict(status='transitioned', key=key, previousStatus=current, currentStatus=saved,
                            transitionId=transition_id, url=self._issue_url(key))
        except RemoteError:
            pass
        return unknown

    @staticmethod
    def _unknown(key, comment_id=None):
        return dict(status='unknown', code='jira_comment_unverified', key=key, commentId=comment_id,
                    next='Read this issue and its recent comments before any new submission. Do not retry automatically.')

    def execute(self, operation, data):
        key = data['issue'].upper()
        match = ISSUE_KEY.fullmatch(key) if data['issue'].isascii() else None
        if match is None:
            return dict(status='error', code='invalid_issue_key', message='Use an exact Jira issue key, for example TEAM-123')
        project = match[1]
        if project not in self.projects:
            return dict(status='error', code='jira_project_not_configured', message='Select an issue in a configured Jira project')
        try:
            with self.operation(max_requests=5, max_response_bytes=1024 * 1024, timeout=25):
                if operation == 'get_jira_issue':
                    return self._get_issue(key, project, data['includeComments'])
                if operation == 'add_jira_comment':
                    return self._add_comment(key, project, data['text'])
                if operation == 'list_jira_transitions':
                    _, current, transitions = self._available_transitions(key, project)
                    return dict(status='read', key=key, currentStatus=current, transitions=list(transitions.values()))
                return self._transition_issue(key, project, native_id(data['transitionId']),
                                              native_id(data['fromStatusId']), native_id(data['targetStatusId']))
        except RemoteError as error:
            if error.outcome_unknown:
                return self._unknown(key)
            return dict(status='error', code=error.code, message=str(error))
