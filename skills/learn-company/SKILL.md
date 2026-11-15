---
name: Learn a company
slug: learn-company
use: When asked to crawl, study, or learn a company's website and keep what it says about the company, its products, people, and places.
inputs:
  - company: the organization's name as memory knows it
  - url: the site to read (optional; found by search when missing)
access:
  - learn_page
  - hound.mcp_smart_fetch
  - hound.mcp_smart_crawl
  - hound.mcp_smart_search
  - exa.web_search_exa
  - exa.web_fetch_exa
  - browser.browser_navigate
  - browser.browser_snapshot
steps:
  - Confirm the company is known. Say which name you are learning about, exactly as memory knows it.
  - Find the site when no URL was given. The site must name the company; if the first result does not, search again rather than guessing.
  - Read the key pages first (home, about, products or shop, pricing, team, contact), then follow same-site links for products. Stop at 20 pages.
  - "Call learn_page on each page with the page's URL, its markdown, and this focus: the company's website, location, founders and team, products and their features, pricing, customers. Do not paraphrase pages into memory yourself; the tool decides what memory keeps."
  - Report what happened. Pages read; what was learned (matched names, facts written); what is waiting in Approvals; what was skipped and why.
validation:
  - Every page named in the report was passed to learn_page.
  - Nothing is claimed as remembered that the tool reported as skipped or held.
return: "A report: pages read, what was learned (matched names, facts written), what waits in Approvals, what was skipped and why."
approvals: []
band_width: 0.1
---
# Learn a company

The harness decides what memory keeps: `learn_page` writes only what a
page says about an organization, person, or product memory already
knows, and about new things the page ties to them. A page that names
nothing memory knows teaches it nothing, however interesting it is.

New people and loose discoveries are not lost: they wait for the user in
Approvals, and the tool's `held` list names them. Say so in the report
instead of writing them yourself.

If the first three pages all come back `unrelated`, stop: either the
site is not this company's, or memory does not know the company by the
name the site uses. Report that and ask which name to use.

Fetch with hound first (it needs no key and returns markdown); exa or
the browser when hound cannot read a page. Pass the markdown exactly as
fetched; the tool redacts secrets and caps the length itself.
