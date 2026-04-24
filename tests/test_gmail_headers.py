"""Parsing of RFC 2369 / RFC 8058 unsubscribe headers."""

from use_agent import gmail


def test_parses_https_and_mailto_pair() -> None:
    header = (
        '<mailto:unsub@example.com?subject=unsub-token>, '
        '<https://example.com/unsub?tok=abc>'
    )
    targets = gmail.unsubscribe_targets(header, 'List-Unsubscribe=One-Click')
    assert targets['http_urls'] == ['https://example.com/unsub?tok=abc']
    mailtos = targets['mailtos']
    assert len(mailtos) == 1
    assert mailtos[0]['address'] == 'unsub@example.com'
    assert mailtos[0]['subject'] == 'unsub-token'
    assert targets['one_click'] is True


def test_one_click_requires_https_url() -> None:
    # Header declares one-click, but only offers a mailto. We should
    # not claim one_click — the RFC 8058 flow requires an HTTPS
    # endpoint.
    header = '<mailto:unsub@example.com>'
    targets = gmail.unsubscribe_targets(header, 'List-Unsubscribe=One-Click')
    assert targets['one_click'] is False
    assert targets['http_urls'] == []


def test_commas_inside_mailto_body_are_preserved() -> None:
    # A mailto body that contains a literal comma must not split the
    # URI into two entries — splitting must respect angle-bracket
    # nesting.
    header = (
        '<mailto:u@example.com?subject=unsub&body=stop,please>, '
        '<https://example.com/u>'
    )
    targets = gmail.unsubscribe_targets(header, '')
    assert targets['mailtos'][0]['body'] == 'stop,please'
    assert targets['http_urls'] == ['https://example.com/u']


def test_empty_header_yields_no_targets() -> None:
    targets = gmail.unsubscribe_targets('', '')
    assert targets['http_urls'] == []
    assert targets['mailtos'] == []
    assert targets['one_click'] is False


def test_http_only_scheme_is_accepted_but_not_one_click() -> None:
    # Plain http (not https) URIs are extracted but can't be used
    # for one-click — the RFC requires https.
    header = '<http://example.com/unsub>'
    targets = gmail.unsubscribe_targets(header, 'List-Unsubscribe=One-Click')
    assert targets['http_urls'] == ['http://example.com/unsub']
    assert targets['one_click'] is False
