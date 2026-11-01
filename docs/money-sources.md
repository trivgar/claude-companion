# Money sources

The Money tab shows what a money source reports. None is set up until you pick
one: set `money_source` in `~/.config/agent-companion/config.toml` (or
`AGENT_COMPANION_MONEY_SOURCE`). `demo` shows a made-up household, labelled
DEMO, so you can see the panel first. Empty means the tab says "No money source
set up".

Your Claude writes the real source. It is one file, `agent_companion/money/<name>.py`,
registered next to `demo` in `agent_companion/money/__init__.py`.

## Where the figures can come from

- **SimpleFIN Bridge.** Read-only bank feeds through a bridge you pay for,
  about $15 a year. You get an access URL; the source fetches accounts and
  transactions from it. Keep the URL in your secret store, never in the repo.
- **A CSV export.** Download statements from your bank, import them into a
  SQLite file on this machine, and have the source query it. Free, manual, and
  as fresh as your last import (say so in `freshness`).
- **Plaid.** If you already have Plaid keys, a source can read through them.
  Read scopes only.

## The interface

A source is a class with a `name` and these methods (see `MoneySource` in
`agent_companion/money/__init__.py`):

| Method | Returns |
|---|---|
| `accounts()` | `[{id, name, kind, balance, currency, updated}]`, kind one of checking, savings, credit, loan, investment |
| `net_worth(days=90)` | `{total, history: [{date, total}]}`, oldest first |
| `cash_flow(months=6)` | `[{month "YYYY-MM", income, spending}]`, oldest first |
| `spending_by_category(start, end)` | `[{category, amount}]` for ISO dates, biggest first |
| `recurring()` | `[{name, amount, every, next_date}]` with every weekly, monthly or yearly |
| `upcoming(days=14)` | `[{date, name, amount}]`, soonest first |
| `freshness()` | `{source, as_of}` |

Register it:

```python
from agent_companion import money

class MyBankSource:
    name = "mybank"
    ...

money.register("mybank", MyBankSource)
```

`balance` on a credit or loan account is the amount owed, as a positive number.
Net worth subtracts it.

## The rules

- **Read only.** No method that moves money, pays, transfers, sends, creates,
  updates or deletes. `tests/test_money.py` fails on any source attribute that
  looks like one. Use read-only credentials where the provider offers them.
- **Never move money.** Not from the panel, not from a script, not on a hunch.
- **A figure you do not have is unknown, never $0.00.** Return `None`. The
  server sends null and the page draws a dash. A zero claims something about
  the person's money that you cannot know.
- **Null tax and null hours are unknown, not zero.** A missing tax rate does
  not mean none was paid; missing hours do not mean no work.
- **Freshness says how old.** `as_of` is when the figures were last pulled from
  the bank or last imported, not when the page loaded.
- **Do the arithmetic once, in the source.** The page only draws what it is
  given, so the two cannot drift.
- **Nothing personal in the repo.** Account names, merchants and balances stay
  in your own database and config, never in a commit.
