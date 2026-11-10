# Static assets

Copied to `site/assets/` by the build.

| File | What |
| --- | --- |
| `style.css` | the only stylesheet: mobile first, system fonts, dark mode via `prefers-color-scheme` |
| `app.js` | progressive enhancement without framework (< 10 KB): tag chips (include/exclude), grouping, swipe, sliding header, date bar arrows, settings in `localStorage` |
| `search.js` | search across all displayed days (< 5 KB): fetches `data/search.<lang>.json` on the first search (at least 3 characters), at most 50 results by date |
| `icon.svg` | favicon |
