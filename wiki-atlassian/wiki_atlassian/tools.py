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


def bounded_integer(minimum, maximum, description, default=None):
    return {**field(I, description, default), 'minimum': minimum, 'maximum': maximum}


S, I, B, A, O = 'string', 'integer', 'boolean', 'array', 'object'
SPACE = {'space': field(S, 'Exact configured space key; never guess an ambiguous write destination')}
META = {'metadata': field(B, 'Include detailed native metadata', False)}
BODY = {**META, 'includeBody': field(B, 'Include bounded saved native content', False)}
MAX_RETRIEVAL_BATCH = 6
MAX_BATCH_READ_CHARS = 24000
MAX_BATCH_SEARCH_HITS = 30
READ_RANGE = {
    'document': field(S, 'Native page ID or URL'), 'version': field(I), 'offset': field(I, default=0),
    'length': bounded_integer(1, 12000, 'Unicode characters, maximum 12000', 4000),
    'format': field(S, default='markdown'), 'section': field(S), 'snapshot': field(S),
    'preferReplica': field(B, 'Use the admitted published snapshot for current outline or section reads'), **SPACE,
}


def batch(items, description):
    return {**field(A, description), 'minItems': 1, 'maxItems': MAX_RETRIEVAL_BATCH, 'items': items}


def tool(name, description, properties, required=(), either=()):
    schema = {'name': name, 'description': description, 'inputSchema': {
        'type': 'object', 'properties': {**properties, 'connection': {
            'type': 'string', 'enum': ['project', 'personal'],
            'description': 'Project is the default. Select personal explicitly for the independently configured private page; no credential fallback.'}},
        'required': list(required), 'additionalProperties': False}}
    if either:
        schema['inputSchema']['oneOf'] = [{'required': [key]} for key in either]
    return schema


WIKI_TOOLS = [
    tool('connection_status', 'Show readiness, scope and synchronization errors for each configured space. Partial readiness is not complete coverage.', {}),
    tool('search_documents', 'Search under the project wiki root. Use queries for several related terms in one space; each group returns exact evidence locators.', {
        'query': field(S), 'queries': batch(field(S), 'Related search terms as strings, e.g. ["release policy", "HD-57"]'),
        'scope': field(S, default='effective'), 'limit': bounded_integer(1, 25, 'Hits per query. Defaults to 10 for a single query or 3 for a batch, with at most 30 total for a batch.'),
        'cursor': field(S), **META, 'root': field(S), 'snippetLength': bounded_integer(1, 500, 'Excerpt characters'), **SPACE}, either=('query', 'queries')),
    tool('document_relations', 'Read explicit authored relations and affected review candidates; links do not establish truth.', {
        'document': field(S), 'depth': field(I, default=2), 'limit': field(I, default=25), **SPACE}, ['document']),
    tool('get_document', 'Read exact search locators or document ranges. Use reads for related evidence or guide openings in one project space. Continue only when truncated, using nextOffset and the same version, format and snapshot.', {
        **READ_RANGE, **META,
        'reads': batch({'type': O, 'properties': READ_RANGE, 'required': ['document'], 'additionalProperties': False},
                       'Ranges from one admitted snapshot, e.g. [{"document": "123", "length": 2000}]. Up to 24,000 characters total.'),
    }, either=('document', 'reads')),
    tool('create_document', 'Create under a native parent or explicit space, one mutation with verified native readback.', {
        'parent': field(S), 'title': field(S), 'content': field(S), 'format': field(S), **BODY,
        'basedOn': field(A), **SPACE}, ['title', 'content']),
    tool('update_document', 'Use one exact versioned body edit or a pure append. Use defer only for an independent versioned update, then check operation_status for the verified result.', {
        **{name: field(S) for name in ('document', 'content', 'title', 'old_text', 'new_text', 'append', 'format', 'parent', 'section')},
        'version': field(I), **BODY, 'edits': field(A), 'allowReferenceRemoval': field(B, default=False),
        'basedOn': field(A), 'links': field(O),
        'defer': field(B, 'Queue an explicit document and version and return a session-local operation ID'), **SPACE}),
    tool('operation_status', 'Check a deferred update in the same MCP session and connection. Queued and running are not stored outcomes.', {
        'operationId': field(S, 'Operation ID returned by update_document')}, ['operationId']),
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
    result = validate_object(schema, arguments)
    if 'reads' in arguments or 'queries' in arguments:
        if arguments.get('connection') == 'personal':
            raise RemoteError('invalid_request', 'Batch retrieval uses one configured project space')
        if 'reads' in arguments and set(arguments) - {'reads', 'space', 'connection'}:
            raise RemoteError('invalid_request', 'Set range options on each item in reads')
        if 'queries' in arguments and 'cursor' in arguments:
            raise RemoteError('invalid_request', 'Use a single query to continue a search')
    if 'reads' in result:
        if sum(row['length'] for row in result['reads']) > MAX_BATCH_READ_CHARS:
            raise RemoteError('invalid_request', 'Read at most 24,000 characters per batch')
        return {key: result[key] for key in arguments}
    if name == 'search_documents':
        result.setdefault('limit', 3 if 'queries' in result else 10)
    return result


def validate_object(schema, arguments, bounded=False, path='arguments'):
    # Diagnostics use only schema keys and array indices, never supplied keys or values.
    if not isinstance(arguments, dict):
        raise RemoteError('invalid_request', f'{path} must be an object')
    missing = set(schema['required']) - arguments.keys()
    if missing:
        fields = ', '.join(f'{path}.{key}' for key in sorted(missing))
        raise RemoteError('invalid_request', f'Required fields: {fields}')
    choices = schema.get('oneOf', [])
    if choices and sum(set(choice['required']) <= arguments.keys() for choice in choices) != 1:
        forms = ' or '.join(', '.join(choice['required']) for choice in choices)
        raise RemoteError('invalid_request', f'{path} requires exactly one of: {forms}')
    properties = schema['properties']
    if set(arguments) - properties.keys():
        fields = ', '.join(properties)
        raise RemoteError('invalid_request', f'Allowed fields at {path}: {fields}')
    types = {S: str, I: int, B: bool, A: list, O: dict}
    result = dict(arguments)
    for key, value in arguments.items():
        spec = properties[key]
        kind = spec['type']
        field_path = f'{path}.{key}'
        if type(value) is not types[kind]:
            raise RemoteError('invalid_request', f'{field_path} must have JSON type {kind}')
        if 'enum' in spec and value not in spec['enum']:
            raise RemoteError('invalid_request', f'{field_path} must be one of: {", ".join(spec["enum"])}')
        if bounded and kind == I and not spec.get('minimum', value) <= value <= spec.get('maximum', value):
            raise RemoteError('invalid_request', f'{field_path} exceeds its declared bound')
        if spec.get('minItems') is not None:
            minimum, maximum = spec['minItems'], spec['maxItems']
            if not minimum <= len(value) <= maximum:
                raise RemoteError('invalid_request', f'{field_path} must contain {minimum} to {maximum} entries')
            item = spec['items']
            item_kind = item['type']
            for index, entry in enumerate(value):
                item_path = f'{field_path}[{index}]'
                if type(entry) is not types[item_kind]:
                    raise RemoteError('invalid_request', f'{item_path} must have JSON type {item_kind}')
            if item_kind == O:
                result[key] = [
                    validate_object(item, entry, bounded=True, path=f'{field_path}[{index}]')
                    for index, entry in enumerate(value)
                ]
    for key, spec in schema['properties'].items():
        if key not in result and 'default' in spec:
            result[key] = spec['default']
    return result
