# Suggested sources

The discovery job writes its suggestions to `data/suggested.md` on the bot-only
`data` branch (generated files never go to `main`, see ADR-004): whitelist
candidates (sources with many hits or a top rating) and blacklist candidates,
each as a block to copy into `whitelist.yaml` or `blacklist.yaml`.

Open it on GitHub: switch to the `data` branch and open `data/suggested.md`.
