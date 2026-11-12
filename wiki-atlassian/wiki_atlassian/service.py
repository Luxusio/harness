"""Versioned native operations under one already resolved knowledge selection."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import html
import re
from urllib.parse import urlsplit
import uuid

from .models import Page, RemoteError, compact_json, digest, native_id
from .references import native_page_url_id
from .remote import is_scope_denial
from . import storage
from .policy import reject_secrets


def require(condition, code='invalid_request', message='The request does not match the current native context'):
    if not condition:
        raise RemoteError(code, message)


def document_id(value, settings):
    return native_id(value) if isinstance(value, str) and value.isdecimal() else native_page_url_id(
        value, settings.site, space=settings.space, allow_api=True)


def comment_ref(value, origin):
    require(isinstance(value, str) and len(value) <= 4096)
    match = re.fullmatch(r'(footer|inline):([1-9][0-9]{0,31})', value)
    if match:
        return match[1], match[2]
    if value.startswith('https://'):
        require(not any(c.isspace() or ord(c) < 32 or 127 <= ord(c) <= 159 for c in value)
                and '?' not in value and '#' not in value, 'invalid_request')
        uri, expected = urlsplit(value), urlsplit(origin)
        require((uri.scheme, uri.netloc) == (expected.scheme, expected.netloc) and not uri.query and not uri.fragment)
        match = re.fullmatch(r'/wiki/api/v2/(footer|inline)-comments/([1-9][0-9]{0,31})', uri.path)
        require(match is not None)
        return match[1], match[2]
    return None, native_id(value)


class WikiService:
    def __init__(self, settings, remote, replica):
        self.settings, self.remote, self.replica = settings, remote, replica
        self.identity = settings.identity()

    def unchanged(self):
        require(self.settings.identity() == self.identity, 'configuration_changed', 'Connection changed; reconnect and read the target again')

    def _node(self, value):
        return self.remote.require_scope(self.settings, document_id(value, self.settings))

    def _sources(self, data, target=None):
        if 'basedOn' not in data:
            return []
        values = data['basedOn']
        require(type(values) is list and 1 <= len(values) <= 10, 'invalid_source')
        result = []
        for row in values:
            require(type(row) is dict and set(row) == {'document', 'version'}, 'invalid_source')
            require(type(row['version']) is int and row['version'] > 0, 'invalid_source')
            id = document_id(row['document'], self.settings)
            require(id != target and id not in [item[0] for item in result], 'invalid_source')
            result.append((id, row['version']))
        return result

    def _check_sources(self, sources):
        nodes = []
        for id, version in sources:
            node = self.remote.require_scope(self.settings, id)
            require(node.type == 'page' and node.version == version, 'source_changed', 'A basedOn source changed; reconsider the derived content')
            nodes.append(node)
        for node in nodes:
            require(self.remote.require_scope(self.settings, node.id) == node, 'source_changed')
        self.unchanged()

    def _content_policy(self, title, body, before=None, comment=False):
        require(isinstance(title, str) and 0 < len(title.strip()) <= 1000 and all(ord(c) >= 32 and not 127 <= ord(c) <= 159 for c in title))
        require(isinstance(body, str) and len(body.encode('utf-8')) <= (65536 if comment else 4 * 1024 * 1024))
        require(bool(body.strip()) or before is not None and body == before.body)
        reject_secrets(body, self.settings.credentials.api_token)

    def _guard(self, node, before, account, sources=()):
        current = self.remote.require_scope(self.settings, node.id)
        if replace(current, version=node.version) == node and current.version != node.version:
            raise RemoteError('version_conflict', 'Native version changed; read the current document again')
        require(current == node, 'scope_metadata_changed')
        if before is not None:
            require(self.remote.page(node.id, node, self.settings, fresh_storage=True) == before,
                    'source_changed', 'Native content changed before the write')
        require(self.remote.current_user() == account, 'account_changed')
        self._check_sources(sources)
        self.unchanged()

    def _result(self, page, status='read', data=None, account=None):
        data = {} if data is None else data
        format = data.get('format', 'markdown') if status == 'read' else 'storage'
        require(format in ('markdown', 'storage', 'outline'))
        offset, length = data.get('offset', 0), data.get('length', 4000)
        require(type(offset) is int and offset >= 0 and type(length) is int and 1 <= length <= 12000)
        body = page.body
        section = data.get('section')
        if section is not None:
            require('version' in data and format != 'outline', 'invalid_section')
            body = storage.storage_section(body, section)
        content = storage.outline(body) if format == 'outline' else storage.to_markdown(
            body, self._url(page.node.id)) if format == 'markdown' else body
        audit = self.remote.version(page.node.id, page.node.version, scope=self.settings)
        require(audit.get('message', '') == page.version_message, 'readback_mismatch')
        if account is not None:
            require(audit.get('authorId') == account.get('accountId'), 'readback_mismatch')
        require(offset <= len(content))
        end = min(len(content), offset + length)
        result = dict(id=page.node.id, title=page.node.title, version=page.node.version)
        if status != 'read' or data.get('metadata'):
            result['status'] = status
        if status == 'read' or data.get('includeBody', False):
            result.update(format=format, content=content[offset:end], truncated=end < len(content))
            if end < len(content):
                result['nextOffset'] = end
        if section is not None:
            result['section'] = section
        if data.get('metadata'):
            result.update(url=self._url(page.node.id), pageId=page.node.id, parentId=page.node.parent_id,
                          spaceId=page.node.space_id, authorId=audit.get('authorId'), createdAt=audit.get('createdAt'),
                          message=page.version_message, bodyHash=hashlib.sha256(page.body.encode()).hexdigest())
            if account is not None:
                result['verified'] = True
            if 'content' in result:
                result.update(offset=offset, nextOffset=end, totalLength=len(content), offsetUnit='unicode_code_points')
        if account is not None:
            require(self.remote.require_scope(self.settings, page.node.id) == page.node, 'scope_metadata_changed')
            self._content_policy(page.node.title, page.body, page)
            require(self.remote.current_user() == account, 'account_changed')
        self.unchanged()
        return result

    def _url(self, id):
        return f'{self.settings.site}/wiki/spaces/{self.settings.space}/pages/{id}'

    def _publish(self, page, result, before=None):
        try:
            self.replica.publish_verified(page, before)
        except Exception:
            try:
                self.replica.request_repair(page.node.id)
            except Exception:
                pass
            result.update(syncRequired=True, next='Saved remotely; automatic synchronization will repair the replica. Do not repeat the write.')
        return result

    def execute(self, operation, data):
        effect = {'started': False, 'saved': False, 'reference': None}
        try:
            self.unchanged()
            with self.remote.operation(timeout=30 if operation in ('get_document', 'list_document_versions', 'list_comments') else 45):
                if operation == 'search_documents':
                    return self.replica.search(data)
                if operation in ('document_relations', 'graph'):
                    return self.replica.relations(data)
                if operation == 'get_document':
                    local = self.replica.get_document(data)
                    if local is not None:
                        return local
                    require('snapshot' not in data, 'snapshot_unavailable')
                    node = self._node(data['document'])
                    version = data.get('version', node.version)
                    require(type(version) is int and 1 <= version <= node.version)
                    require(not data.get('offset') or 'version' in data, 'invalid_request', 'Continued reads require a native version')
                    page = (self.remote.page(node.id, node, self.settings) if version == node.version
                            else self.remote.historical_page(node.id, version, self.settings))
                    result = self._result(page, data=data)
                    require(self.remote.require_scope(self.settings, node.id) == node, 'scope_metadata_changed')
                    return result
                if operation == 'list_document_versions':
                    node = self._node(data['document'])
                    batch = self.remote.list_versions(node.id, data.get('limit', 20), data.get('cursor'), self.settings)
                    require(self.remote.require_scope(self.settings, node.id) == node, 'scope_metadata_changed')
                    return {**{k: v for k, v in batch.items() if k != 'results'}, 'status': 'listed',
                            'document': node.id, 'versions': batch['results']}
                if operation in ('create_document', 'update_document'):
                    return self._write_document(operation, data, effect)
                if operation in ('list_comments', 'create_comment', 'update_comment'):
                    return self._comments(operation, data, effect)
                raise RemoteError('invalid_request', 'Unknown wiki operation')
        except Exception as exc:
            fence_pending = False
            if is_scope_denial(exc):
                try:
                    self.replica.handle_denial(exc)
                except Exception:
                    fence_pending = True
                    wake = getattr(self.replica, 'on_repair', None)
                    if wake is not None:
                        try:
                            wake()
                        except Exception:
                            pass  # Preserve the original effect/error and pending fence.
            unknown = effect['saved'] or (effect['started'] and not isinstance(exc, RemoteError)) or getattr(exc, 'outcome_unknown', False)
            result = dict(status='unknown' if unknown else 'error', code=getattr(exc, 'code', 'unknown_outcome' if unknown else 'invalid_request'),
                          message=str(exc) if isinstance(exc, RemoteError) else 'Wiki operation could not be completed')
            if isinstance(exc, RemoteError) and exc.details:
                result['details'] = dict(exc.details)
            if unknown:
                result.update(operation=operation, request={k: v for k, v in data.items() if k in ('document', 'target', 'comment', 'parent', 'version')},
                              requestHash=digest(data), next='Inspect native content/history using read-only tools. Retain the original request; never automatically repeat this write.')
                if effect['reference']:
                    result.setdefault('details', {})['reference'] = effect['reference']
            elif getattr(exc, 'next', None):
                result['next'] = exc.next
            if fence_pending:
                result.setdefault('details', {}).update(scopeFence='pending', scopeFenceCode='replica_denial_pending',
                    scopeFenceNext='This process has refused access; shared revocation publication is pending. Automatic synchronization will retry.')
            return result

    def _write_document(self, operation, data, effect):
        creating = operation == 'create_document'
        supplied = data.get('document', 'memory')
        logical = not creating and supplied in ('memory', 'feedback')
        modes = [name for name in ('content', 'append', 'edits', 'links') if name in data]
        if 'old_text' in data or 'new_text' in data:
            require('old_text' in data and 'new_text' in data, 'invalid_edit')
            modes.append('patch')
        pure_append = not creating and modes == ['append'] and not any(k in data for k in ('title', 'parent', 'section')) and not data.get('allowReferenceRemoval')
        require(creating or not logical and 'version' in data or pure_append, 'invalid_edit', 'Only a pure append may omit document or version')
        node = before = None
        link_targets = []
        if creating:
            id = document_id(data['parent'], self.settings) if data.get('parent') is not None else self.settings.root_page_id
            if id is None:
                require(self.settings.whole_space, 'selection_required')
                self.remote.require_space(self.settings)
            else:
                node = self.remote.require_scope(self.settings, id)
            title = data['title']
            body = storage.to_storage(data['content'], data.get('format', 'storage'))
        else:
            if logical:
                id = self.settings.config.get('memoryPageId' if supplied == 'memory' else 'feedbackPageId')
                require(id is not None, 'selection_required', 'Choose an existing configured recording page')
            else:
                id = document_id(supplied, self.settings)
            node = self.remote.require_scope(self.settings, id)
            require(node.type == 'page')
            before = self.remote.page(id, node, self.settings, fresh_storage=True)
            expected = data.get('version')
            require(expected is None or type(expected) is int and expected > 0, 'invalid_version')
            require(expected is None or expected == node.version, 'version_conflict', 'Native version changed; read the current page')
            require(len(modes) <= 1 and (modes or 'title' in data or 'parent' in data), 'invalid_edit')
            require(not data.get('allowReferenceRemoval') or modes == ['content'], 'invalid_edit')
            title = data.get('title', node.title)
            format = data.get('format', 'text' if pure_append and (logical or expected is None) else 'storage')
            require(format in ('storage', 'text'), 'invalid_edit')
            if 'content' in data:
                body = storage.to_storage(data['content'], format)
                if 'section' in data:
                    require(not data.get('allowReferenceRemoval'), 'invalid_section')
                    body = storage.replace_section(before.body, data['section'], body)
                else:
                    storage.require_reference_preservation(before.body, body, data.get('allowReferenceRemoval', False))
            elif 'section' in data:
                raise RemoteError('invalid_section', 'A section edit requires content and a native version')
            elif 'append' in data:
                require(bool(data['append'].strip()), 'invalid_edit')
                addition = storage.to_storage(data['append'], format)
                body = before.body + addition
            elif 'links' in data:
                require(type(data['links']) is dict and 1 <= len(data['links']) <= 20, 'invalid_links')
                for target in data['links'].values():
                    require(type(target) is str, 'invalid_links')
                    linked_id = document_id(target.partition('#')[0], self.settings)
                    linked_node = self.remote.require_scope(self.settings, linked_id)
                    require(linked_node.type == 'page', 'invalid_links')
                    link_targets.append(linked_id)
                body = storage.retarget_links(before.body, data['links'], self.settings.site, self.settings.space)
            elif 'edits' in data or 'patch' in modes:
                edits = data.get('edits', [{'old_text': data.get('old_text'), 'new_text': data.get('new_text')}])
                body = storage.apply_edits(before.body, edits, format)
            else:
                body = before.body
        sources = self._sources(data, None if creating else id)
        self._check_sources(sources)
        account = self.remote.current_user()
        require(isinstance(account.get('accountId'), str) and account['accountId'], 'account_unavailable')
        if logical:
            added = '<p><ac:link><ri:user ri:account-id="' + html.escape(account['accountId'], quote=True) + '" /></ac:link></p>' + addition
            if supplied == 'feedback':
                headings = storage.native_headings(before.body)
                matches = [row for row in headings if row['text'].strip() == '개선 의견']
                require(len(matches) == 1, 'selection_required', 'Choose a unique existing improvement section')
                _, end = storage.section_range(before.body, matches[0]['key'])
                body = before.body[:end] + added + before.body[end:]
            else:
                body = before.body + added
        self._content_policy(title, body, before)
        destination = None
        original_ancestry = destination_ancestry = None
        if not creating and 'parent' in data:
            require(not logical and 'version' in data and id != self.settings.root_page_id, 'invalid_move')
            destination = document_id(data['parent'], self.settings)
            original_ancestry = self.remote.ancestry(self.settings, id)
            destination_ancestry = self.remote.ancestry(self.settings, destination)
            require(id not in [n.id for n in destination_ancestry] and
                    {n.id for n in original_ancestry[1:]}.issubset({n.id for n in destination_ancestry}), 'invalid_move',
                    'A move must retain every existing ancestor and its inherited access restrictions')
        require(creating or body != before.body or title != before.node.title or destination is not None and destination != node.parent_id, 'invalid_edit', 'The edit makes no change')
        message = 'wiki ' + ('create' if creating else 'update') + ' document ' + str(uuid.uuid4())
        def guard():
            if node is None:
                self.remote.require_space(self.settings)
                require(self.remote.current_user() == account, 'account_changed')
                self._check_sources(sources)
            else:
                self._guard(node, before, account, sources)
            if destination is not None:
                require(self.remote.ancestry(self.settings, id) == original_ancestry and
                        self.remote.ancestry(self.settings, destination) == destination_ancestry, 'scope_metadata_changed')
            for linked_id in link_targets:
                require(self.remote.require_scope(self.settings, linked_id).type == 'page', 'scope_metadata_changed')
            self._content_policy(title, body, before)
            effect['started'] = True
        saved = (self.remote.create(self.settings.space_id, id, title, body, message, self.settings, guard)
                 if creating else self.remote.update(before, body, message, self.settings, title, guard, destination))
        effect.update(saved=True, reference=saved.node.id)
        require(self.remote.current_user() == account, 'account_changed')
        self._check_sources(sources)
        for linked_id in link_targets:
            require(self.remote.require_scope(self.settings, linked_id).type == 'page', 'scope_metadata_changed')
        if destination is not None:
            require(self.remote.ancestry(self.settings, saved.node.id) == [saved.node, *destination_ancestry], 'scope_metadata_changed')
        return self._publish(saved, self._result(saved, 'created' if creating else 'updated', data, account), before)

    def _resolve_comment(self, value):
        kind, id = comment_ref(value, self.settings.site)
        if kind is not None:
            return kind, id, self.remote.comment_location(id, kind)
        try:
            location = self.remote.comment_location(id, 'footer')
            return 'footer', id, location
        except RemoteError as exc:
            if exc.code != 'http_404':
                raise
            return 'inline', id, self.remote.comment_location(id, 'inline')

    def _comment_row(self, row, data, page_id):
        result = dict(row) if data.get('metadata') else {key: row[key] for key in ('id', 'body', 'format', 'version') if key in row}
        result['reference'] = row['kind'] + ':' + row['id']
        context = row.get('inlineContext')
        result.pop('inlineContext', None)
        if context:
            visible = {k: v for k, v in context.items() if data.get('metadata') or k == 'resolutionStatus' or k == 'inlineOriginalSelection' and data.get('includeBody', True)}
            if visible:
                result['inlineContext'] = visible
        if data.get('includeBody', True) is False:
            result.pop('body', None)
            result.pop('format', None)
        elif data.get('format', 'markdown') == 'markdown' and 'body' in result:
            result.update(body=storage.to_markdown(result['body'], self._url(page_id)), format='markdown')
        return result

    def _comments(self, operation, data, effect):
        updating = operation == 'update_comment'
        ref = self._resolve_comment(data['comment']) if updating else None
        target = data.get('target')
        if ref is None and target and (target.startswith(('footer:', 'inline:')) or '-comments/' in target):
            ref = self._resolve_comment(target)
        page_id = ref[2][0] if ref else document_id(target, self.settings)
        node = self.remote.require_scope(self.settings, page_id)
        if operation == 'list_comments':
            from .cursors import decode_cursor, encode_cursor
            limit, replies = data.get('limit', 20), data.get('repliesPerComment', 3)
            expanded = data.get('includeReplies', False)
            require(1 <= limit <= 50 and 1 <= replies <= 10 and (not expanded or limit * replies <= 100))
            require(not expanded or ref is None)
            format = data.get('format', 'markdown')
            require(format in ('storage', 'markdown'))
            binding = [self.identity, page_id, None if ref is None else list(ref[:2]), limit, format, expanded, replies if expanded else 0]
            state = decode_cursor(data['cursor'], binding) if 'cursor' in data else {}
            kind = ref[0] if ref else state.get('kind', 'footer')
            require(kind in ('footer', 'inline'))
            selected = self.remote.comment_detail(page_id, ref[1], ref[0], ref[2][1], node, self.settings) if ref else None
            batch = self.remote.list_comments(page_id, kind, ref[1] if ref else None, limit, state.get('native'), self.settings)
            rows = []
            for row in batch['results']:
                projected = self._comment_row(row, {**data, 'format': format}, page_id)
                if expanded:
                    children = self.remote.list_comments(page_id, kind, row['id'], replies, None, self.settings)
                    reply_binding = [self.identity, page_id, [kind, row['id']], replies, format, False, 0]
                    projected['replies'] = dict(target=f"{kind}:{row['id']}", limit=replies, format=format,
                        comments=[self._comment_row(child, {**data, 'format': format}, page_id) for child in children['results']],
                        hasMore=bool(children.get('cursor')), complete=not children.get('cursor'))
                    if children.get('cursor'):
                        projected['replies']['cursor'] = encode_cursor(reply_binding, {'kind': kind, 'native': children['cursor']})
                rows.append(projected)
            native = batch.get('cursor')
            more = bool(native) or ref is None and kind == 'footer'
            result = dict(status='listed', target=page_id, comments=rows, hasMore=more, complete=not more,
                          threadScope='direct native replies' if ref else 'page root comments with bounded replies' if expanded else 'page root comments; replies not included')
            if selected:
                result['comment'] = self._comment_row(selected, {**data, 'format': format}, page_id)
            if more:
                result['cursor'] = encode_cursor(binding, {'kind': kind if native else 'inline', 'native': native})
            require(self.remote.require_scope(self.settings, page_id) == node, 'scope_metadata_changed')
            return result
        page = self.remote.page(page_id, node, self.settings, fresh_storage=True)
        body = storage.to_storage(data['text'], data.get('format', 'storage'))
        self._content_policy(node.title, body, comment=True)
        account = self.remote.current_user()
        before = self.remote.comment_detail(page_id, ref[1], ref[0], ref[2][1], node, self.settings) if updating else None
        if before:
            require(before['version'] == data['version'] and before['status'] == 'current', 'version_conflict')
        reply = None if updating else self._resolve_comment(data['replyTo']) if 'replyTo' in data else ref
        require(reply is None or reply[2][0] == page_id, 'invalid_reply')
        reply_snapshot = self.remote.comment_detail(page_id, reply[1], reply[0], reply[2][1], node, self.settings) if reply else None
        selection = None
        if 'selection' in data:
            require(not updating and reply is None and ref is None, 'invalid_selection')
            index, count = storage.inline_selection(page.body, data['selection'], data.get('occurrence'))
            selection = data['selection'], index, count
        else:
            require('occurrence' not in data, 'invalid_selection')
        def guard():
            self._guard(node, page, account)
            if before:
                require(self.remote.comment_detail(page_id, ref[1], ref[0], ref[2][1], node, self.settings) == before, 'source_changed')
            if reply:
                require(self.remote.comment_location(reply[1], reply[0]) == reply[2] and
                        self.remote.comment_detail(page_id, reply[1], reply[0], reply[2][1], node, self.settings) == reply_snapshot, 'source_changed')
            effect['started'] = True
        saved = (self.remote.update_comment(page_id, before, body, self.settings, guard) if before else
                 self.remote.create_comment(page_id, body, reply[1] if reply else None, account['accountId'], self.settings,
                                            'inline' if selection else reply[0] if reply else 'footer', selection, guard))
        effect.update(saved=True, reference=saved['kind'] + ':' + saved['id'])
        require(saved.get('editorId') == account['accountId'], 'readback_mismatch')
        result = self._comment_row(saved, {**data, 'format': data.get('format', 'storage'), 'includeBody': data.get('includeBody', False)}, page_id)
        result['status'] = 'updated' if before else 'created'
        if selection:
            current = self.remote.require_scope(self.settings, page_id)
            require(replace(current, version=node.version) == node and current.version - node.version in (0, 1), 'scope_metadata_changed')
            marked = self.remote.page(page_id, current, self.settings, fresh_storage=True)
            marker = saved.get('inlineContext', {}).get('inlineMarkerRef')
            require(storage.selection_anchored(marked.body, selection[0], selection[1], marker), 'readback_mismatch')
            result = self._publish(marked, result, page)
        else:
            require(self.remote.require_scope(self.settings, page_id) == node, 'scope_metadata_changed')
        require(self.remote.current_user() == account, 'account_changed')
        return result
