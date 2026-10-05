# Spec: a static catalogue site for a large digitised archival series

This document describes how this site is built, in general terms, so another project can reuse the design for a different collection. It covers:
- the data pipeline;
- the file formats;
- the single-page app;
- the build scripts;
- the conventions that keep it maintainable.

The example throughout is this site: U.S. State Department decimal file 894 (Japan, 1930–49), about 1,900 files, 115,000 pages and 24,000 documents.

---

## 1. What the site is

The site catalogues a series of scanned **files**. A file is a bundle of papers filed under one subject: one PDF, often hundreds of pages. Each file contains many **documents**, or sub-files: a telegram, a despatch, a memorandum, a clipping.

The site lets a reader:
- browse and search the files, and each file's documents, using a catalogue written by language models from OCR text;
- search the full OCR text of every page;
- read curated **Spotlight** documents: page images beside raw OCR and a cleaned transcription;
- follow curated **Sets**, which are rule-built groupings of documents across files;
- mark and tag items for the current browser session, and export them.

Page images are not hosted on the site. They live on the Internet Archive (archive.org), and every page reference links there.

**Design principles**
- **A static site only.** Plain HTML, JS and JSON files, with no server code and no database server. It can be hosted anywhere, including a shared web host or GitHub Pages.
- **One `index.html`.** All the views are in one file, with hash routing. There's no framework and no build step for the front end.
- **Data as generated JS files.** These are `data.js`, `sets.js` and `spotlight-data.js`, which set `window.X = …`. They are rebuilt by Python scripts from source JSON.
- **The source of truth is the per-file JSON** that the cataloguing models write. Everything served to the browser is derived from it and can be regenerated.
- **Be honest about machine text.** Everything a model wrote carries a "by LLM" pill. Raw OCR and cleaned transcriptions are labelled with the engine or model that made them.
- **No personal data anywhere:** not in commits, request headers or file metadata.

---

## 2. Repository layout

```
site/                         (the public git repo; most of it is deployed)
  index.html                  the whole app: CSS + HTML shell + JS (~950 lines)
  data.js                     window.CATALOG = [file records]            (generated)
  sets.js                     window.SETS = [set definitions + items]    (generated)
  spotlight-data.js           window.SPOTLIGHT = [entries]               (generated)
  catalog.sqlite              the same catalogue as a SQLite database    (generated; deployed, not in git)
  downloads/                  bulk downloads: OCR text (.jsonl.gz) and a copy of the SQLite file
  pagefind/                   full-text search index                     (generated; deployed, not in git)
  spotlight/
    urls.txt                  Spotlight list: one link per line, "## Category" headings
    clean/<file>__<n>.txt     cleaned transcription per Spotlight document (optional .ja.txt)
    img/<file>/<page>.jpg     page images for Spotlight documents (150 dpi grayscale JPEG)
    transcribers.tsv          which model made each cleaned transcript
  json/<file id>.json         catalogue output for small files (one request per file)
  json-merged/<file id>/      catalogue output for large files: sNNN.json per section + file.json
  build_db.py                 json/ + json-merged/ + OCR text -> catalog.sqlite + data.js
  build_sets.py               catalogue -> sets.js (rule-based selections)
  build_spotlight.py          spotlight/ + catalogue + OCR -> spotlight-data.js
  catalog.py, catalog_merged.py   cataloguing drivers (call the model CLIs)
  prompt.txt, schema.json     cataloguing prompt and JSON schema
  fulltext-build/             prepare.py (OCR -> records.jsonl), build.mjs (Pagefind), make_bundle.py
  llms.txt, robots.txt        point crawlers and agents at the bulk downloads, not the index
  spec.md                     this document
```

These live outside the repo: the PDFs, the OCR text (`../ocr/*.txt` and `../merged/ocr*/…txt`, one `=== Page N ===` header per page), and the upload lists (`upload.csv`, `merged-items.csv`).

---

## 3. Data pipeline

```
PDF scans ──OCR──> page text (=== Page N === blocks)
                 │
                 ├──LLM cataloguing──> json/ or json-merged/ (per file / per section)
                 │                          │
                 │                    build_db.py ──> catalog.sqlite, data.js
                 │                          │            ├─ build_sets.py ──> sets.js
                 │                          │            └─ build_spotlight.py ──> spotlight-data.js
                 │                          │
                 └──prepare.py (+ catalogue) ──> records.jsonl ──build.mjs──> pagefind/
```

