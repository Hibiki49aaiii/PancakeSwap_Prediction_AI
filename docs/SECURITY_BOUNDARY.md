# Security boundary

The v0.7 canonical codebase keeps research, modeling, and transaction semantics separate from signing authority.

## Hard rules

- No private-key field, seed phrase, keystore parser, or wallet-unlock path belongs in the research package.
- `JsonRpcClient` is read-oriented and has no transaction broadcast method.
- Transaction-capable `LocalForkRpcClient` accepts only literal loopback hosts (`127.0.0.1`, `localhost`, `::1`).
- The only transaction RPC currently exposed is `eth_sendTransaction` to an impersonated account on a local Anvil fork.
- `eth_sendRawTransaction` is intentionally absent.
- AI/LLM components must never own signing authority.
- A Stage 5A/5B infrastructure test is not evidence of profitability.
- Funded validation remains a separate explicit gate.

## Semantic intent

`UnsignedBetIntent` freezes chain, wallet, market, epoch, side, target, value, and calldata into a deterministic SHA-256 semantic hash. Later durable execution work must preserve that semantic identity across retries and nonce replacements.
