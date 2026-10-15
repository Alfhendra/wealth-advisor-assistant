"""Validation and deterministic, explainable demo analytics (not investment advice)."""
from __future__ import annotations

import statistics
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from .contracts import InputError


# --- Shared input checks ---
def money(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise InputError(f'{field} must be a non-negative monetary value')
    try:
        # Validate cents before converting to the JSON-friendly numeric representation.
        parsed = Decimal(str(value))
        if not parsed.is_finite() or parsed < 0 or parsed > Decimal('1000000000000'):
            raise InvalidOperation
        if parsed != parsed.quantize(Decimal('0.01')):
            raise InvalidOperation
        return float(parsed)
    except InvalidOperation as exc:
        raise InputError(f'{field} must be finite, non-negative, <= 1e12, and have at most 2 decimals') from exc


def text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise InputError(f'{field} must be a nonempty string of at most 200 characters')
    return value.strip()


def iso_date(value: Any, field: str) -> date:
    # fromisoformat also accepts compact and week dates; our input contract does not.
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value):
        raise InputError(f'{field} must be an ISO date YYYY-MM-DD')
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise InputError(f'{field} must be a valid calendar date') from exc


# --- Raw input to cleaned records ---
class FinancialDataTool:
    def prepare(self, raw: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise InputError('Input must be a JSON object')
        required = ['client_id', 'as_of', 'currency', 'monthly_income', 'monthly_expenses',
                    'liquid_assets', 'debt', 'holdings', 'transactions']
        missing = [key for key in required if key not in raw]
        if missing:
            raise InputError('Missing fields: ' + ', '.join(missing))
        result = {key: text(raw[key], key) for key in ['client_id', 'currency']}
        if result['currency'] != 'USD':
            raise InputError('Demo supports USD only; convert currencies upstream')
        as_of = iso_date(raw['as_of'], 'as_of')
        result['as_of'] = as_of.isoformat()
        for key in ['monthly_income', 'monthly_expenses', 'liquid_assets', 'debt']:
            result[key] = money(raw[key], key)
        for key in ['holdings', 'transactions']:
            if not isinstance(raw[key], list) or len(raw[key]) > 10000:
                raise InputError(f'{key} must be an array with at most 10000 entries')
        # Holdings need unique symbols; silently merging them could hide input mistakes.
        result['holdings'] = []
        holding_ids = set()
        for h in raw['holdings']:
            if not isinstance(h, dict):
                raise InputError('Each holding must be an object')
            symbol = text(h.get('symbol'), 'holding.symbol')
            if symbol in holding_ids:
                raise InputError('Duplicate holding symbol: ' + symbol)
            holding_ids.add(symbol)
            result['holdings'].append({'symbol': symbol, 'value': money(h.get('value'), 'holding.value')})

        # Repeated identical records are safe to drop; conflicting IDs need correction upstream.
        result['transactions'] = []
        seen = {}
        removed = 0
        for tx in raw['transactions']:
            if not isinstance(tx, dict):
                raise InputError('Each transaction must be an object')
            tid = text(tx.get('id'), 'transaction.id')
            when = iso_date(tx.get('date'), f'Transaction {tid} date')
            if when > as_of:
                raise InputError(f'Transaction {tid} is dated after as_of')
            if tx.get('direction') not in ('inflow', 'outflow'):
                raise InputError(f'Transaction {tid} requires direction inflow or outflow')
            clean = {'id': tid, 'date': when.isoformat(), 'direction': tx['direction'],
                     'amount': money(tx.get('amount'), 'transaction.amount')}
            if tid in seen:
                if seen[tid] != clean:
                    raise InputError('Conflicting duplicate transaction: ' + tid)
                removed += 1
                continue
            seen[tid] = clean
            result['transactions'].append(clean)
        result['transactions'].sort(key=lambda tx: (tx['date'], tx['id']))
        result['quality'] = {'duplicates_removed': removed, 'transaction_count': len(seen)}
        return result


# --- Curated records to explainable findings ---
class RuleAnalysisTool:
    """Thresholds are synthetic demo policy, deliberately not financial recommendations."""
    def analyze(self, data: dict[str, Any]) -> dict[str, Any]:
        signals = []
        def flag(code, severity, evidence, action):
            signals.append({'code': code, 'severity': severity, 'evidence': evidence, 'suggested_action': action})

        # Cash flow and reserves: use the supplied monthly estimates, not a partial ledger.
        income, expenses = data['monthly_income'], data['monthly_expenses']
        runway = round(data['liquid_assets'] / expenses, 2) if expenses else None
        if expenses > income:
            flag('NEGATIVE_CASH_FLOW', 'high', {'income': income, 'expenses': expenses},
                 'Validate recurring income and expenses with the client; review the spending plan.')
        # Compare exact amounts before rounding the displayed runway near the boundary.
        if expenses and Decimal(str(data['liquid_assets'])) < 3 * Decimal(str(expenses)):
            flag('LOW_LIQUIDITY', 'high', {'runway_months': runway, 'threshold_months': 3},
                 'Discuss near-term liquidity needs and confirm available reserves.')

        # Concentration is measured only against reported holdings.
        total = float(sum(Decimal(str(h['value'])) for h in data['holdings']))
        if total:
            for h in data['holdings']:
                share = h['value'] / total
                if share > .40:
                    flag('CONCENTRATION', 'medium', {'symbol': h['symbol'], 'share': round(share, 4), 'threshold': .40},
                         'Review concentration against the client mandate before considering any changes.')

        # Transaction outliers: inflows should not distort the spending baseline.
        outflows = [tx for tx in data['transactions'] if tx['direction'] == 'outflow']
        amounts = [tx['amount'] for tx in outflows]
        if len(amounts) >= 5:
            median = statistics.median(amounts)
            mad = statistics.median(abs(a - median) for a in amounts)
            # Robust one-sided rule: require an absolute floor and an extreme relative amount.
            threshold = max(1000, median + 6 * 1.4826 * mad, median * 3)
            for tx in outflows:
                if tx['amount'] > threshold:
                    flag('UNUSUAL_OUTFLOW', 'high', {'transaction_id': tx['id'], 'amount': tx['amount'],
                         'median': median, 'mad': mad, 'threshold': round(threshold, 2)},
                         'Verify the transaction purpose and authorization with the client.')

        # Spending trend: anchor both windows to as_of so replaying input is reproducible.
        as_of = date.fromisoformat(data['as_of'])
        current, prior = Decimal('0'), Decimal('0')
        for tx in outflows:
            age = (as_of - date.fromisoformat(tx['date'])).days
            if 0 <= age < 30:
                current += Decimal(str(tx['amount']))
            elif 30 <= age < 60:
                prior += Decimal(str(tx['amount']))
        if prior > 0 and current > prior * Decimal('1.5') and current - prior > 1000:
            flag('SPENDING_INCREASE', 'medium', {'current_30d': float(current), 'prior_30d': float(prior),
                 'increase_ratio': round(float(current / prior - 1), 4)},
                 'Confirm both windows have complete transaction coverage and explain the spending increase.')

        # Keep evidence and limitations together so a skipped check is visible to the reviewer.
        return {'metrics': {'monthly_surplus': float(Decimal(str(income)) - Decimal(str(expenses))),
                            'liquidity_runway_months': runway, 'portfolio_value': total,
                            'debt': data['debt'], 'current_30d_outflows': float(current),
                            'prior_30d_outflows': float(prior)},
                'signals': signals,
                'limitations': (['Fewer than five outflows: transaction outlier detection skipped.'] if len(amounts) < 5 else [])
                    + (['Zero expenses: liquidity runway is undefined.'] if not expenses else [])
                    + (['No prior-window outflows: spending growth cannot be computed.'] if not prior else [])
                    + ['Transaction coverage is not independently verified; trend flags require manual confirmation.']}
