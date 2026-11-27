"""List API prices per million tokens, used to turn token counts into an estimated API cost.

These are first-party list prices (not what a Pro/Max subscription costs you): read them as
"what this usage would cost on the API", which is the right yardstick for comparing models.
Override or extend with ~/.claude-helper/prices.json: {"claude-opus-5-5": {"in": 4, "out": 20, "cache_read": 0.2}}.
Keys are matched as substrings of the model id, longest key first. Cache writes are 1.25x input (5 min) / 2x (1 h).
"""
import json, os

PRICES = {   # $ per million tokens; cache_read defaults to 0.1 x input
    'claude-fable-5-2': {'in': 8, 'out': 40, 'cache_read': 0.2},
    'claude-mythos-5-2': {'in': 8, 'out': 40, 'cache_read': 0.2},
    'claude-fable-5': {'in': 10, 'out': 50},
    'claude-mythos-5': {'in': 10, 'out': 50},
    'claude-opus-5-5': {'in': 4, 'out': 20, 'cache_read': 0.2},
    'claude-opus-5': {'in': 5, 'out': 25},
    'claude-opus-4': {'in': 5, 'out': 25},
    'claude-sonnet-5-5': {'in': 2, 'out': 10, 'cache_read': 0.1},
    'claude-sonnet-5': {'in': 2, 'out': 10},
    'claude-sonnet-4': {'in': 3, 'out': 15},
    'claude-haiku-5-5': {'in': 0.1, 'out': 0.5},
    'claude-haiku-4': {'in': 1, 'out': 5},
}
FALLBACK = {'in': 3, 'out': 15}   # unknown model: priced like a mid-tier model


def _load():
    table = dict(PRICES)
    p = os.path.join(os.path.expanduser(os.getenv('CLAUDE_HELPER_HOME', '~/.claude-helper')), 'prices.json')
    try:
        table.update(json.load(open(p)))
    except (OSError, ValueError):
        pass
    return sorted(table.items(), key=lambda kv: -len(kv[0]))


_TABLE = _load()


def price(model):
    m = model or ''
    for k, v in _TABLE:
        if k in m:
            return v
    return FALLBACK


def usd(u, model):
    """Estimated list-price API cost of one API call's usage record."""
    p = price(model)
    cw = u.get('cache_creation_input_tokens') or 0
    cc = u.get('cache_creation') or {}
    c5, c1 = cc.get('ephemeral_5m_input_tokens', cw), cc.get('ephemeral_1h_input_tokens', 0)
    cr = p.get('cache_read', 0.1 * p['in'])
    return ((u.get('input_tokens') or 0) * p['in'] + (u.get('cache_read_input_tokens') or 0) * cr
            + (1.25 * c5 + 2.0 * c1) * p['in'] + (u.get('output_tokens') or 0) * p['out']) / 1e6
