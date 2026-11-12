"""Shared, credential-admitted native wiki read projection.

The manifest commits a generation; its journal prevents readers from observing
half-published Markdown. Storage remains immutable and hash addressed. All file
access and cross-host locks use the shared native platform implementation.
"""
from __future__ import annotations

from collections import deque
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import threading
import time
import unicodedata
from urllib.parse import urlsplit
import uuid

from wiki_platform import safe_metadata_read, trusted_metadata_directory, ensure_private_directory, atomic_write, file_lock
from . import changes
from .models import Node, Page, RemoteError, compact_json, native_id
from .references import native_page_url_id
from .search import Query, Match, authored_start, projected_headings

BODY_LIMIT = 8 * 1024 * 1024
MANIFEST_LIMIT = 16 * 1024 * 1024
PAGE_LIMIT = 20_000
_DEVICE = r'(?i)(CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?'


def _hash(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def _iso(millis):
    try:
        return datetime.fromtimestamp(millis / 1000, timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
    except (OverflowError, ValueError, OSError):
        return None


def _canonical(value):
    return unicodedata.normalize('NFC', value).casefold()


def _relative(value):
    if (not isinstance(value, str) or not value or '\\' in value or ':' in value
        or value.startswith('/') or any(p in ('', '.', '..') for p in value.split('/'))
        or any(ord(c) < 32 for c in value)):
        raise ValueError('Invalid replica path')
    # Windows strips trailing dots/spaces and interprets device names even with extensions.
    parts = value.split('/')
    if len(parts) > 102:
        raise ValueError('Replica path exceeds ancestry budget')
    for part in parts:
        if (len(part.encode('utf-8')) > 240 or part.rstrip(' .') != part or re.search(r'[<>"|?*]', part)
            or re.fullmatch(_DEVICE, part)):
            raise ValueError('Invalid replica path component')
    return value


def _segment(title):
    name = re.sub(r'[\x00-\x1f<>:"/\\|?*]', '_', unicodedata.normalize('NFC', title)).rstrip(' .')
    name = name if name and name not in ('.', '..') else '_'
    if re.fullmatch(_DEVICE, name):
        name = '_' + name
    if len(name.encode('utf-8')) > 200:
        raise ValueError('Page title exceeds mirror filename budget')
    return name


def _node(row):
    return Node(row['id'], row['title'], row.get('parentId'), row['type'], row['spaceId'], row['version'], row['status'])


def _row(node, path, storage, body_hash):
    row = dict(id=node.id, title=node.title)
    if node.parent_id is not None:
        row['parentId'] = node.parent_id
    row.update(type=node.type, spaceId=node.space_id, version=node.version, status=node.status,
               path=path, storage=storage, bodyHash=body_hash)
    return row


def _owners(pages, known=None):
    known = pages if known is None else known
    known_directories, files, directories = {}, {}, {}
    for id, row in known.items():
        path = _relative(row['path'])
        if not path.startswith('pages/') or not path.endswith('.md'):
            raise ValueError('Invalid replica Markdown path')
        directory = _canonical(path[:-3])
        if directory in known_directories:
            raise ValueError('Mirror subtree has multiple owners')
        known_directories[directory] = id
    for id, row in pages.items():
        path = _canonical(_relative(row['path']))
        if path in files:
            raise ValueError('Mirror Markdown path has multiple owners')
        files[path] = id
        reservations = [(path[:-3], id)]
        parent = path.rsplit('/', 1)[0]
        direct = True
        while parent != 'pages':
            expected = row.get('parentId') if direct else None
            owner = known_directories.get(parent)
            # Folder components have no Markdown row; their owner is still native.
            if owner is not None and expected is not None and owner != expected:
                raise ValueError('Mirror subtree has multiple owners')
            reservations.append((parent, expected or owner))
            if '/' not in parent:
                raise ValueError('Invalid replica subtree')
            parent = parent.rsplit('/', 1)[0]
            direct = False
        for directory, owner in reservations:
            if directory in directories and directories[directory] is not None and owner is not None and directories[directory] != owner:
                raise ValueError('Mirror subtree has multiple owners')
            if directory not in directories or owner is not None:
                directories[directory] = owner
    if set(files) & set(directories):
        raise ValueError('Mirror path is both a file and directory')
    return files, directories


def knowledge_relations(body, origin, space):
    """Only explicitly labelled links in the authored knowledge-relations section."""
    from .storage import parse_storage, Text
    root = parse_storage(body)
    excluded = {'pre', 'code', 'blockquote', 'q', 'ac:structured-macro', 'ac:macro', 'ac:plain-text-body'}
    types = {'근거': 'evidence', '의존': 'depends_on', '반박': 'contradicts', '대체': 'supersedes', '관련': 'related'}
    section, items, complete = 0, [], True
    for element in root.descendants():
        if any(p.tag in excluded for p in element.ancestors()):
            continue
        if re.fullmatch('h[1-6]', element.tag):
            level = int(element.tag[1])
            if section and level <= section:
                section = 0
            if ' '.join(element.text().split()) == '지식 연결' and not any(p.tag in excluded for p in element.descendants()):
                section = level
            continue
        if not section or element.tag not in ('p', 'li') or any(p.tag == 'li' for p in element.ancestors()):
            continue
        match = re.match(r'^(근거|의존|반박|대체|관련)\s*:\s*', ' '.join(element.text().split()))
        if match is None:
            continue
        descendants = list(element.descendants())
        links = [p for p in descendants if (p.tag == 'a' and 'href' in p.attrs)
                 or (p.tag == 'ri:page' and p.parent is not None and p.parent.tag == 'ac:link'
                     and not any(a.tag in ('ac:image', 'ri:attachment') for a in p.ancestors()))]
        if len(links) != 1 or any(p.tag in excluded for p in descendants) or len(items) >= 100:
            complete = False
            continue
        link = links[0]
        anchor = link.parent if link.parent is not None and link.parent.tag == 'ac:link' else link
        # Read positioned text from the balanced native tree. A raw suffix can
        # begin with a formatting close tag when the link is inside strong/em.
        parts, pending = [], list(reversed(element.children))
        while pending:
            child = pending.pop()
            if isinstance(child, Text):
                if child.start >= anchor.end:
                    parts.append(child.text())
            elif child.end > anchor.end:
                if child.tag == 'br' and child.start >= anchor.end:
                    parts.append('\n')
                else:
                    pending.extend(reversed(child.children))
        suffix = ' '.join(''.join(parts).split())
        reason = re.fullmatch(r'[—–-]\s+(.+)', suffix)
        if reason is None or not 1 <= len(reason[1]) <= 1000:
            complete = False
            continue
        item = dict(type=types[match[1]], reason=reason[1])
        try:
            if link.tag == 'ri:page':
                if link.attrs.get('ri:space-key', space) != space:
                    raise ValueError('Cross-space relation')
                if 'ri:content-id' in link.attrs:
                    item['to'] = native_id(link.attrs['ri:content-id'])
                else:
                    title = link.attrs['ri:content-title']
                    if not title.strip() or len(title) > 1000:
                        raise ValueError('Invalid relation title')
                    item['title'] = title
                fragment = anchor.attrs.get('ac:anchor')
            else:
                href = link.attrs['href']
                href = origin + href if href.startswith('/wiki/') else href
                if (href.count('#') > 1 or re.search(r'%(?![0-9a-fA-F]{2})', href)
                    or any(c.isspace() or ord(c) < 32 or 127 <= ord(c) <= 159 for c in href)
                    or any(c in href for c in '[]<>"{}|^`')):
                    raise ValueError('Invalid native relation URI')
                uri = urlsplit(href)
                item['to'] = native_page_url_id(href.partition('#')[0], origin, space=space)
                fragment = uri.fragment
            if fragment:
                if len(fragment) > 1000 or any(ord(c) < 32 for c in fragment):
                    raise ValueError('Invalid relation fragment')
                item['fragment'] = fragment
            if item not in items:
                items.append(item)
        except (ValueError, KeyError, RemoteError):
            complete = False
    return dict(items=items, complete=complete)


class Replica:
    def __init__(self, settings, remote):
        self.settings, self.remote = settings, remote
        self.base = settings.corpus_root / 'confluence' / settings.space
        self.control = self.base / '.sync'
        self.pending_repair = set()
        self.on_repair = None
        self.on_authorization_denied = None
        self.last_failure = None
        self.last_success = None
        self._denied = False
        self._denial_pending = False
        self._denial_generation = 0
        self._state_lock = threading.Lock()
        self.search_identity = _hash(str(settings.corpus_root) + '\0' + settings.space)
        self.closed = False

    def close(self):
        self.closed = True

    def _path(self, relative, base=None):
        return (self.base if base is None else base) / PurePosixPath(_relative(relative))

    def _read(self, path, limit=MANIFEST_LIMIT):
        raw = safe_metadata_read(path, limit)
        if isinstance(raw, str):
            raw = raw.encode('utf-8')
        def invalid_constant(value):
            raise ValueError('Invalid replica JSON constant')
        value = json.loads(raw.decode('utf-8'), parse_constant=invalid_constant)
        if not isinstance(value, dict):
            raise ValueError('Invalid replica metadata')
        return value

    def _write(self, path, text):
        if not path.parent.exists():
            ensure_private_directory(path.parent)
        atomic_write(path, text.encode('utf-8') if isinstance(text, str) else text)

    def _metadata(self, path, value):
        limit = 65536 if path.name == 'state.json' else MANIFEST_LIMIT
        data = bytearray()
        encoder = json.JSONEncoder(ensure_ascii=False, separators=(',', ':'), allow_nan=False)
        for chunk in encoder.iterencode(value):
            encoded = chunk.encode('utf-8')
            if len(data) + len(encoded) > limit:
                raise RemoteError('replica_metadata_limit', 'Candidate metadata exceeds its readable byte budget')
            data.extend(encoded)
        self._write(path, bytes(data))

    def _manifest(self, missing=False):
        try:
            manifest = self._read(self.control / 'manifest.json')
        except FileNotFoundError:
            if missing:
                return {}
            raise RemoteError('replica_missing', 'No complete local snapshot exists yet') from None
        self._validate(manifest)
        return manifest

    def _validate(self, manifest):
        try:
            if (type(manifest['schemaVersion']) is not int or manifest['schemaVersion'] != 1
                or manifest['site'].rstrip('/') != self.settings.site
                or manifest['spaceId'] != self.settings.space_id
                or manifest['homePageId'] != (self.settings.root_page_id or '')
                or manifest.get('wholeSpace', False) != self.settings.whole_space
                or manifest.get('knowledgeRootPageId') != self.settings.root_page_id
                or manifest.get('structurePolicy') != 'knowledge'):
                raise ValueError('Replica identity changed')
            pages = manifest['pages']
            if not isinstance(pages, dict) or len(pages) > PAGE_LIMIT:
                raise ValueError('Invalid replica page bound')
            for id, row in pages.items():
                node = _node(row)
                native_id(id)
                native_id(node.space_id)
                if node.parent_id is not None:
                    native_id(node.parent_id)
                if (node.id != id or node.type != 'page' or node.status != 'current'
                    or type(node.version) is not int or not 1 <= node.version <= 2147483647
                    or not isinstance(node.title, str) or len(node.title) > 1000
                    or not isinstance(row['bodyHash'], str) or not changes.HASH.fullmatch(row['bodyHash'])):
                    raise ValueError('Invalid replica node')
                _relative(row['path'])
                _relative(row['storage'])
                if row['storage'] != f'.sync/storage/{id}-{node.version}-{row["bodyHash"]}.xhtml':
                    raise ValueError('Invalid native body path')
            _owners(pages)
            for key in ('explicitPages', 'unavailable'):
                values = manifest.get(key, [])
                if (not isinstance(values, list) or len(values) != len(set(values))
                    or any(id not in pages or not changes.ID.fullmatch(id) for id in values)):
                    raise ValueError('Invalid replica selection')
            if len(manifest.get('explicitPages', [])) > 500:
                raise ValueError('Explicit selection too large')
            folders = manifest.get('folderParents', {})
            if not isinstance(folders, dict) or len(folders) > PAGE_LIMIT:
                raise ValueError('Invalid folder bound')
            for id, parent in folders.items():
                native_id(id)
                native_id(parent)
                if id in pages or id == parent or id == self.settings.root_page_id:
                    raise ValueError('Invalid replica folder')
            binding = manifest.get('credentialBinding')
            if binding is not None and (not isinstance(binding, str) or not changes.HASH.fullmatch(binding)):
                raise ValueError('Invalid credential binding')
            if type(manifest.get('inventoryComplete', False)) is not bool:
                raise ValueError('Invalid inventory status')
            if type(manifest.get('wholeSpace', False)) is not bool:
                raise ValueError('Invalid whole-space selection')
            if datetime.fromisoformat(manifest['cursor'].replace('Z', '+00:00')).tzinfo is None:
                raise ValueError('Invalid replica cursor timestamp')
            changes.validate(manifest)
        except (KeyError, TypeError, ValueError, AttributeError, RemoteError):
            raise RemoteError('invalid_mirror_snapshot', 'Synchronized mirror data is malformed; preserve it and rebuild the disposable mirror') from None

    @contextmanager
    def _locked_manifest(self):
        identity = self.settings.identity()
        try:
            with file_lock(self.control / 'sync.lock', shared=True):
                if (self.control / 'transaction.json').exists():
                    raise RemoteError('replica_recovery_pending', 'Automatic synchronization must finish the interrupted publication')
                manifest = self._manifest()
                if manifest.get('credentialBinding') != self.settings.credential_fingerprint:
                    raise RemoteError('credential_reauthorization_pending', 'Wait for complete synchronization with the current credentials')
                if self._denied or (self.control / changes.DENIAL).exists():
                    raise RemoteError('root_scope_denied', 'Confirmed scope denial blocks this snapshot until fresh synchronization')
                if manifest.get('inventoryComplete') is not True:
                    raise RemoteError('replica_incomplete', 'Wait for a complete inventory synchronization')
                yield manifest
                if self.settings.identity() != identity:
                    raise RemoteError('replica_unavailable', 'Connection changed during read')
                if self._denied or (self.control / changes.DENIAL).exists():
                    raise RemoteError('root_scope_denied', 'Confirmed scope denial blocks this snapshot')
        except FileNotFoundError:
            raise RemoteError('replica_missing', 'No local snapshot exists yet') from None
        except BlockingIOError:
            raise RemoteError('replica_busy', 'A corpus writer is active; retry after synchronization') from None

    def _checkpoint(self):
        try:
            return self._read(self.control / 'state.json', 65536)
        except FileNotFoundError:
            return {}

    def _freshness(self, manifest):
        timestamp = self._checkpoint().get('bodyRefreshedAt')
        return dict(source='local_replica', lastSuccessfulSync=self.last_success or manifest.get('cursor', 'unknown'),
                    permissionScope='as_of_last_sync', bodyRefresh='shared_authoritative_sweep_per_interval',
                    lastAuthoritativeBodySync=_iso(timestamp) if type(timestamp) is int else None,
                    syncError=self._failure_message() if self.last_failure else None)

    def sync_failed(self, code='sync_failed'):
        self.last_failure = code if isinstance(code, str) and re.fullmatch('[a-z][a-z0-9_]{0,63}', code) else 'sync_failed'

    def _failure_message(self, code=None):
        return f'Automatic synchronization failed ({self.last_failure if code is None else code}); this replica may be stale'

    @staticmethod
    def _error(error):
        if isinstance(error, RemoteError):
            result = dict(status='error', code=error.code, message=str(error))
            if error.details:
                result['details'] = error.details
                if error.details.get('authorizationDenied') is True:
                    result['authorizationDenied'] = True
            return result
        return dict(status='error', code='replica_unavailable', message='Check connection_status; no verified local projection is available')

    def status(self):
        try:
            with self._locked_manifest() as manifest:
                result = dict(enabled=True, ready=True, status='failed' if self.last_failure else 'ready',
                              documents=len(manifest['pages']), freshness=self._freshness(manifest))
                watch = changes.comment_status(manifest, self.settings.watched_comment_pages)
                if watch is not None:
                    result['commentWatch'] = watch
                if self.last_failure:
                    result.update(code=self.last_failure, syncError=self._failure_message())
                return result
        except Exception as error:
            reason = error.code if isinstance(error, RemoteError) else 'replica_unavailable'
            failure = ('replica_denial_pending' if self._denial_pending else self.last_failure
                       or ('root_scope_denied' if reason == 'root_scope_denied' else None))
            status = 'busy' if reason == 'replica_busy' else 'failed' if failure else 'pending' if reason in (
                'replica_missing', 'replica_recovery_pending', 'replica_incomplete', 'credential_reauthorization_pending') else 'unavailable'
            result = dict(enabled=True, ready=False, status=status, reason='replica_scope_denied' if reason == 'root_scope_denied' else reason,
                          code=failure or reason, syncError=self._failure_message(failure) if failure else None,
                          message='No complete readable snapshot is available; check synchronization and connection configuration')
            if reason == 'root_scope_denied' and self.settings.watched_comment_pages:
                result['commentWatch'] = 'unavailable'
            return result

    def snapshot_identity(self):
        try:
            with self._locked_manifest() as manifest:
                return _hash(compact_json(manifest))
        except Exception:
            return None

    def contains_document(self, id):
        try:
            with self._locked_manifest() as manifest:
                return id in manifest['pages'] and id not in manifest.get('unavailable', [])
        except Exception:
            return False

    def request_repair(self, id):
        native_id(id)
        if len(self.pending_repair) >= 500 and id not in self.pending_repair:
            raise RemoteError('replica_repair_limit', 'Wait for the pending replica repairs')
        self.pending_repair.add(id)
        if self.on_repair is not None:
            self.on_repair()

    def _body(self, row, format, limit=BODY_LIMIT):
        raw = safe_metadata_read(self._path(row['storage' if format == 'storage' else 'path']), limit)
        text = raw.decode('utf-8') if isinstance(raw, bytes) else raw
        if format == 'storage' and _hash(text) != row['bodyHash']:
            raise RemoteError('replica_unavailable', 'Native replica hash mismatch; wait for synchronization')
        return text

    def _page_id(self, value):
        if isinstance(value, str) and changes.ID.fullmatch(value):
            return value
        return native_page_url_id(value, self.settings.site, space=self.settings.space, allow_api=True)

    def _snapshot(self, id, row, markdown):
        return _hash(':'.join((self.search_identity, id, str(row['version']), row['bodyHash'], _hash(markdown))))

    def get_document(self, data):
        locator = data.get('snapshot')
        if 'snapshot' in data and (not isinstance(locator, str) or not changes.HASH.fullmatch(locator)
            or data.get('section') is not None or data.get('format', 'markdown') != 'markdown'
            or type(data.get('version')) is not int or data['version'] <= 0):
            return self._error(RemoteError('invalid_snapshot', 'Use the search result Markdown arguments, snapshot and native version'))
        if locator is None and (data.get('section') is not None or data.get('format') == 'outline'):
            return None
        try:
            with self._locked_manifest() as manifest:
                id = self._page_id(data['document'])
                if id in manifest.get('unavailable', []):
                    raise RemoteError('document_unavailable', 'The document was unavailable at last synchronization')
                row = manifest['pages'].get(id)
                if row is None:
                    raise RemoteError('document_not_found', 'Search the configured corpus for a current document ID')
                expected = data.get('version')
                if expected is not None and (type(expected) is not int or not 1 <= expected <= 2147483647):
                    raise RemoteError('invalid_query', 'Use an exact positive native version')
                if expected is not None and expected != row['version']:
                    if locator is not None:
                        raise RemoteError('snapshot_changed', 'The document projection changed; restart search')
                    return None
                format = data.get('format', 'markdown')
                if format not in ('markdown', 'storage'):
                    raise RemoteError('invalid_query', 'Use Markdown or storage format')
                text = self._body(row, format)
                if locator is not None and locator != self._snapshot(id, row, text):
                    raise RemoteError('snapshot_changed', 'The document projection changed; restart search')
                offset, length = data.get('offset', 0), data.get('length', 4000)
                if (type(offset) is not int or type(length) is not int or not 0 <= offset <= len(text)
                    or not 1 <= length <= 12000 or (offset != 0 and expected is None)):
                    raise RemoteError('invalid_query', 'Continued reads require a version and bounded offset')
                end = min(len(text), offset + length)
                result = dict(id=id, title=row['title'], version=row['version'], format=format,
                              content=text[offset:end], offset=offset, offsetUnit='unicode_code_points',
                              truncated=end < len(text), freshness=self._freshness(manifest))
                if end < len(text):
                    result['nextOffset'] = end
                if locator is not None:
                    result['snapshot'] = locator
                return result
        except Exception as error:
            return self._error(error)

    def search(self, data):
        try:
            with self._locked_manifest() as manifest:
                return self._search(data, manifest)
        except Exception as error:
            return self._error(error)

    def _search(self, data, manifest):
        query = data['query'].strip()
        limit, snippet = data.get('limit', 10), data.get('snippetLength', 400)
        if (not 1 <= len(query) <= 200 or type(limit) is not int or not 1 <= limit <= 25
            or type(snippet) is not int or not 1 <= snippet <= 500
            or data.get('scope', 'effective') not in ('effective', 'common', 'project')):
            raise RemoteError('invalid_query', 'Use 1..200 query characters, 1..25 results and 1..500 excerpt characters')
        root = self._page_id(data['root']) if data.get('root') is not None else None
        binding = _hash('\0'.join((query, root or '', str(limit), str(snippet))))
        cursor, after = data.get('cursor'), None
        if cursor is not None:
            parts = cursor.split(':', 3)
            if (len(parts) != 4 or not changes.HASH.fullmatch(parts[0]) or parts[1] != binding
                or not re.fullmatch('[1-6]', parts[2]) or not changes.ID.fullmatch(parts[3])):
                raise RemoteError('snapshot_changed', 'Search snapshot changed; start a new search')
            after = (-int(parts[2]), parts[3])
        pages, denied, folders = manifest['pages'], set(manifest.get('unavailable', [])), manifest.get('folderParents', {})

        def inside(id):
            seen = set()
            while id is not None and id not in denied and id not in seen:
                if root is None or id == root:
                    return True
                if id == self.settings.root_page_id:
                    return False
                seen.add(id)
                id = pages.get(id, {}).get('parentId', folders.get(id))
            return False

        def hit(id, row, match, markdown):
            prefix = authored_start(markdown)
            at = prefix + match.body_offset if match.body_offset >= 0 else next((i for i in range(prefix, len(markdown)) if not markdown[i].isspace()), -1)
            result = dict(id=id, title=row['title'], version=row['version'], match=match.field)
            headings = None
            if at >= 0:
                start = at - min(at-prefix, 100, snippet // 4)
                length = min(snippet, len(markdown)-start)
                result.update(excerpt=markdown[start:start+length], excerptOnly=True,
                              read=dict(document=id, version=row['version'], format='markdown', offset=start,
                                        length=length, snapshot=self._snapshot(id, row, markdown)))
                headings = projected_headings(markdown, prefix, at)
            return ((-match.rank, id), result, headings)

        def response(hits, next=None):
            results, remaining = [], 4 * 1024 * 1024
            from .storage import native_headings, parse_storage
            for _, result, projected in hits:
                if projected:
                    try:
                        if remaining <= 0:
                            raise ValueError('Heading verification budget')
                        storage = self._body(pages[result['id']], 'storage', limit=min(BODY_LIMIT, remaining))
                        size = len(storage.encode('utf-8'))
                        if size > remaining:
                            raise ValueError('Heading verification budget')
                        remaining -= size
                        native = native_headings(storage)
                        outline = [(row['level'], row['text']) for row in native]
                        native_starts = {row['start'] for row in native}
                        containers = {'#document', '#root', 'div', 'section', 'article', 'ac:rich-text-body'}
                        transparent = all(all(a.tag in containers for a in e.ancestors())
                                          for e in parse_storage(storage).descendants() if e.start in native_starts)
                        if outline == projected[0] and transparent:
                            result['headingPath'] = projected[1]
                    except (OSError, ValueError, RemoteError):
                        pass
                results.append(result)
            result = dict(results=results, freshness=self._freshness(manifest), truncated=next is not None)
            if next is not None:
                result['nextCursor'] = next
            return result

        if changes.ID.fullmatch(query) and query in pages and inside(query):
            if cursor is not None:
                raise RemoteError('snapshot_changed', 'Search snapshot changed; start a new search')
            return response([hit(query, pages[query], Match(0, 'native_id'), self._body(pages[query], 'markdown'))])
        lexical, generation, hits, scanned_bytes, scanned_pages = Query(query), hashlib.sha256(), [], 0, 0
        for id, row in sorted(pages.items()):
            if not inside(id):
                continue
            scanned_pages += 1
            # Size is only a refusal hint. The platform reader still verifies
            # the actual file and enforces the remaining aggregate byte limit.
            size = self._path(row['path']).stat(follow_symlinks=False).st_size
            if size > BODY_LIMIT:
                raise RemoteError('replica_unavailable', 'Replica projection is oversized; wait for synchronization')
            if scanned_bytes + size > 64 * 1024 * 1024 or scanned_pages > 5000:
                raise RemoteError('search_scope_too_large', 'Narrow the root or search an exact native page ID')
            markdown = self._body(row, 'markdown', limit=min(BODY_LIMIT, 64*1024*1024-scanned_bytes))
            scanned_bytes += len(markdown.encode('utf-8'))
            projection = self._snapshot(id, row, markdown)
            generation.update(projection.encode('utf-8'))
            generation.update(compact_json(row['title']).encode('utf-8'))
            generation.update(b'\0')
            match = lexical.match(row['title'], markdown[authored_start(markdown):])
            if match is None or (after is not None and (-match.rank, id) <= after):
                continue
            candidate = hit(id, row, match, markdown)
            # Only one response and a continuation witness remain resident.
            if len(hits) <= limit or candidate[0] < hits[-1][0]:
                hits.append(candidate)
                hits.sort(key=lambda item: item[0])
                del hits[limit+1:]
        digest = generation.hexdigest()
        if cursor is not None and parts[0] != digest:
            raise RemoteError('snapshot_changed', 'Search snapshot changed; start a new search')
        continuation = None
        if len(hits) > limit:
            key = hits[limit-1][0]
            continuation = f'{digest}:{binding}:{-key[0]}:{key[1]}'
        return response(hits[:limit], continuation)

    def _root_access(self):
        if self.settings.whole_space:
            self.remote.require_space(self.settings)
        else:
            self.remote.require_scope(self.settings, self.settings.root_page_id)

    def _operation(self, timeout=30):
        return self.remote.operation(max_requests=512, max_response_bytes=32*1024*1024, timeout=timeout)

    def deny(self, timeout=3):
        """Fence this credential now; bounded durable contention is explicit.

        The existing worker owns later authorization/fencing retries. This
        method never wakes it itself, avoiding a repeated-401 request loop.
        """
        with self._state_lock:
            self._denied, self._denial_pending = True, True
            self._denial_generation += 1
            generation = self._denial_generation
        deadline = time.monotonic() + min(3, max(0, timeout))
        while True:
            try:
                with file_lock(self.control / 'notice-access.lock'):
                    current = self._manifest(missing=True)
                    if current.get('credentialBinding') == self.settings.credential_fingerprint:
                        self._write(self.control / changes.DENIAL, str(uuid.uuid4()))
                    with self._state_lock:
                        if generation == self._denial_generation:
                            self._denial_pending = False
                    return
            except BlockingIOError as error:
                remaining = deadline-time.monotonic()
                if remaining <= 0:
                    raise RemoteError('replica_denial_pending', 'Current reads are fenced; shared denial persistence is pending',
                                      details=dict(scopeFence='pending', scopeFenceCode='replica_denial_pending')) from error
                time.sleep(min(.025, remaining))
            except FileNotFoundError:
                # A cold namespace has no admitted snapshot to revoke.
                if not (self.control / 'manifest.json').exists():
                    with self._state_lock:
                        if generation == self._denial_generation:
                            self._denial_pending = False
                    return
                raise RemoteError('replica_denial_pending', 'Current reads are fenced; shared denial persistence is pending',
                                  details=dict(scopeFence='pending', scopeFenceCode='replica_denial_pending')) from None
            except (OSError, ValueError, RemoteError) as error:
                if isinstance(error, RemoteError) and error.code == 'replica_denial_pending':
                    raise
                raise RemoteError('replica_denial_pending', 'Current reads are fenced; shared denial persistence is pending',
                                  details=dict(scopeFence='pending', scopeFenceCode='replica_denial_pending')) from error

    def _denial_token(self):
        try:
            raw = safe_metadata_read(self.control / changes.DENIAL, 64)
            return raw.decode('utf-8') if isinstance(raw, bytes) else raw
        except FileNotFoundError:
            return None

    def handle_denial(self, error, timeout=3):
        """Record confirmed root loss; an ordinary child denial only triggers recheck."""
        from .remote import RootScopeDenial, is_scope_denial
        deadline = time.monotonic() + min(3, max(0, timeout))
        causes, seen = [], set()
        cause = error
        while cause is not None and id(cause) not in seen:
            seen.add(id(cause))
            causes.append(cause)
            cause = cause.__cause__
        if any(isinstance(cause, RemoteError) and cause.code == 'http_401' for cause in causes):
            if isinstance(error, RemoteError):
                error.details = dict(error.details, authorizationDenied=True)
            if self.on_authorization_denied is None:
                self.deny(timeout=max(0, deadline-time.monotonic()))
                return
            # The owner fences every runtime admission before waiting on any
            # namespace lease, and caches one result for the current operation.
            with self._state_lock:
                self._denied = True
                self._denial_generation += 1
            try:
                pending = self.on_authorization_denied()
            except Exception as failure:
                with self._state_lock:
                    self._denial_pending = True
                raise RemoteError('replica_denial_pending', 'Current reads are fenced; shared denial persistence is pending',
                                  details=dict(scopeFence='pending', scopeFenceCode='replica_denial_pending')) from failure
            if pending:
                raise RemoteError('replica_denial_pending', 'Current reads are fenced; shared denial persistence is pending',
                                  details=dict(scopeFence='pending', scopeFenceCode='replica_denial_pending'))
            return
        if any(isinstance(cause, RootScopeDenial)
               and cause.settings.identity() == self.settings.identity()
               and cause.settings.credential_fingerprint == self.settings.credential_fingerprint for cause in causes):
            self.deny(timeout=max(0, deadline-time.monotonic()))
        elif isinstance(error, RemoteError) and (is_scope_denial(error) or error.code == 'scope_metadata_changed'):
            try:
                with self._operation(timeout=max(.001, deadline-time.monotonic())):
                    self._root_access()
            except RootScopeDenial as denial:
                if denial.code == 'http_401':
                    # Keep the provider's original child error while exposing
                    # the newly observed shared authentication loss to callers.
                    error.details = dict(error.details, authorizationDenied=True)
                    self.handle_denial(denial, timeout=max(0, deadline-time.monotonic()))
                else:
                    self.deny(timeout=max(0, deadline-time.monotonic()))
            except RemoteError:
                pass

    def synchronize(self, authoritative_bodies=True):
        if self.closed:
            return
        ensure_private_directory(self.settings.corpus_root)
        if not self.control.exists():
            ensure_private_directory(self.control)
        with trusted_metadata_directory(self.control):
            pass
        deadline = time.monotonic() + 30
        try:
            with self._operation():
                with file_lock(self.control / 'sweep.lock'):
                    result = self._synchronize(authoritative_bodies)
                self.poll_comments(_deadline=deadline)
                self.last_success, self.last_failure = _now(), None
        except BlockingIOError:
            return
        except Exception as error:
            self.sync_failed(getattr(error, 'code', 'sync_failed'))
            if isinstance(error, RemoteError):
                try:
                    self.handle_denial(error, timeout=max(0, min(3, deadline-time.monotonic())))
                except RemoteError as pending:
                    if pending.code != 'replica_denial_pending':
                        raise
                    error.details = dict(error.details, scopeFence='pending', scopeFenceCode=pending.code)
            raise
        return result

    def _synchronize(self, authoritative_bodies):
        identity, started = self.settings.identity(), int(time.time() * 1000)
        with self._state_lock:
            denial_generation = self._denial_generation
        with file_lock(self.control / 'sync.lock'):
            with file_lock(self.control / 'notice-access.lock'):
                prior_denial = self._denial_token()
            self._root_access()
            with file_lock(self.control / 'notice-access.lock'):
                self._recover(allow_credential_switch=True)
            previous = self._manifest(missing=True)
            old = previous.get('pages', {})
            reauthorizing = bool(old) and previous.get('credentialBinding') != self.settings.credential_fingerprint
            timestamp = self._checkpoint().get('bodyRefreshedAt')
            due = type(timestamp) is not int or timestamp < 0 or timestamp > started or started-timestamp >= self.settings.poll_interval_seconds*1000
            full_pass = bool(authoritative_bodies and due and not reauthorizing)
            requested = set(self.pending_repair)
            nodes, unavailable = {}, set()
            inventory = self.remote.inventory(self.settings)
            if len(inventory) > PAGE_LIMIT or len({n.id for n in inventory}) != len(inventory):
                raise RemoteError('invalid_inventory', 'Native inventory is duplicate or oversized')
            for node in inventory:
                self._validate_native(node)
                nodes[node.id] = node
            for id, row in old.items():
                if id in nodes:
                    continue
                try:
                    node = self.remote.require_scope(self.settings, id)
                    self._validate_native(node)
                    nodes[id] = node
                except RemoteError as error:
                    from .remote import RootScopeDenial
                    if isinstance(error, RootScopeDenial):
                        raise
                    if error.code not in ('scope_outside_connection', 'scope_not_current'):
                        unavailable.add(id)
            if not self.settings.whole_space:
                root = self.remote.require_scope(self.settings, self.settings.root_page_id)
                self._validate_native(root)
                nodes[root.id] = root
                # Resolve folder parents so a page can retain its real native hierarchy.
                for node in list(nodes.values()):
                    parent, seen = node.parent_id if node.id != root.id else None, {node.id}
                    while parent is not None and parent != root.id:
                        if parent in seen or len(seen) >= 100:
                            raise RemoteError('invalid_inventory', 'Native ancestry is cyclic or too deep')
                        seen.add(parent)
                        if parent not in nodes:
                            try:
                                ancestor = self.remote.scope_node(parent, 'folder')
                            except RemoteError as error:
                                if error.code != 'http_404':
                                    raise
                                ancestor = self.remote.scope_node(parent)
                            self._validate_native(ancestor)
                            nodes[parent] = ancestor
                        parent = nodes[parent].parent_id

            def chain(node):
                if self.settings.whole_space:
                    return [node]
                result, seen = [], set()
                while node is not None:
                    if node.id in seen or len(seen) >= 100:
                        raise RemoteError('invalid_inventory', 'Native ancestry is cyclic or too deep')
                    seen.add(node.id)
                    result.insert(0, node)
                    if node.id == self.settings.root_page_id:
                        return result
                    node = nodes.get(node.parent_id)
                return None

            selected = {id: n for id, n in nodes.items() if n.type == 'page' and n.status == 'current'
                        and chain(n) is not None and id not in unavailable}
            paths = self._paths(nodes, selected, chain, {i: old[i] for i in unavailable})
            stage = self.control / ('stage-' + str(uuid.uuid4()))
            ensure_private_directory(stage)
            next_pages, changed, bytes_written = {}, [], 0
            try:
                for id, node in sorted(selected.items()):
                    if self.remote.require_scope(self.settings, id) != node:
                        raise RemoteError('scope_metadata_changed', 'Native metadata changed during synchronization')
                    prior = old.get(id)
                    refresh = id in requested or (full_pass and id in old)
                    cached = None
                    if not refresh and prior is not None and prior['version'] == node.version:
                        try:
                            cached = self._body(prior, 'storage')
                        except (OSError, ValueError, RemoteError):
                            pass
                    page = Page(node, cached) if cached is not None else self.remote.page(id, expected_node=node, scope=self.settings, fresh_storage=refresh)
                    if page.node != node:
                        raise RemoteError('scope_metadata_changed', 'Native page changed during synchronization')
                    bytes_written += len(page.body.encode('utf-8'))
                    if bytes_written > 64*1024*1024 or len(page.body.encode('utf-8')) > 4*1024*1024:
                        raise RemoteError('replica_body_limit', 'Replica body publication exceeds its bounded operation budget')
                    row = self._stage_page(stage, page, paths[id])
                    next_pages[id] = row
                    if prior is not None and any(prior.get(k) != row.get(k) for k in ('version', 'path', 'bodyHash')):
                        changed.append(dict(pageId=id, oldVersion=prior['version'], version=node.version, title=node.title,
                                            path=row['path'], structural=prior['path'] != row['path']))
                for id in sorted(unavailable):
                    next_pages[id] = old[id]
                    self._write(self._path(old[id]['path'], stage), self._body(old[id], 'markdown'))
                _owners(next_pages)
                folders = {id: n.parent_id for id, n in nodes.items()
                           if n.type == 'folder' and n.parent_id is not None and id != self.settings.root_page_id}
                manifest = dict(schemaVersion=1, site=self.settings.site, spaceId=self.settings.space_id,
                                homePageId=self.settings.root_page_id or '', wholeSpace=self.settings.whole_space,
                                structurePolicy='knowledge')
                if self.settings.root_page_id is not None:
                    manifest['knowledgeRootPageId'] = self.settings.root_page_id
                manifest.update(explicitPages=[i for i in previous.get('explicitPages', []) if i in next_pages],
                                inventoryComplete=True, credentialBinding=self.settings.credential_fingerprint,
                                cursor=previous['cursor'] if unavailable and 'cursor' in previous else _now(),
                                pages=next_pages, unavailable=sorted(unavailable), folderParents=folders, changes=changed)
                manifest = changes.documents(previous, manifest)
                self._validate(manifest)
                if self.settings.identity() != identity:
                    raise RemoteError('connection_changed', 'Connection changed during synchronization')
                self._root_access()
                self._metadata(stage / 'manifest.json', manifest)
                self._check_projection_budget(stage, manifest['pages'])
                self._metadata(self.control / 'transaction.json', dict(stage=stage.name))
                with file_lock(self.control / 'notice-access.lock'):
                    self._recover(allow_credential_switch=True)
                    if (previous.get('credentialBinding') != self.settings.credential_fingerprint
                        or (prior_denial is not None and self._denial_token() == prior_denial)):
                        (self.control / changes.DENIAL).unlink(missing_ok=True)
                    with self._state_lock:
                        if denial_generation == self._denial_generation and not (self.control / changes.DENIAL).exists():
                            self._denied, self._denial_pending = False, False
                if full_pass:
                    state = self._checkpoint()
                    state['bodyRefreshedAt'] = started
                    self._metadata(self.control / 'state.json', state)
                self.pending_repair.difference_update(requested - unavailable)
                return dict(status='synced_with_unavailable' if unavailable else 'synced', pages=len(next_pages),
                            changes=changed, unavailable=sorted(unavailable), manifest=str(self.control / 'manifest.json'))
            finally:
                if not (self.control / 'transaction.json').exists():
                    self._discard_stage(stage, list(paths.values()) + [old[i]['path'] for i in unavailable])

    def _validate_native(self, node):
        native_id(node.id)
        if (node.space_id != self.settings.space_id or node.type not in ('page', 'folder')
            or not isinstance(node.title, str) or len(node.title) > 1000
            or type(node.version) is not int or not 0 <= node.version <= 2147483647):
            raise RemoteError('invalid_inventory', 'Cross-space or unsupported native inventory entry')

    def _paths(self, nodes, selected, chain, retained):
        # Reserve both file and subtree names, including names of inaccessible retained pages.
        retained_files, retained_directories = _owners(retained) if retained else ({}, {})
        segments = {}
        groups = {}
        for node in nodes.values():
            groups.setdefault(None if self.settings.whole_space else node.parent_id, []).append(node)
        ordered_groups = sorted(groups.values(), key=lambda rows: len(chain(rows[0]) or [None]*101))
        for siblings in ordered_groups:
            occupied = set()
            for node in sorted(siblings, key=lambda n: n.id):
                ancestry = chain(node)
                if ancestry is None:
                    continue
                parent = '/'.join(segments[n.id] for n in ancestry[:-1])
                original = _segment(node.title) + ('~'+node.id if self.settings.whole_space else '')
                candidate = original
                for attempt in range(101):
                    directory = _canonical('pages/' + (parent+'/' if parent else '') + candidate)
                    file = directory + '.md'
                    names = {_canonical(candidate), _canonical(candidate+'.md')}
                    collision = (bool(names & occupied) or directory in retained_files or file in retained_directories
                                 or (directory in retained_directories and retained_directories[directory] != node.id)
                                 or (file in retained_files and retained_files[file] != node.id))
                    if not collision:
                        break
                    candidate = original+'~'+node.id+('-'+str(attempt+1) if attempt else '')
                else:
                    raise RemoteError('replica_path_collision', 'Unresolvable replica filename collision')
                if len(candidate.encode('utf-8')) > 240:
                    raise RemoteError('replica_path_limit', 'Replica filename exceeds its byte budget')
                occupied.update(names)
                segments[node.id] = candidate
        return {id: 'pages/'+'/'.join(segments[n.id] for n in chain(node))+'.md' for id, node in selected.items()}

    def _stage_page(self, stage, page, path):
        from .markdown import to_markdown
        node, hash = page.node, _hash(page.body)
        storage = f'.sync/storage/{node.id}-{node.version}-{hash}.xhtml'
        self._write(self._path(storage), page.body)
        markdown = (f'---\npage_id: "{node.id}"\nversion: {node.version}\n'
                    f'url: {self.settings.site}/wiki/spaces/{self.settings.space}/pages/{node.id}\n---\n\n'
                    + to_markdown(page.body))
        if len(markdown.encode('utf-8')) > BODY_LIMIT:
            raise RemoteError('replica_body_limit', 'Markdown projection exceeds its byte budget')
        self._write(self._path(path, stage), markdown)
        return dict(_row(node, path, storage, hash), relations=knowledge_relations(page.body, self.settings.site, self.settings.space))

    def _discard_stage(self, stage, relative_paths=()):
        # Delete only proven projection names, never recursively walk an untrusted
        # or replaced tree. Unknown contents remain recoverable and untouched.
        if not stage.exists():
            return
        with trusted_metadata_directory(stage):
            identity = stage.stat(follow_symlinks=False)
            def unchanged():
                current = stage.stat(follow_symlinks=False)
                if (current.st_dev, current.st_ino) != (identity.st_dev, identity.st_ino):
                    raise ValueError('Stage directory identity changed')
            names = set(relative_paths)
            try:
                manifest = self._read(stage / 'manifest.json')
                self._validate(manifest)
                names.update(r['path'] for r in manifest['pages'].values())
            except FileNotFoundError:
                pass
            if len(names) > PAGE_LIMIT:
                raise ValueError('Stage cleanup exceeds its bound')
            directories = set()
            for relative in names:
                path = self._path(relative, stage)
                try:
                    safe_metadata_read(path, BODY_LIMIT)
                except FileNotFoundError:
                    continue
                unchanged()
                path.unlink()
                parent = path.parent
                while parent != stage:
                    directories.add(parent)
                    parent = parent.parent
            try:
                safe_metadata_read(stage / 'manifest.json', MANIFEST_LIMIT)
            except FileNotFoundError:
                pass
            else:
                unchanged()
                (stage / 'manifest.json').unlink()
            for directory in sorted(directories, key=lambda p: len(p.parts), reverse=True):
                with trusted_metadata_directory(directory):
                    unchanged()
                try:
                    directory.rmdir()
                except OSError:
                    pass
            unchanged()
        try:
            stage.rmdir()
        except OSError:
            pass

    def _check_projection_budget(self, stage, pages, files=None):
        """Reject a deterministically unreadable generation before journaling."""
        workset = None if files is None else set(files)
        remaining = 64*1024*1024
        for row in pages.values():
            relative = row['path']
            source = self._path(relative, stage) if workset is None or relative in workset else self._path(relative)
            size = source.stat(follow_symlinks=False).st_size
            if size > BODY_LIMIT or size > remaining:
                raise RemoteError('replica_body_limit', 'Candidate projections exceed the readable generation byte budget')
            raw = safe_metadata_read(source, min(BODY_LIMIT, remaining))
            remaining -= len(raw)

    def _recover(self, allow_credential_switch=False):
        journal_path = self.control / 'transaction.json'
        if not journal_path.exists():
            return
        transaction = self._read(journal_path)
        name = transaction['stage']
        if not isinstance(name, str) or not re.fullmatch('stage-[a-f0-9-]{1,64}', name):
            raise RemoteError('invalid_mirror_snapshot', 'Invalid mirror recovery state')
        stage = self.control / name
        previous, next = self._manifest(missing=True), self._read(stage / 'manifest.json')
        self._validate(next)
        if not allow_credential_switch:
            if any(m.get('credentialBinding') != self.settings.credential_fingerprint for m in (previous, next)):
                raise RemoteError('credential_reauthorization_pending', 'Complete inventory admission is required')
        elif 'files' in transaction and previous.get('credentialBinding') != next.get('credentialBinding'):
            raise RemoteError('invalid_mirror_snapshot', 'Partial transaction cannot change credential admission')
        pages, prior = next['pages'], previous.get('pages', {})
        desired = {r['path'] for r in pages.values()}
        files = transaction.get('files', list(desired))
        if (not isinstance(files, list) or len(set(files)) != len(files) or not set(files) <= desired
            or ('files' in transaction and (not set(prior) <= set(pages)
                or any(r != prior.get(i) and r['path'] not in files for i, r in pages.items())))):
            raise RemoteError('invalid_mirror_snapshot', 'Invalid partial mirror transaction')
        for id in next.get('unavailable', []):
            if prior.get(id) != pages[id]:
                raise RemoteError('invalid_mirror_snapshot', 'Unavailable page differs from retained checkpoint')
        # Preflight the complete workset before touching the readable generation.
        owned = {r['path'] for r in prior.values()}
        owned_canonical = {_canonical(p) for p in owned}
        desired_canonical = {_canonical(p) for p in desired}
        _, prior_directories = _owners(prior)
        _, desired_directories = _owners(pages)
        obsolete_files = owned_canonical-desired_canonical
        total, entry_count, directory_entries = 0, 0, {}
        for relative in desired:
            _relative(relative)
            source = self._path(relative, stage) if relative in files else self._path(relative)
            raw = safe_metadata_read(source, BODY_LIMIT)
            total += len(raw.encode('utf-8')) if isinstance(raw, str) else len(raw)
            if total > 64*1024*1024:
                raise RemoteError('replica_body_limit', 'Staged projections exceed the publication budget')
            destination = self._path(relative)
            permitted_existing = False
            if _canonical(relative) not in owned_canonical and destination.exists():
                if (_canonical(relative) in prior_directories and _canonical(relative) not in desired_directories
                    and destination.is_dir()):
                    # A proven former subtree becomes a file after its obsolete
                    # owned children are removed. Unknown contents are never removed.
                    with trusted_metadata_directory(destination):
                        pass
                    permitted_existing = True
                elif relative in files and safe_metadata_read(destination, BODY_LIMIT) == raw:
                    # Recovery can resume after Markdown reached disk but before
                    # the manifest did. Only byte-identical staged content is replayable.
                    permitted_existing = True
                else:
                    raise RemoteError('replica_path_collision', 'Unowned file obstructs mirror publication')
            if destination.parent.exists():
                parent_relative = _canonical(str(PurePosixPath(relative).parent))
                if parent_relative in obsolete_files and destination.parent.is_file():
                    # A proven obsolete Markdown file will become this subtree.
                    # Verify it through the platform reader before deferring creation.
                    safe_metadata_read(destination.parent, BODY_LIMIT)
                    continue
                # Detect the same obstruction on case-sensitive hosts before exporting
                # a cache that would collide on native Windows.
                with trusted_metadata_directory(destination.parent):
                    pass
                if destination.parent not in directory_entries:
                    names = set()
                    for sibling in destination.parent.iterdir():
                        entry_count += 1
                        if entry_count > 4*PAGE_LIMIT:
                            raise RemoteError('replica_path_limit', 'Mirror directories exceed their entry budget')
                        names.add(_canonical(sibling.name))
                    directory_entries[destination.parent] = names
                if (_canonical(destination.name) in directory_entries[destination.parent]
                    and _canonical(relative) not in owned_canonical and not permitted_existing):
                    raise RemoteError('replica_path_collision', 'Unowned file obstructs mirror publication')
        for row in prior.values():
            if row['path'] not in desired:
                obsolete = self._path(row['path'])
                if _canonical(row['path']) in desired_directories and obsolete.is_dir():
                    # An interrupted replay may already have replaced this old
                    # file with its desired directory. Keep that new subtree.
                    with trusted_metadata_directory(obsolete):
                        pass
                    continue
                if obsolete.exists():
                    safe_metadata_read(obsolete, BODY_LIMIT)
                    obsolete.unlink()
                parent = obsolete.parent
                while parent != self.base / 'pages':
                    try:
                        parent.rmdir()
                    except (OSError, ValueError):
                        break
                    parent = parent.parent
        for relative in files:
            self._write(self._path(relative), safe_metadata_read(self._path(relative, stage), BODY_LIMIT))
        self._metadata(self.control / 'manifest.json', next)
        state = dict(cursor=next['cursor'], status='synced', credentialBinding=next.get('credentialBinding'))
        prior_state = self._checkpoint()
        stamp = prior_state.get('bodyRefreshedAt')
        if type(stamp) is int and 0 <= stamp <= 9223372036854775807:
            state['bodyRefreshedAt'] = stamp
        self._metadata(self.control / 'state.json', state)
        feed = compact_json(next[changes.NOTICES])
        if len(feed.encode('utf-8')) > 65536:
            raise RemoteError('replica_feed_limit', 'Notification feed exceeds its byte budget')
        self._write(self.control / 'changes.json', feed)
        journal_path.unlink()
        self._discard_stage(stage)

    def publish_verified(self, page, before=None):
        """Publish one verified remote effect; this function never mutates the provider."""
        if not (self.control / 'manifest.json').exists():
            return
        identity = self.settings.identity()
        node = page.node
        self._validate_native(node)
        if node.type != 'page' or node.status != 'current' or node.version <= 0 or len(page.body.encode('utf-8')) > 4*1024*1024:
            raise RemoteError('invalid_request', 'A verified current native page is required')
        with file_lock(self.control / 'sync.lock'):
            previous = self._manifest()
            if previous.get('credentialBinding') != self.settings.credential_fingerprint:
                raise RemoteError('credential_reauthorization_pending', 'Complete inventory admission is required')
            if self._denied or (self.control / changes.DENIAL).exists():
                raise RemoteError('root_scope_denied', 'A fresh inventory is required after confirmed denial')
            with file_lock(self.control / 'notice-access.lock'):
                self._recover()
            previous = self._manifest()
            old, prior = previous['pages'], previous['pages'].get(node.id)
            if prior is not None and prior['version'] > node.version:
                return
            hash = _hash(page.body)
            if prior is not None and prior['version'] == node.version:
                transition = before is not None and before.node == node and _hash(before.body) == prior['bodyHash']
                if _node(prior) != node or (prior['bodyHash'] != hash and not transition):
                    raise RemoteError('replica_version_conflict', 'Same-version mirror content differs from the verified write')
            if self.settings.whole_space:
                path = 'pages/'+_segment(node.title)+'~'+node.id+'.md'
            elif prior is not None and prior['title'] == node.title and prior.get('parentId') == node.parent_id:
                path = prior['path']
            else:
                if prior is not None and prior.get('parentId') == node.parent_id:
                    parent = prior['path'].rsplit('/', 1)[0]
                elif node.parent_id in old:
                    parent = old[node.parent_id]['path'][:-3]
                elif node.id == self.settings.root_page_id:
                    parent = 'pages'
                else:
                    raise RemoteError('replica_parent_missing', 'Synchronize the verified native parent structure first')
                old_directory = prior['path'][:-3] if prior else None
                if old_directory is not None and (parent == old_directory or parent.startswith(old_directory+'/')):
                    raise RemoteError('replica_path_collision', 'Cyclic mirror relocation')
                path = parent+'/'+_segment(node.title)+'.md'
            old_directory = prior['path'][:-3] if prior else None
            descendants = {id: row for id, row in old.items() if old_directory and row['path'].startswith(old_directory+'/')}
            unavailable = set(previous.get('unavailable', [])) - {node.id}
            if unavailable & set(descendants) and prior['path'] != path:
                raise RemoteError('document_unavailable', 'Unavailable descendants require a full synchronization before relocation')

            def entries(candidate):
                rows = dict(old)
                for id, row in descendants.items():
                    rows[id] = dict(row, path=candidate[:-3]+row['path'][len(old_directory):])
                rows[node.id] = _row(node, candidate, f'.sync/storage/{node.id}-{node.version}-{hash}.xhtml', hash)
                return rows

            original = path[:-3]
            for attempt in range(101):
                rows = entries(path)
                try:
                    _owners(rows)
                    break
                except ValueError:
                    path = original+'~'+node.id+('-'+str(attempt+1) if attempt else '')+'.md'
            else:
                raise RemoteError('replica_path_collision', 'Unresolvable mirror filename collision')
            changed = {id for id, row in rows.items() if row != old.get(id)} | {node.id}
            pinned = list(previous.get('explicitPages', []))
            if prior is None:
                pinned.append(node.id)
            if len(pinned) > 500:
                raise RemoteError('replica_selection_limit', 'Explicit mirror selection exceeds the page limit')
            stage = self.control / ('stage-'+str(uuid.uuid4()))
            ensure_private_directory(stage)
            try:
                rows[node.id] = self._stage_page(stage, page, path)
                for id in changed - {node.id}:
                    self._write(self._path(rows[id]['path'], stage), self._body(old[id], 'markdown'))
                records = [dict(pageId=id, **({'oldVersion': old[id]['version']} if id in old else {}),
                                version=rows[id]['version'], title=rows[id]['title'], path=rows[id]['path'],
                                structural=old.get(id, {}).get('path') != rows[id]['path']) for id in sorted(changed)]
                next = changes.documents(previous, dict(previous, pages=rows, explicitPages=pinned,
                                                        unavailable=sorted(unavailable), changes=records))
                self._validate(next)
                if self.settings.identity() != identity:
                    raise RemoteError('connection_changed', 'Connection changed during publication')
                self._metadata(stage / 'manifest.json', next)
                files = [rows[i]['path'] for i in sorted(changed)]
                self._check_projection_budget(stage, next['pages'], files)
                self._metadata(self.control / 'transaction.json', dict(stage=stage.name, files=files))
                with file_lock(self.control / 'notice-access.lock'):
                    if (self.control / changes.DENIAL).exists():
                        raise RemoteError('root_scope_denied', 'A current inventory is required before publication')
                    self._recover()
            finally:
                if not (self.control / 'transaction.json').exists():
                    self._discard_stage(stage, [rows[i]['path'] for i in changed])

    def poll_comments(self, page_ids=None, interval_seconds=None, *, _deadline=None):
        targets = self.settings.watched_comment_pages if page_ids is None else page_ids
        if not targets or self.closed:
            return
        if len(targets) > 10 or len(set(targets)) != len(targets) or not all(changes.ID.fullmatch(i) for i in targets):
            raise RemoteError('invalid_request', 'Watch up to ten distinct native pages')
        interval = self.settings.poll_interval_seconds if interval_seconds is None else interval_seconds
        try:
            with file_lock(self.control / 'comments.lock'):
                self._poll_comments(targets, interval, _deadline)
        except (BlockingIOError, FileNotFoundError):
            return

    def _poll_comments(self, targets, interval, deadline=None):
        started = int(time.time()*1000)
        deadline = time.monotonic() + 30 if deadline is None else deadline
        try:
            with self._locked_manifest() as manifest:
                admitted = changes.admitted(manifest)
                watched = {i: admitted[i] for i in sorted(targets, key=lambda id: (changes.comment_attempted_at(manifest, id) or -1, id))
                           if i in admitted and changes.comment_due(manifest, i, started, interval*1000)}
        except RemoteError:
            return
        attempted, observed = [], {}
        with self._operation(timeout=max(.001, deadline-time.monotonic())):
            for id, row in watched.items():
                page_deadline = min(deadline, time.monotonic()+8)
                try:
                    with self._operation(timeout=max(.001, page_deadline-time.monotonic())):
                        expected = _node(row)
                        if self.remote.require_scope(self.settings, id) != expected:
                            raise RemoteError('scope_metadata_changed', 'Comment target metadata changed')
                        inventory = self.remote.comment_inventory(id, expected_node=expected, scope=self.settings)
                        if self.remote.require_scope(self.settings, id) != expected:
                            raise RemoteError('scope_metadata_changed', 'Comment target metadata changed')
                        observed[id] = changes.comment_fingerprint(inventory)
                except RemoteError as error:
                    from .remote import RootScopeDenial, is_scope_denial
                    if error.code == 'http_401':
                        try:
                            self.handle_denial(error, timeout=max(0, deadline-time.monotonic()))
                        except RemoteError as pending:
                            error.details = dict(error.details, scopeFence='pending', scopeFenceCode=pending.code)
                        raise error
                    if error.code in ('wiki_request_limit', 'wiki_body_limit', 'wiki_timeout'):
                        if time.monotonic() >= page_deadline and page_deadline < deadline and time.monotonic() < deadline:
                            # A per-page timeout is an unavailable observation,
                            # not exhaustion of the shared request/scan budget.
                            attempted.append(id)
                            continue
                        if not attempted:
                            attempted.append(id)
                        break
                    if isinstance(error, RootScopeDenial):
                        try:
                            self.handle_denial(error, timeout=max(0, deadline-time.monotonic()))
                        except RemoteError as pending:
                            error.details = dict(error.details, scopeFence='pending', scopeFenceCode=pending.code)
                            raise error
                        return
                    if is_scope_denial(error) or error.code == 'scope_metadata_changed':
                        try:
                            self._root_access()
                        except RootScopeDenial as denial:
                            try:
                                self.handle_denial(denial, timeout=max(0, deadline-time.monotonic()))
                            except RemoteError as pending:
                                denial.details = dict(denial.details, scopeFence='pending', scopeFenceCode=pending.code)
                                raise denial
                            if denial.code == 'http_401':
                                raise denial
                            return
                        except RemoteError:
                            return
                except (ValueError, KeyError, TypeError):
                    pass
                attempted.append(id)
        if not attempted:
            return
        try:
            with file_lock(self.control / 'sync.lock'):
                current = self._manifest()
                if self._denied or (self.control / 'transaction.json').exists() or (self.control / changes.DENIAL).exists() or current.get('credentialBinding') != self.settings.credential_fingerprint:
                    return
                pages = changes.admitted(current)
                confirmed = {i: h for i, h in observed.items() if pages.get(i) == watched[i]}
                next = changes.comments(current, started, attempted, confirmed)
                stage = self.control / ('stage-'+str(uuid.uuid4()))
                ensure_private_directory(stage)
                try:
                    self._metadata(stage / 'manifest.json', next)
                    self._check_projection_budget(stage, next['pages'], [])
                    self._metadata(self.control / 'transaction.json', dict(stage=stage.name, files=[]))
                    with file_lock(self.control / 'notice-access.lock'):
                        if (self.control / changes.DENIAL).exists():
                            return
                        self._recover()
                finally:
                    if not (self.control / 'transaction.json').exists():
                        self._discard_stage(stage)
        except BlockingIOError:
            return

    def relations(self, data):
        try:
            with self._operation(timeout=45):
                try:
                    with self._locked_manifest() as manifest:
                        return self._relations(data, manifest)
                except RemoteError as error:
                    if error.code != 'replica_missing':
                        raise
                    depth, limit = data.get('depth', 2), data.get('limit', 25)
                    if type(depth) is not int or type(limit) is not int or not 1 <= depth <= 5 or not 1 <= limit <= 25:
                        raise RemoteError('invalid_request', 'Graph requires depth 1..5 and limit 1..25')
                    node = self.remote.require_scope(self.settings, self._page_id(data['document']))
                    if node.type != 'page':
                        raise RemoteError('invalid_request', 'A native page is required')
                    self._root_access()
                    return dict(document=node.id, nodes=[dict(id=node.id, title=node.title, version=node.version)],
                                relations=[], affectedCandidates=[], syncRequired=True, truncated=False, complete=False,
                                limitations=['One synchronized scope and bounded explicit relations; not global logical closure.',
                                             'Affected candidates depend on the starting document; verify premises, dates and deployment evidence before editing.'])
        except Exception as error:
            if isinstance(error, RemoteError):
                try:
                    self.handle_denial(error)
                except RemoteError as pending:
                    if pending.code != 'replica_denial_pending':
                        raise
                    error.details = dict(error.details, scopeFence='pending', scopeFenceCode=pending.code)
            return self._error(error)

    def _relations(self, data, manifest):
        start, depth, limit = self._page_id(data['document']), data.get('depth', 2), data.get('limit', 25)
        if type(depth) is not int or type(limit) is not int or not 1 <= depth <= 5 or not 1 <= limit <= 25:
            raise RemoteError('invalid_request', 'Graph requires depth 1..5 and limit 1..25')
        start_node = self.remote.require_scope(self.settings, start)
        if start_node.type != 'page':
            raise RemoteError('invalid_request', 'A native page is required')
        pages, unavailable = manifest['pages'], set(manifest.get('unavailable', []))
        sync_required, truncated, edges = start not in pages or bool(unavailable), False, []
        titles = {}
        for id, row in pages.items():
            titles.setdefault(row['title'], []).append(id)
        for id, row in pages.items():
            if id in unavailable:
                continue
            relations = row.get('relations')
            if relations is None:
                sync_required = True
                continue
            if relations.get('complete') is not True:
                truncated = True
            for item in relations['items']:
                if len(edges) >= 50_000:
                    truncated = True
                    break
                candidates = titles.get(item.get('title'), [])
                target = item.get('to') or (candidates[0] if len(candidates) == 1 else None)
                if target not in pages or target in unavailable:
                    truncated = True
                    continue
                edge = dict(from_=id, to=target, type=item['type'], reason=item['reason'])
                edge['from'] = edge.pop('from_')
                if 'fragment' in item:
                    edge['fragment'] = item['fragment']
                edges.append(edge)
        by_source, by_target = {}, {}
        for edge in edges:
            by_source.setdefault(edge['from'], []).append(edge)
            by_target.setdefault(edge['to'], []).append(edge)
        current, visible, attempts, scanned_bytes = {}, {start: start_node}, 0, 0

        def verified(id):
            nonlocal attempts, scanned_bytes, sync_required, truncated
            if id in current:
                return current[id]
            current[id] = False
            if id not in pages or id in unavailable:
                sync_required = True
                return False
            attempts += 1
            if attempts > 40:
                truncated = True
                return False
            try:
                node = self.remote.require_scope(self.settings, id)
                page = self.remote.page(id, expected_node=node, scope=self.settings, fresh_storage=True)
                scanned_bytes += len(page.body.encode('utf-8'))
                if scanned_bytes > 16*1024*1024:
                    truncated = True
                    return False
                row = pages[id]
                if _node(row) != node or _hash(page.body) != row['bodyHash'] or row.get('relations') != knowledge_relations(page.body, self.settings.site, self.settings.space):
                    sync_required = True
                    return False
                visible[id], current[id] = node, True
                return True
            except RemoteError as error:
                from .remote import RootScopeDenial
                if isinstance(error, RootScopeDenial) or error.code not in ('http_403', 'http_404', 'scope_outside_connection'):
                    raise
                truncated = True
                return False

        selected, selected_edges, queue = [start], [], deque([(start, 0)])
        while queue:
            id, hop = queue.popleft()
            fresh = verified(id)
            if not fresh and id != start:
                continue
            adjacent = (by_source.get(id, []) if fresh else []) + by_target.get(id, [])
            if hop == depth:
                truncated |= any(e['from'] not in selected or e['to'] not in selected for e in adjacent)
                continue
            for edge in adjacent:
                if len(selected_edges) >= 100 or attempts >= 40:
                    truncated = True
                    break
                next = edge['to'] if edge['from'] == id else edge['from']
                if next not in selected and len(selected) >= limit:
                    truncated = True
                    continue
                if not verified(edge['from']) or not verified(edge['to']):
                    continue
                if edge not in selected_edges:
                    selected_edges.append(edge)
                if next not in selected:
                    selected.append(next)
                    queue.append((next, hop+1))
        affected, frontier = [], {start}
        while frontier:
            next = {edge['from'] for edge in selected_edges if edge['to'] in frontier
                    and edge['type'] in ('evidence', 'depends_on') and edge['from'] != start and edge['from'] not in affected}
            affected.extend(sorted(next))
            frontier = next
        for id, node in visible.items():
            if self.remote.require_scope(self.settings, id) != node:
                raise RemoteError('scope_metadata_changed', 'A consumed native graph endpoint changed before return')
        self._root_access()
        return dict(document=start, nodes=[dict(id=i, title=visible[i].title, version=visible[i].version) for i in selected if i in visible],
                    relations=[dict(e, version=visible[e['from']].version) for e in selected_edges], affectedCandidates=affected,
                    syncRequired=sync_required, truncated=truncated, complete=False,
                    limitations=['One synchronized scope and bounded explicit relations; not global logical closure.',
                                 'Affected candidates depend on the starting document; verify premises, dates and deployment evidence before editing.'])
