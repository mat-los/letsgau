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
in automatically: `{{ site_name }}`, `{{ region_label }}`, `{{ municipalities }}`,
`{{ display_days }}`, `{{ contact_email }}`, `{{ base_url }}`, `{{ repo_url }}`,
`{{ instagram_posts }}` (posts read per Instagram account and run).
`{{ mail_link() }}` (or `{{ mail_link('Subject') }}`) shows `contact_email` as a link that
simple scrapers do not find: the HTML holds only the parts of the address, and
`static/mail.js` turns them into a normal mailto link in the browser. `{{ imprint_name }}`
comes from the environment (`LETSGAU_IMPRINT_NAME`, the repository variable
`IMPRINT_NAME` in the workflow), so the name is not in the public repository.
Text only you can write is marked `[TODO: ...]`; `letsgau check` lists every
marker and the build warns about them. Replace them before going live (the
imprint and privacy policy are not legal advice; have them checked).