### 3.1 OCR
- Each PDF is OCR'd into a text file with `=== Page N ===` before each page. N is the PDF page number, which is the numbering used everywhere.
- Two engines were used: Mistral OCR for the smaller files, and PaddleOCR locally for the large merged files. The engine is recorded per file and shown to readers.

### 3.2 Cataloguing (inference)
- **Small files** (`catalog.py`): one model request per file returns overview, interest, keywords, people, places and a list of sub-files. Each sub-file has title, type, start and end page, date, from, to, about, summary and keywords.
- **Large files** (`catalog_merged.py`):
  - the text is split into sections of at most 80 pages or 100,000 characters;
  - each section is catalogued on its own. A `continues_previous` flag joins documents that cross a section boundary;
  - a final request writes the file-level overview, keywords and interest note from the section results.
- **Structured output:** strict JSON schemas, so every field is always present.
- **Backends:** model CLIs called with `-p` / `exec` (Gemini via Antigravity, OpenAI Codex). Workers are safe to run in parallel, using per-task lock files and atomic writes. Each worker has quota awareness: it pauses until the quota resets instead of failing. A `--only list.txt` option limits a run to a chosen batch.
- **Length rules** (in the prompts):
  - 1-page cover sheets: summaries of 20 words or fewer;
  - other 1-page documents: up to 50;
  - documents under 5 pages: up to 50, aiming for 20;
  - "interesting" notes: 10 words or fewer for 1-page items, 20 otherwise.
- A file that isn't catalogued yet still appears on the site as a **placeholder**: its title and page count, plus a note. Full-text search covers it.

### 3.3 Building the catalogue (deterministic, no inference)
`build_db.py`:
- **Merging:** merges each file's sections, and joins documents that span sections.
- **Mention counts:** counts people and places in the OCR text, using every spelling the model reported. Counts are done locally; the model's own counts aren't used.
- **Output:** writes `catalog.sqlite` (tables `files`, `subfiles`, `people`, `places`, `file_keywords`, `subfile_keywords`) and `data.js`. Both are written to temporary files and swapped in, so a page loaded mid-build never sees half a file.
- **Speed:** mention counts and page totals are cached in `.counts-cache.json`. Each entry is keyed by the OCR file's path, size and modification time plus the exact name list. Cache misses are counted in parallel on all cores but two.
  - A full recount of 115,000 pages takes about 3 minutes on 10 cores.
  - An incremental rebuild takes about 20 seconds.

**Record shape in `data.js`.** Keys are short to keep the file small.
```
{ id, ia, iaf,            // site id, archive.org item, file within the item (split PDFs)
  f, t, dec, n, d, pg,    // PDF name, title, decimal number, file number, date, pages
  o, i, k,                // overview, "interesting" note, keywords
  pl: [[name, count]], pe: [[name, role, count]],
  sub: [{ t, ty, p:[start,end], d, fr, to, a, s, k }],   // documents
  sk: 1 }                 // present only on placeholder (uncatalogued) files
```

**Short IDs.** A file's ID is its State Department decimal number, a hyphen and its file number, which is unique, plus `-N` for part N of a split PDF: `894A.00-1181`, `894.00-1645-2`. A document's ID adds `.` and its position in the file: `894A.00-1181.21`. When typing an `in:` filter, the decimal part can be left out (`in:1181.21`). Shared links and the full-text `in:` filter use these IDs.

### 3.4 Sets (`build_sets.py`)
- Each set is a rule over document titles, types and dates, for example: cabinet changes, Diet sessions, the February 26 Incident, biographical sketches, translated laws, radio intercepts, and bi-weekly intelligence summaries.
- A "month" mode groups copies of the same monthly report together and offers "alternative version" links.
- Single-page documents are left out of every set.
- Each item points to an exact document anchor, `#d/<file>/doc/<n>`.

