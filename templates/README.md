# Templates

Jinja2 templates rendered by `src/letsgau/build.py` (autoescaped).

| File | Page |
| --- | --- |
| `base.html` | layout: head with canonical/hreflang/Open Graph, sticky header, footer with AI notice and "last updated" |
| `day.html` | day page: quick links, date bar, tag chips, grouping, sections, pager |
| `partials/event.html` | macros for one event row (`<details>`, works without JavaScript) |
| `redirect.html` | `/`: jumps to the stored language (`localStorage`), else the default language |
| `page.html` | content pages from `instance/content/*.md` and the changelog |
| `feedback.html` | feedback form (`site.feedback_form_endpoint`) or `mailto:` links, link to the issue forms |

UI strings come from `i18n/<lang>.yaml`; event texts are bilingual in the data.
