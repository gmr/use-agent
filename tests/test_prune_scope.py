"""Derivation of the cache-prune folder scope from a run query."""

from use_agent import agent


def test_default_inbox_query_scopes_to_inbox() -> None:
    # Byte-for-byte identical to the pre-fix hardcoded behavior.
    assert agent._prune_query('in:inbox is:unread') == 'in:inbox'


def test_spam_query_scopes_to_spam() -> None:
    assert agent._prune_query('in:spam') == 'in:spam'


def test_first_in_operand_wins() -> None:
    assert agent._prune_query('in:spam -from:example.com') == 'in:spam'


def test_label_operand_is_preserved() -> None:
    assert agent._prune_query('label:promotions is:unread') == (
        'label:promotions'
    )


def test_appended_lookback_does_not_shadow_folder() -> None:
    query = 'in:spam is:unread newer_than:2d'
    assert agent._prune_query(query) == 'in:spam'


def test_query_without_folder_falls_back_to_inbox() -> None:
    assert agent._prune_query('is:unread from:foo@example.com') == 'in:inbox'


def test_negated_folder_operand_does_not_scope() -> None:
    assert agent._prune_query('-in:spam is:unread') == 'in:inbox'


def test_negated_folder_operand_does_not_shadow_positive_one() -> None:
    assert agent._prune_query('in:spam -label:promotions') == 'in:spam'
