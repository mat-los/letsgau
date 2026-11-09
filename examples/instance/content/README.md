# Content pages

Markdown pages, one file per language (`<page>.<lang>.md`), built with the site
layout by `letsgau run --job build`:

| Page | German path | English path |
| --- | --- | --- |
| `about` | `/de/about/` | `/en/about/` |
| `missing-event` | `/de/fehlt-mein-event/` | `/en/missing-event/` |
| `imprint` | `/de/impressum/` | `/en/imprint/` |
| `privacy` | `/de/datenschutz/` | `/en/privacy/` |
| `feedback` (optional intro) | `/de/feedback/` | `/en/feedback/` |

The first line `# Title` is the page title. Values from `config.yaml` are filled
in automatically: `{{ site_name }}`, `{{ region_label }}`, `{{ drive_time_minutes }}`,
`{{ display_days }}`, `{{ contact_email }}`, `{{ base_url }}`, `{{ repo_url }}`.
Text only you can write is marked `[TODO: ...]`; `letsgau check` lists every
marker and the build warns about them. Replace them before going live (the
imprint and privacy policy are not legal advice; have them checked).
