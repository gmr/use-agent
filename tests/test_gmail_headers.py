"""Parsing of RFC 2369 / RFC 8058 unsubscribe headers."""

from use_agent import gmail


class _FakeList:
    """Records the ``q`` passed to ``users().messages().list``."""

    def __init__(self, calls: list[str], pages: list[dict]) -> None:
        self._calls = calls
        self._pages = pages

    def list(self, *, userId, q, maxResults, pageToken):  # noqa: N803
        self._calls.append(q)
        return self

    def execute(self) -> dict:
        return self._pages.pop(0)


class _FakeMessages:
    def __init__(self, lst: _FakeList) -> None:
        self._lst = lst

    def messages(self) -> _FakeList:
        return self._lst


class _FakeService:
    def __init__(self, lst: _FakeList) -> None:
        self._msgs = _FakeMessages(lst)

    def users(self) -> _FakeMessages:
        return self._msgs


def _client_with(pages: list[dict], calls: list[str]) -> gmail.GmailClient:
    client = object.__new__(gmail.GmailClient)
    client._service = _FakeService(_FakeList(calls, pages))
    return client


def test_list_message_ids_defaults_to_inbox() -> None:
    calls: list[str] = []
    client = _client_with([{'messages': [{'id': 'a'}]}], calls)
    assert client.list_message_ids() == {'a'}
    assert calls == ['in:inbox']


def test_list_message_ids_honors_query_and_paginates() -> None:
    calls: list[str] = []
    pages = [
        {'messages': [{'id': 'a'}], 'nextPageToken': 't'},
        {'messages': [{'id': 'b'}]},
    ]
    client = _client_with(pages, calls)
    assert client.list_message_ids('in:spam') == {'a', 'b'}
    assert calls == ['in:spam', 'in:spam']


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


def test_relationship_queries_build_sent_and_inbound_bounds() -> None:
    queries = gmail._relationship_queries(
        'Jane Rep <jane@vendor.com>', 1_700_000_000, min_age_days=60
    )
    # Cheapest outbound signal first; inbound cutoff = 1_700_000_000
    # - 60 * 86400 = 1_694_816_000.
    assert queries == [
        'in:sent to:jane@vendor.com before:1700000000',
        'from:jane@vendor.com before:1694816000',
    ]


def test_relationship_queries_empty_without_address() -> None:
    assert gmail._relationship_queries('', 1_700_000_000) == []


def test_relationship_queries_empty_without_timestamp() -> None:
    # No usable internalDate means no trustworthy before: bound, so
    # skip the lookup rather than search unbounded Sent mail.
    assert gmail._relationship_queries('jane@vendor.com', 0) == []


def test_relationship_queries_omits_inbound_for_old_message() -> None:
    # A message older than the age window can't have an inbound
    # cutoff above epoch 0, so only the outbound query is built.
    assert gmail._relationship_queries('jane@vendor.com', 100) == [
        'in:sent to:jane@vendor.com before:100'
    ]