### 3.5 Spotlight (`build_spotlight.py`)
- **The list:** `spotlight/urls.txt` holds site links or archive.org page URLs. `## Category` headings group them, and line order gives the "date added" order.
- **The build:** for each entry it takes the page range from the catalogue, the raw OCR, the cleaned text (and the Japanese reconstruction if present), and the transcriber label. These go into `spotlight-data.js`.
- **Images:** pre-extracted with `pdftoppm -r 150 -gray -jpeg -jpegopt quality=70`.
- **Transcription conventions** (copied into every transcription brief):
  - one `=== Page N ===` per page;
  - paragraphs follow the original. A blank line separates them; no indentation is typed. Line-by-line blocks (letterheads, addresses, signatures, lists) stay one item per line;
  - `[square brackets]` for editorial notes; `[illegible]`, `[word?]`, `[?]` for figures that can't be read. **Never guess a figure**;
  - footnotes: a `[^N]` marker in the text, and `[^N]: text` lines at the foot of the page;
  - tables as `a | b | c` rows;
  - one `[Note: …]` per page for faint or illegible areas.
- **Who reads the scans:** transcribers read the images themselves, with no OCR tools. They may be Claude, Codex or Gemini agents. Long documents are split into chunks of about 12 pages, and each chunk is transcribed separately and then joined. Every join is checked for continuous page numbers.

### 3.6 Full text (`fulltext-build/`)
- **`prepare.py`** writes one record per OCR page. Each record carries:
  - **meta:** file title, page, file ID, archive.org item and file, OCR engine, and document number;
  - **filters:** `subject`, `year` (the document's own year where known, else the file's range), `doc` (file), `sub` (`<file>#<doc no.>`, with `#0` for pages outside any document), `type` (a coarse document type) and `decimal`;
  - **sort:** `date`, as `YYYY-MM-DD|file|page`.
- **`build.mjs`** runs Pagefind's Node API (`addCustomRecord`) into `pagefind/`. A full build is a heavy job, so tell the user before running one.
- **`make_bundle.py`** writes the OCR text bundle for download. `llms.txt` and `robots.txt` steer crawlers to the bundle instead of the index.

---

## 4. The app (`index.html`)

### 4.1 Routing (`location.hash`)
| Hash | View |
|---|---|
| `#` | Files list (search, subject and year filters, sort, Files/Documents toggle) |
| `#d/<file>` | File page; `/pg/<n>` highlights the document on page n; `/doc/<n>` jumps to document n |
| `#d/<file>/q/<words>` · `/pe/` · `/pl/` · `/k/` | Search inside one file (text, person, place, keyword) |
| `#f/pe/<name>` · `#f/pl/` · `#f/k/` | Files list filtered by one person, place or keyword |
| `#people` · `#places` · `#keywords` | Facet lists |
| `#fulltext` · `#fulltext/<query>` | Full-text search (shareable query) |
| `#sets` · `#<set id>` | Sets index and one set |
| `#spotlight` · `#spotlight/<category>` | Spotlight lists |
| `#s/<entry>[/p/<page>][/v/<modes>]` | One Spotlight document. The page and the shown modes are kept in the address as the reader scrolls and clicks |
| `#marked` · `#tags` · `#tags/<tag>` | Session marks and tags |
| `#about` | About page, with downloads |

`history.replaceState` keeps addresses up to date without adding history entries.

### 4.2 Catalogue search syntax (Files list and file pages)
- Words and `"phrases"` match anywhere in a file (all of them must match).
- Field prefixes: `name:` / `title:` (titles of files and documents), `person:` / `people:`, `place:` / `location:`, `keyword:`, `summary:`, `from:`, `to:`, `year:1936`, `year:1930-1935`.
- A per-file "blob" and per-field blobs are built once when the page loads.
- **Highlights:** yellow in headlines, light blue in summaries.
- Under each file, every matching document is listed as a link to its anchor.
- The **Documents** toggle lists every document on its own. It has the same filters and sorting, with an "In file:" link back to the parent file.

### 4.3 Full-text search syntax and views
- **Syntax:**
  - words and `"phrases"`;
  - `-word` to exclude;
  - `A OR B`;
  - filters `year:`, `type:`, `subject:`, `decimal:`, `in:<file id>` / `in:<doc id>`. Put `-` before a filter to exclude it.
