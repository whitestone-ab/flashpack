# Architecture

The demo is intentionally split into a small set of layers so FlashPack can
show full/skeleton content, dependency information and exclusion handling in
one snapshot.

## Entry point

`src/main.py` wires settings, the domain model, and the greeting function.

## Domain

`src/models.py` contains a dataclass. FlashPack keeps the class signature,
field annotations, and method signatures when it compresses this file to an AST
skeleton.

## JavaScript helper

`src/retry.js` demonstrates a non-Python source file. Without a prefetched
tree-sitter JavaScript grammar, FlashPack falls back to comment stripping,
import normalization and truncation instead of pretending to have a structural
skeleton.

## Configuration

`config/settings.json` is outside the default include whitelist; add it with
`--include "*.json"` when needed.

## Notes

This demo does not contain real credentials. `.env` is intentionally ignored.
