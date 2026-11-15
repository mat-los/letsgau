# Product and efficiency review, 2026-11-15

Inputs: `main` at `c24c1d5`, the `data` branch at `149e158`, the log of *Run: events*
2026-11-14 18:13 (run id 43625411737), the site built from the live data, and the
[review of 2026-11-13](2026-11-13-efficiency-review.md), whose F1–F7 items are now done.

## Verdict

The approach is sound. A static site, AI extraction behind deterministic validation, and
caches for pages, flyers and posts is the right shape for a near-free local calendar. Since
the last review the cost of an events run is down 44 %, with more events. The open
questions are no longer about the architecture. They are **reach** (how people find and
come back to the site) and **scale** (the concept's 30-minute area is about 10× today's
4 municipalities).

## Key numbers

| What | 13 Nov | 14 Nov |
| --- | --- | --- |
| AI calls per events run | 22 | 15 |
| API-equivalent cost | $2.01 | **$1.12** |
| Output tokens | 234 k | 67 k |
| Duration | 743 s | 284 s |
| Stored events | 137 | 244 (135 in the 21-day window) |

- Where the $1.12 goes: 10 agent sweeps $0.62 (40 pages), 3 text calls $0.46 (9 changed
  pages), 1 re-check $0.03, merge $0.01, Instagram pre-screen $0.00.
- Monthly at API prices: about $10 for events (2 runs a week) and $3 for discovery. That is
  about **$13 a month** for 4 municipalities.
- Events per place: Schongau 155, Peiting 54, Herzogsägmühle 15, Altenstadt 11, Hohenfurch 8.
- Top primary sources: veranstaltungen.pfaffen-winkel.de 45 (18 %), plantsch.de 28,
  Instagram 23 (9 %).

## Token efficiency: what to do next

1. **Don't sweep flyer pages.** reaktor-sog.de, brauhaus-schongau.de, the pg-schongau
   Pfarrbrief, the Schloßbergler PDF and the Schönach-Kurier are read by the flyer calls
   (cached, $0) *and* by agent sweeps, because they are also in `sources.json`.
   `known_sources_for` should skip URLs on the flyer list. This saves about 1–2 sweep calls
   per run.
