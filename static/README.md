# Static assets

Copied to `site/assets/` by the build.

| File | What |
| --- | --- |
| `style.css` | the only stylesheet: mobile first, system fonts, dark mode via `prefers-color-scheme` |
| `app.js` | progressive enhancement without framework (< 12 KB, about 3.5 KB compressed): tag chips (include/exclude), grouping with foldable groups, foldable categories, swipe, sliding header, date bar arrows, settings in `localStorage` |
| `search.js` | search across all displayed days, on every page (< 5 KB): fetches `data/search.<lang>.json` on the first search (at least 3 characters), 50 results at a time by date, with status badges and a "show more" button |
| `icon.svg` | favicon |
