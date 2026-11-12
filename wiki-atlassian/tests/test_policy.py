import pytest

from wiki_atlassian.models import RemoteError
from wiki_atlassian.policy import reject_secrets


@pytest.mark.parametrize('body', [
    '<p>pass<strong>word</strong>: meaningful-private-value</p>',
    '<p>Authorization:</p><p>Bearer abcdefghijklmnop</p>',
    '<p>prefixeyJabcdefgh.abcdefgh.abcdefgh</p>',
    '<ac:structured-macro ac:name="code"><ac:plain-text-body><![CDATA[password=private-value]]></ac:plain-text-body></ac:structured-macro>',
    '<table><tr><th>api key</th><td>meaningful-private-value</td></tr></table>',
    '<table><tr><th>token</th></tr><tr><td>meaningful-private-value</td></tr></table>',
    '<table><tr><th colspan="2">private key</th></tr><tr><th>개발</th><th>QA</th></tr><tr><td>REDACTED</td><td>private-value</td></tr></table>',
])
def test_rejects_visible_text_cdata_and_native_credential_tables(body):
    with pytest.raises(RemoteError) as failure:
        reject_secrets(body)
    assert failure.value.code == 'secret_detected'
    assert 'locations' in failure.value.details
    assert 'private-value' not in str(failure.value.details)


@pytest.mark.parametrize('value', ['REDACTED', '환경 변수', '없음', '***', ''])
def test_redacted_credential_tables_remain_editable(value):
    reject_secrets('<table><tr><th>token</th><td>' + value + '</td></tr></table>')


def test_invalid_overlapping_table_spans_are_not_guessed():
    with pytest.raises(RemoteError) as failure:
        reject_secrets('<table><tr><td rowspan="9">Value</td></tr></table>')
    assert failure.value.code == 'invalid_table'


def test_literal_current_token_cannot_be_hidden_in_inline_markup():
    with pytest.raises(RemoteError) as failure:
        reject_secrets('<p>ordinary-<b>credential</b></p>', 'ordinary-credential')
    assert failure.value.code == 'secret_detected'


def test_large_repeated_credential_headers_reject_without_diagnostic_expansion():
    body = '<table>' + ('<tr>' + '<th>token</th>' * 10 + '</tr>') * 800 + '</table>'
    with pytest.raises(RemoteError) as failure:
        reject_secrets(body)
    assert failure.value.code == 'secret_detected'
    assert len(failure.value.details['locations']) <= 10


def test_token_shaped_nonsecret_cannot_block_the_runtime():
    from pathlib import Path
    import subprocess
    import sys
    code = ('import sys; sys.path.insert(0,sys.argv[1]); '
            'from wiki_atlassian.policy import reject_secrets; '
            'reject_secrets("<p>"+"eyJ"*100000+"</p>")')
    subprocess.run([sys.executable, '-B', '-c', code, str(Path(__file__).resolve().parents[1])],
                   timeout=5, capture_output=True, check=True)
