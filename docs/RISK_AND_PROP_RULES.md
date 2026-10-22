# Risk and Prop-Firm Rules

`risk/profiles/*.yaml` stores source URLs, verification timestamps, values, and provenance tags. Null values are intentional and must fail closed through `require_numeric` when a simulation needs them.

FundingPips currently has several populated values marked verified in the local profile, while its maximum-trades-per-day field remains an assumption/null. FTMO, FunderPro, The5ers, and Topstep profiles remain assumption/null profiles. No values should be copied from memory.

`RuleEngine` implements daily lockout, maximum trades, monotone stop tightening, and maximum-drawdown lockout. The current profile model does not yet fully encode every firm's equity-vs-balance basis, trailing ratchet, reset timezone, news/overnight restrictions, or account-specific rule variants. Those are required before a profile can be used for a real decision.

No live order path is authorized. Demo and Monte Carlo outputs are simulations and upper bounds, not live viability evidence.
