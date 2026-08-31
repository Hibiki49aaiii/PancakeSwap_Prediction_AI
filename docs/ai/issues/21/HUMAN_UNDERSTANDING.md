# Issue #21 Human Understanding

## Problem

Campaign Evidence proves campaign history. It is not a process heartbeat.

During a provider outage, the continuous runtime can be alive and retrying without writing a new successful-cycle Evidence file.

An operator watching only files cannot tell whether the process is:

- healthy;
- retrying;
- dead.

## Solution

Add a separate operational status file that is updated atomically on:

- successful cycles;
- retryable cycle errors;
- fatal cycle errors before exit.

## Important distinction

The status file is not evidence that a prediction campaign is valid, profitable or complete.

It answers an operational question:

> What is this runtime process currently doing, and when did it last complete a successful cycle?

## Privacy

Only exception class names are stored for errors.

Raw exception messages are intentionally excluded because provider errors can contain endpoint or credential details.

## Atomicity

A monitor should never read half-written JSON.

The runtime writes a temporary sibling file and atomically replaces the prior status file.

## Scope

This creates a file-level health checkpoint that external tooling can consume later. It does not itself install a service manager, metrics server, or alerting system.
