try:
    import pytest
except ModuleNotFoundError as error:
    if error.name != 'pytest':
        raise
    from unittest import SkipTest
    raise SkipTest('pytest is required') from error

from wiki_atlassian.references import native_page_url_id
from wiki_atlassian.models import RemoteError


@pytest.mark.parametrize('path', ['/wiki/spaces/TEAM/pages/42', '/wiki/api/v2/pages/42/한글%20title'])
def test_native_ui_and_api_page_references(path):
    assert native_page_url_id('https://site.invalid' + path, 'https://site.invalid', 'TEAM', True) == '42'


@pytest.mark.parametrize('suffix', ['#', '?', '/..', '/%2e%2e', '/%252e', '/%ZZ', '/%2f', '/%255c', '/bad slug', '/%C2%80', '/%FF', '/a[b]', '/a{b}', '/a|b', '/a^b', '/a`b'])
def test_invalid_native_page_suffixes_do_not_supply_authority(suffix):
    with pytest.raises(RemoteError):
        native_page_url_id('https://site.invalid/wiki/spaces/TEAM/pages/42' + suffix, 'https://site.invalid', 'TEAM', True)


def test_explicit_space_and_origin_stay_exact():
    with pytest.raises(RemoteError) as error:
        native_page_url_id('https://site.invalid/wiki/spaces/OTHER/pages/42', 'https://site.invalid', 'TEAM')
    assert error.value.code == 'space_mismatch'
    with pytest.raises(RemoteError):
        native_page_url_id('https://elsewhere.invalid/wiki/spaces/TEAM/pages/42', 'https://site.invalid')