- **How OR and NOT run:** OR and NOT are done in the browser by running several Pagefind searches and combining the results.
- **Grouping:** by **document** (the default), by page, or by file. A document card shows:
  - its title, date, type and LLM description, with a ★ Spotlight badge linking to the transcription;
  - how many pages match;
  - the top three pages by relevance;
  - "show all N pages", which lists every matching page in page order.
- **Extras:**
  - a summary line: "N pages in M documents across K files, years";
  - a clickable year histogram;
  - a type filter;
  - **Export CSV** (up to 5,000 pages, built in the browser).
- **Entry points:** every file and document entry has a "Search full text" link that opens the search limited to that item.

### 4.4 Spotlight document view
- **Views:** up to two of Raw OCR, Clean (or Japanese) and Page image, side by side, page by page, inside a scrolling panel.
- **Navigation and downloads:** a jump-to-page menu, and a Download menu. The menu offers TXT of the clean, raw or Japanese text, with a citation header, and a PDF of the page images. The PDF is assembled in the browser by embedding the JPEGs (DCTDecode) in a minimal PDF writer, so no library is needed.
- **Rendering:** clean text is turned into paragraphs (indented prose; flush-left headings, short lines and line-by-line blocks), tables and footnotes.
- **Labels:** a warning box names the model that made the transcript and the OCR engine.

### 4.5 Marks and tags (session only)
- **Selecting:** Option-click selects an item, Shift-Option-click selects a range, and Ctrl-Option-click toggles items one at a time.
- **Acting:** floating buttons mark the selection or tag it.
- **Storage and export:** everything is kept in `sessionStorage`, so it lasts for the session only. The View Marked and View Tags pages can export to CSV or JSON and import JSON; imported JSON is sanitised.

### 4.6 Visual conventions
- **Theme:** colour tokens on `:root`, with dark mode via `prefers-color-scheme` and a manual toggle. A file page tints the whole page.
- **Page-length pills:** grey for 1 page, light pink for 2–30, magenta for 31–100, dark purple for over 100.
- **Era date bubbles:** pre-war, Pacific War, Occupation.
- **"by LLM" pill:** small, light grey, after every machine-written summary.
- **Layout:** works at phone width; the sticky search bar stacks on small screens.

---

## 5. Operations

- **Rebuild after cataloguing:**
  `python3 build_db.py && python3 build_sets.py && python3 build_spotlight.py && cp catalog.sqlite downloads/…`
- **Rebuild full text** (heavy):
  `cd fulltext-build && python3 prepare.py && node build.mjs && python3 make_bundle.py`
- **Deploy:** upload `index.html`, `data.js`, `sets.js`, `spotlight-data.js`, `catalog.sqlite`, `downloads/`, `pagefind/` and `spotlight/img/`. The `json*` folders, `spotlight/clean/` and the scripts are sources, and the site doesn't load them.
- **Git:** commit and push as you go. Generated large outputs (`catalog.sqlite`, `pagefind/`, `downloads/`) are in `.gitignore`.
- **CPU:** heavy jobs may use up to about 80% of the CPU. Leave headroom, and don't run two very heavy jobs at once.
- **Delegating work to other model CLIs:**
  - write a self-contained brief with the exact paths and an explicit "write only this file" rule;
  - test one small job first;
  - run at most a few jobs at a time;
  - check every output: page coverage, exit codes, file existence (some CLIs exit 0 even when they produced nothing);
  - spot-check results against the scans yourself.

---

## 6. Adapting the design to another collection

1. Produce per-page OCR text with `=== Page N ===` headers and a stable file ID per PDF. Keep the page images somewhere public with page-addressable URLs.
2. Write the cataloguing prompt and schema for your material:
   - file level: overview, interest, keywords, people, places;
   - document level: title, type, pages, date, from, to, about, summary, keywords.
   Run it in sections for long files.
3. Adapt `build_db.py`:
   - the ID scheme;
   - the subject classes, derived here from the decimal number;
   - title-date parsing.
   Keep the output record shape so `index.html` works unchanged.
4. Replace the link helpers (`IA`, `IAP`, `iaBook`) with your image host's URL pattern.
5. Write your own set rules and Spotlight list, or leave them empty; the views cope with empty data.
6. Build the Pagefind index with the same filter names (`subject`, `year`, `doc`, `sub`, `type`, `decimal`) so full-text grouping and filters work.
