"""Project-scoped Jira Cloud issue reads and verified comment writes."""
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
            with self.operation(max_requests=4, max_response_bytes=1024 * 1024, timeout=25):
                if operation == 'get_jira_issue':
                    return self._get_issue(key, project, data['includeComments'])
                return self._add_comment(key, project, data['text'])
        except RemoteError as error:
            if error.outcome_unknown:
                return self._unknown(key)
            return dict(status='error', code=error.code, message=str(error))