2. **21 of 40 agent pages are not the source of any stored event.** Pausing after 3 empty
   runs over 7 days (#120) will drop most of them between 16 and 20 Nov. The whitelist is never
   paused, but `schongau.de/veranstaltungen` and `juze-schongau.de/termine` were empty
   6 runs in a row. Give whitelist entries a "warn and sweep every 4th run" rule instead.
3. **Get more pages into text mode.** 24 agent pages fail with "no dates in the text
   (JavaScript?)" and 7 with HTTP 403. An agent read costs 5–10× a text read.
   - Send normal browser `Accept` / `Accept-Language` headers. Many 403s are bot filters
     that react to their absence. Keep the honest `letsgau` user agent.
   - Before giving up on a page, look for structured data: `application/ld+json` `Event`
     objects, `.ics` / iCal links and RSS feeds. Parse them in Python with no AI at all. The
     regional calendar (pfaffen-winkel.de) and municipal CMS calendars often have one.
     This is also the main lever for scaling (see below).
4. **Send only what changed.** A changed page goes to the AI in full (up to 40 k
   characters; one text call cost $0.22). Hash text blocks instead of whole pages, and send
   only new blocks plus the list of known events. Also strip volatile text (today's date,
   counters, session parameters) before hashing, so fewer pages count as changed.
5. **Try Haiku for `pages` calls.** These are pure extraction from text, with no web and
   few turns. Keep Sonnet for agent sweeps and flyers. Run one *Crawl check* comparing the
   two on the same day. If Haiku finds ≥ 95 % of the events, the text calls cost about a
   third.
6. **Drop filler descriptions** (old F9, still open). 91 of 244 descriptions are under
   60 characters ("Filmvorführung im Lagerhauskino Schongau."). Allow `null`.

Expected together: about $0.50–0.60 per run, and fewer calls than municipalities × sources.

## Instagram API usage

The last run stopped with `(#4) Application request limit reached` after 50 accounts. That
is Meta's rolling **hourly** app limit. Twice a week, all 62 accounts are read in one burst
(10 posts each, carousel children nested).

The yield is uneven: 147 posts are cached, 31 had events, and they came from only 19
accounts. 23 accounts posted nothing within 21 days.

1. **Read accounts by tier.** Read every run: whitelist accounts, and accounts with an event
   in the last 60 days. Read every 3rd run: accounts that are active but have no events.
   Read every 6th run: accounts with no post in 21 days. This is about 40 % fewer calls.
2. **Spread the reads.** Read a third of the accounts on each daily run (the daily
   schedule exists anyway), instead of all of them twice a week. This keeps every burst well
   under the hourly cap and makes posts fresher.
3. **Watch the budget instead of hitting the wall.** Read the `X-App-Usage` /
   `X-Business-Use-Case-Usage` response headers in `http_get` and stop at about 80 %.
   Today the first sign is the error.
4. **Use a smaller default request.** Use `media.limit(5)`. Raise it to 10 only for an
   account whose 5th post is still newer than its `last_checked`.
5. Check the app's usage graph in the Meta developer dashboard once. Development-mode apps
   have much lower limits than live apps.

## Design

From screenshots of the live data on an iPhone 13 and a 1280 px desktop:

- **The mobile header takes a third of the first screen.** The first event starts at about
  55 % of the height. Collapse the tagline and the two links into a menu, and drop the
  *Heute/Morgen* pills: the date bar already does that job.
- **The time column of untimed events is empty.** It looks broken and wastes a quarter of
  the row width. Show "ganztägig" or a muted dash, or let the title use the space.
- **The list is too sparse on a phone.** About 2.5 events fit on a screen, because every card
  has a chip row. On mobile, the coloured left border already shows the primary tag. Show
  the chips only when expanded. Phase 4 asks for "100 events at 375 px übersichtlich".
- **Routine items crowd the top.** On Sunday the list opens with four church services. See
  "Highlights" below.

## Features that would improve the offer

Ordered by value for effort:

1. **Weekend view.** A "Wochenende" pill (Fri–Sun on one page) answers the most common
   question. It's pure build work.
2. **Highlights / Regelmäßiges.** Split one-off events (concerts, festivals, markets) from
   recurring routine (services, courses, training, league games). Show routine items folded
   per day. A rule plus one boolean from the crawl (`recurring`) is enough.
3. **Weekly digest for distribution.** Generate "Diese Woche in Schongau" from
   `events.json`: text and an image card for a WhatsApp channel, Instagram or a newsletter.
   It needs one Haiku call a week. For a local calendar, distribution matters more than
   completeness.
4. **Share and add to calendar per event.** Add the Web Share API on the card and a one-event
   `.ics` download. Feeds per category ("only concerts") are a small step from the existing
   ICS code.
5. **"Neu" badge** for events first seen in the last 3 days. This gives returning visitors a
   reason to look.
6. **Event submission.** A structured "Event melden" form that opens a GitHub issue. The
   pipeline checks the issue like any other source. Altenstadt (11 events) and Hohenfurch (8)
   show where crawling alone is thin.

## Are these the right events?

I went through all 130 events in the 21-day window (15 Nov – 5 Dec).

**Too religious?** Partly. Religion is the primary tag of 19 events (15 %) and a tag of 36
(28 %, including concerts in churches). Part of that is the season: Volkstrauertag, Buß- und
Bettag, Ewigkeitssonntag and the 1st of Advent all fall in this window. Special occasions
are fine to show: church concerts, the Kinderbibeltag, the Taschenlampenführung. But about
half of the 19 are routine worship that a general visitor would not look for: an ordinary
Sunday service, Eucharistische Anbetung, Bibelgespräch, Abendgebet, Friedensgebet, a
choir "shaping the service". The Gedenkfeier für verstorbene Heimbewohner is for care-home
residents, which the excluded topics already forbid.

**Not real events.** About 9 entries (7 %) are offers, opening hours or appointments:

| Kind | Examples |
| --- | --- |
| Menu specials and discounts | "Halbes Hendl im Café Herzog", "Freier Eintritt für Ehrenamtskarte-Inhaber" |
| Opening hours or regular services | "Museum im Klösterle", "Traum- und Duftreise im Saunaland", "Abnahmetermin Jugendschwimm-Abzeichen" |
| Helper sessions and collections | "Basteln für den Peitinger Christkindlmarkt", "Weihnachtspäckchen für Notleidende" |
| Closed group, or a duplicate of a service | "Gedenkfeier für verstorbene Heimbewohner", "Vivace Chor: Gestaltung des Gottesdienstes" |

**Niche and routine.** About 25 more (20 %) are real, but of narrow interest:
- courses that need a booking (cooking, brewing and Italian courses, yoga, Honigbär-Yoga)
- weekly club and senior routines (AWO games afternoon, lunch for seniors, handicrafts)
- lower-league and youth matches: 3 bowling matches of the 3rd/4th men's and the women's
  team, EA Schongau 1b and U20

**Wrong tags.** "Schongauer Weihnachtszauber" (the Christmas market, 17 days) is tagged
*Senioren*. "Totenehrung am Kriegerdenkmal" is tagged *Kunst & Kultur*. These put events in
the wrong filters and colours.

**Overall:** about two thirds of the list is what a visitor wants. The rest is noise that
makes the list look smaller-town and church-heavy than the area is. Real events are not
missing, though; the problem is ordering and what gets in.

### What to change

1. **Let the crawl say what kind of entry it is.** Add a field `kind`: `event`, `routine`
   (regular worship, weekly groups, lower-league games), `course` (needs a booking) or `offer`
   (menu specials, discounts, opening hours, appointments). Drop `offer` in `to_event`. Show
   `routine` and `course` folded at the end of each day as "Regelmäßiges & Kurse" (this is
   the Highlights item above). It's a schema change plus a prompt change: migrate
   `events.json` (existing events default to `event`) and run a *Crawl check*.
2. **Define "event" in the prompt:** a dated happening open to the public with its own
   programme, which someone would plan to attend. Name the special services that count
   (Christmette, Festgottesdienst with a choir or orchestra, services with a concert,
   children's Bible days).
3. **Tag rules:** use *Senioren* only for events specifically for seniors, and *Religion* as
   the primary tag only for worship. A commemoration (Volkstrauertag) is not *Kunst & Kultur*.
   Have the merge's Haiku call re-check the primary tag of new events.
4. **A "Kein Event?" link on every card**, opening the feedback form pre-filled with the
   event id. Your own corrections then become data for item 1, and visitors can help.
5. **Keep watching the share.** Add a line to the job summary: religion as primary tag, and
   once `kind` exists, the share of `routine` / `course` / `offer`.

## Strategy: scaling to the 30-minute area

At about $0.28 per municipality per run, the concept's 44 municipalities would cost about
$100 a month at API prices, and the plan's limits would stop it first. Cost grows with the
number of *sources*, not of places. The way to scale is regional aggregators read as
structured data (pfaffen-winkel.de already covers the whole Landkreis), plus Instagram by
tier. Agent sweeps should be a last resort.

Grow the area step by step (Peißenberg, Hohenpeißenberg and Burggen next). After each step,
compare cost per new event in the job summary.

## Suggested order

1. Skip flyer pages in sweeps; whitelist backoff (items 1–2): small, immediate.
2. Instagram tiers, daily spreading and the usage header (items 1–3).
3. Mobile density: header, untimed column, chips.
4. Weekend view and the "Neu" badge.
5. Structured data and the `Accept` header for pages; then a Haiku test for `pages` calls.
6. Event quality: the `kind` field with the Highlights/routine split, the event definition
   and tag rules, the "Kein Event?" link; then the weekly digest.
7. Block-level diff for changed pages; `null` descriptions.
