"""The explicit, strict public MCP surface; platform selection adds no tools."""
from __future__ import annotations

from .models import RemoteError


def field(kind, description='', default=None):
    value = {'type': kind, 'description': description}
    if default is not None:
        value['default'] = default
    if kind == 'array':
        value['items'] = {}
    return value


S, I, B, A, O = 'string', 'integer', 'boolean', 'array', 'object'
SPACE = {'space': field(S, 'Exact configured space key; never guess an ambiguous write destination')}
META = {'metadata': field(B, 'Include detailed native metadata', False)}
BODY = {**META, 'includeBody': field(B, 'Include bounded saved native content', False)}


def tool(name, description, properties, required=()):
    schema = {'name': name, 'description': description, 'inputSchema': {
        'type': 'object', 'properties': {**properties, 'connection': {
            'type': 'string', 'enum': ['project', 'personal'],
            'description': 'Project is the default. Select personal explicitly for the independently configured private page; no credential fallback.'}},
        'required': list(required), 'additionalProperties': False}}
    return schema


WIKI_TOOLS = [
    tool('connection_status', 'Show readiness, scope and synchronization errors for each configured space. Partial readiness is not complete coverage.', {}),
    tool('search_documents', 'Search the shared local replica by native ID or ranked text; returned read locators bind the actual source snapshot.', {
        'query': field(S), 'scope': field(S, default='effective'), 'limit': field(I, default=10),
        'cursor': field(S), **META, 'root': field(S), 'snippetLength': field(I), **SPACE}, ['query']),
    tool('document_relations', 'Read explicit authored relations and affected review candidates; links do not establish truth.', {
        'document': field(S), 'depth': field(I, default=2), 'limit': field(I, default=25), **SPACE}, ['document']),
    tool('get_document', 'Read Markdown, native storage, or version-local outline. Keep version/section/format/snapshot on continued reads.', {
        'document': field(S), 'version': field(I), 'offset': field(I, default=0), 'length': field(I, default=4000),
        'format': field(S, default='markdown'), **META, 'section': field(S), 'snapshot': field(S), **SPACE}, ['document']),
    tool('create_document', 'Create under a native parent or explicit space, one mutation with verified native readback.', {
        'parent': field(S), 'title': field(S), 'content': field(S), 'format': field(S), **BODY,
        'basedOn': field(A), **SPACE}, ['title', 'content']),
    tool('update_document', 'Use one exact versioned body edit or a pure append. Preserve rich references and never repeat unknown writes.', {
        **{name: field(S) for name in ('document', 'content', 'title', 'old_text', 'new_text', 'append', 'format', 'parent', 'section')},
        'version': field(I), **BODY, 'edits': field(A), 'allowReferenceRemoval': field(B, default=False),
        'basedOn': field(A), 'links': field(O), **SPACE}),
    tool('list_comments', 'Read native footer/inline comments and bounded replies; returned references preserve thread identity.', {
        'target': field(S), 'limit': field(I, default=20), 'cursor': field(S), 'format': field(S), **META,
        'includeReplies': field(B, default=False), 'repliesPerComment': field(I, default=3), **SPACE}, ['target']),
    tool('create_comment', 'Create a footer comment, native reply, or an inline comment with exact text selection; uncertain writes are never repeated.', {
        'target': field(S), 'text': field(S), 'replyTo': field(S), 'format': field(S), **BODY,
        'selection': field(S), 'occurrence': field(I), **SPACE}, ['target', 'text']),
    tool('update_comment', 'Edit an existing native comment at its version while preserving author and thread.', {
        'comment': field(S), 'version': field(I), 'text': field(S), 'format': field(S), **BODY, **SPACE}, ['comment', 'version', 'text']),
    tool('list_document_versions', 'Read bounded actual native history under current registered access.', {
        'document': field(S), 'limit': field(I, default=20), 'cursor': field(S), **SPACE}, ['document']),
]
SCHEMAS = {item['name']: item for item in WIKI_TOOLS}


def validate_arguments(name, arguments):
    if name not in SCHEMAS:
        raise RemoteError('unknown_tool', 'Unknown tool')
    schema = SCHEMAS[name]['inputSchema']
    if (not isinstance(arguments, dict) or set(arguments) - schema['properties'].keys()
            or set(schema['required']) - arguments.keys()):
        raise RemoteError('invalid_request', 'Supply only the declared required and optional tool arguments')
    types = {S: str, I: int, B: bool, A: list, O: dict}
    result = dict(arguments)
    for key, value in arguments.items():
        if type(value) is not types[schema['properties'][key]['type']]:
            raise RemoteError('invalid_request', 'Tool arguments must have their declared JSON types; omit unused fields')
        if 'enum' in schema['properties'][key] and value not in schema['properties'][key]['enum']:
            raise RemoteError('invalid_request', 'Select a declared connection')
    for key, spec in schema['properties'].items():
        if key not in result and 'default' in spec:
            result[key] = spec['default']
    return result
