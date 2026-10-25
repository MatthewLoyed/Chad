"""Feed curation requires both the interest lookup and actual publisher."""
from types import SimpleNamespace

from src.infrastructure.tools.selection.bundles import default_bundle_groups
from src.infrastructure.tools.selection.turn_selector import TurnToolSelector


def test_interest_and_publishing_tools_are_exposed_together():
    selector = TurnToolSelector(SimpleNamespace(_bundle_groups=default_bundle_groups()))
    for seed in ('manage_curiosity', 'update_dashboard'):
        expanded = selector._expand_bundles([seed])
        assert set(expanded) == {'manage_curiosity', 'update_dashboard'}
        assert expanded[0] == seed


def test_registry_bundle_groups_do_not_share_mutable_members():
    first = default_bundle_groups()
    second = default_bundle_groups()
    first['feed_discovery'].add('fictional_tool')
    assert 'fictional_tool' not in second['feed_discovery']
