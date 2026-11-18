# Release history

## Repository layout and documentation · 2026-11-17

The bilingual landing pages now use actual Reader screenshots and locally rendered architecture diagrams. Reader code and browser assets live in `reader/`; launch and build scripts live in `scripts/`. The Next engine remains in `next/`, and historical material remains in `Demo/`.

Local startup uses `python scripts/bootstrap_reader.py`. Deployments must point `--reader` to `reader/` and the vendor directory to `reader/vendor/`. Client download URLs remain unchanged. This update does not migrate project or learning databases.

## Next 0.8.1

The Next workflow covers source registration, generation, translation, resource explanations, study materials, review, and confirmed delivery. The web release uses the existing authenticated Reader entry point and preserves project and learning records.

- [Connection and deployment](docs/next-deployment.md)
- [Earlier releases](Demo/legacy-docs/CHANGELOG.md)
