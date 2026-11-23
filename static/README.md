# Static assets

Copied to `site/assets/` by the build.

| File | What |
| --- | --- |
| `style.css` | the only stylesheet: mobile first, system fonts, dark mode via `prefers-color-scheme` |
| `app.js` | progressive enhancement without framework (< 14 KB, about 4 KB compressed): filter panel with tag chips (include/exclude) and grouping, row of active filters, foldable groups, swipe, sliding header, date bar arrows, settings in `localStorage` |
| `feedback.js` | sends the contact form in the background and shows the thank-you message on the page (< 1 KB) |
| `search.js` | search across all displayed days, on every page (< 5 KB): fetches `data/search.<lang>.json` on the first search (at least 3 characters), 50 results at a time by date, with status badges and a "show more" button |
| `icon.svg` | favicon |
