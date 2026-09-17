"""Related parties are classified by subledger identifiers stored in app data.

Exclude them from exception/collection worklists and disclose the exclusion.
Keep every row in ledger totals, aging totals and bank reconciliation. Identical
codes in different subledgers may identify different people or organizations;
never classify by a surname or suppress an unrelated trading customer.
"""
from .business_config import get

FAMILY_NAME = "owner-family accounts"
CHIP_LABEL = "family"
CHIP_TITLE = ("Owner-family account. Shareholder distributions, family payroll and family receivables are normal "
              "private-company movement, so they are kept out of exception and collection views. They stay in every "
              "total, ledger and reconciliation.")

def _sets():
    policy = get("related_parties", {})
    return {kind: frozenset(_norm(v) for v in policy.get(kind, [])) for kind in ("customer", "vendor", "employee")}



def _norm(value):
    return (value or "").strip().upper()


def is_related(kind, identifier):
    """True when this SL identifier belongs to the owner family. `kind` is customer / vendor / employee."""
    try:
        return _norm(identifier) in _sets()[kind]
    except KeyError:
        raise ValueError("unknown related-party kind %r (expected one of %s)" % (kind, ", ".join(sorted(_sets()))))


def is_related_customer(customer_id):
    return is_related("customer", customer_id)


def is_related_vendor(vendor_id):
    return is_related("vendor", vendor_id)


def is_related_employee(employee_key):
    return is_related("employee", employee_key)


def ids(kind):
    """The identifier tuple for a kind, ready to bind to a SQL `NOT IN %s` / `IN %s` parameter."""
    return tuple(sorted(_sets()[kind])) or ("",)


def sql_exclude(column, kind):
    """(fragment, params) excluding related parties from a raw-SQL WHERE clause.

        frag, params = sql_exclude("d.customer_id_raw", "customer")
        fetch_dict("SELECT ... WHERE released AND " + frag, other_params + params)

    NULL identifiers are kept — an unattributed row is not a family row."""
    return "(%s IS NULL OR %s NOT IN %%s)" % (column, column), [ids(kind)]


def partition(rows, kind, key):
    """Split rows into (kept, excluded) on a related-party test. `key` names the dict key or is a callable.

    Use on any list that ranks or highlights problems, then report `excluded` with `excluded_note` so the
    omission is visible instead of silent."""
    getter = key if callable(key) else (lambda r: r.get(key))
    kept, excluded = [], []
    for r in rows:
        (excluded if is_related(kind, getter(r)) else kept).append(r)
    return kept, excluded


def excluded_note(excluded, amount_key="amt", noun="item"):
    """A one-line disclosure for whatever `partition` took out, or "" when it took nothing.

    Every surface that filters related parties out should print this, so the books still visibly tie."""
    if not excluded:
        return ""
    n = len(excluded)
    total = 0.0
    for r in excluded:
        try:
            total += float(r.get(amount_key) or 0)
        except (TypeError, ValueError):
            pass
    money = ("%s$%s" % ("-" if total < 0 else "", format(abs(int(round(total))), ","))) if total else ""
    return ("%d %s%s (%s) left out of this list%s. Still included in every total and in the ledger."
            % (n, noun, "" if n == 1 else "s", FAMILY_NAME, (" · " + money) if money else ""))
