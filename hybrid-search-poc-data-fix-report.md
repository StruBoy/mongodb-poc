# Hybrid-Search PoC — Data-Fix Session Report

## Starting state

The three sidebar demo queries didn't differentiate the search modes:

| Query | Old behaviour |
|---|---|
| Marathon training | Both keyword and semantic returned relevant footwear — hybrid pane looked the same. |
| TrailMaster | Both keyword and semantic returned TrailMaster products — hybrid pane looked the same. |
| Long flights | Both modes returned travel-relevant electronics — hybrid pane looked the same. |

The hybrid pane therefore had no visible advantage over the other two and the architectural argument ("hybrid is the safe default") didn't land.

Two implementation choices caused the tie:

1. `src/embed.py:product_to_text` included the title (which begins with the brand) and an explicit `Brand: {brand}` segment in the embedded text. Voyage-3 embeddings contained the brand string verbatim, so semantic search could resolve brand-only queries.
2. `data/generate.py` told Claude descriptions should be "evocative but not use the exact product type name" — but did not ban surface-level intent vocabulary. Descriptions of marathon racing shoes still contained `marathon`, `race`, `mile`, `endurance`. Keyword BM25 latched onto those, so intent queries didn't fail keyword either.
3. (Discovered later) Claude received the brand name as part of the prompt. Even when descriptions didn't mention the brand verbatim, the brand string biased the description's *theme* — `TrailMaster trail running shoe` produced unusually trail-emphatic prose, which the embedding then carried.

## What changed

### `src/embed.py`
`product_to_text` now embeds only `description + category`. Title (which begins with the brand) and the explicit `Brand: X.` segment are both excluded from the vector space. Atlas Search keyword indexing is unaffected — it reads the document fields directly.

### `data/generate.py`
- Added per-category `BANNED_WORDS` map (footwear only): `marathon`, `race`, `racing`, `racer`, `mile`, `miles`, `26.2`, `10K`, `5K`, `long-distance`, `long distance`, `endurance`, `ultra`, `ultramarathon`. Other categories left unconstrained so the long-flights demo retains natural keyword anchors.
- The Claude prompt now includes a strict no-use rule for the banned words for footwear products only.
- The prompt no longer passes the brand to Claude — only `(product_type, category)`. Tuple shape changed from `(product_type, brand, category)` to `(product_type, category)`. Eliminates both literal brand mentions and brand-themed prose.
- Defense-in-depth post-processing on every Claude response: `_strip_brand_mentions` removes any brand string that slipped through, and `_scrub_banned_words` strips whole-word occurrences of any banned term. Word boundaries matter here — substrings like `mileage` and `gracefully` are left intact (BM25 tokenises them separately and they don't match query tokens for `mile` / `race`).
- Stopped popping `_product_type` from inserted documents — the smoke test now uses it for subtype assertions.
- Fallback description (when a Claude batch fails) no longer references `_product_type` directly, since it can contain banned words.

### `app.py`
Replaced `DEMO_QUERIES`:
- Marathon racing: `"racing 26.2 miles"` (was `"shoes for running long distances"`)
- Brand search: `"TrailMaster"` (unchanged)
- Long flights: `"comfortable for long flights"` (unchanged)

The marathon query was originally `"footwear for racing 26.2 miles"` but `footwear` appears naturally in 12% of footwear descriptions, so keyword still found anchors. Dropping the `footwear` token reduced keyword to zero hits.

Default text-input query value updated to match.

### `scripts/smoke_test.py`
Fully rewritten. Old version asserted only "all three modes return at least one result." New version runs all three demo queries and asserts mode-by-mode differentiation:

| Demo | Assertion |
|---|---|
| Marathon | keyword surfaces ≤ 1 marathon-racing/trail-running shoe; semantic surfaces ≥ 3 |
| Brand | keyword returns 5/5 TrailMaster; semantic returns ≤ 2 TrailMaster |
| Long flights | both modes return ≥ 1 headphones/earbuds |

Hybrid must be non-empty for all three. Each failure includes a hint at the likely root cause (banned-words leakage, brand-in-embedding leakage, etc.).

### `scripts/verify_catalog.py`
- `_product_type` added to `REQUIRED_FIELDS` (was previously a leak-warning).
- New check 6b: footwear banned-words leakage. Counts the fraction of footwear descriptions that contain any banned token; warns above 2%. Surfaces prompt-rule violations from Claude.

### `plans/02_DigitalNative_Hybrid_Search.md`
Phase 5 demo prose rewritten. Added a "Demo trade-offs to flag if asked" section noting the two deliberate departures from production behaviour (brand stripped from embedding, footwear descriptions banned-word constrained, brand stripped from Claude prompt).

### `README.md`
Smoke-test description updated. Hybrid-search demo flow rewritten with three numbered steps matching the new queries plus a trade-off footnote.

## Iterations and what each one fixed

The fix took three regenerations:

| Step | Change | Result |
|---|---|---|
| 1. First regen | Added banned-words rule for footwear; stripped brand from `product_to_text`. | Marathon: keyword still surfaced 4/5 running shoes (query `"footwear for racing 26.2 miles"` matched on `footwear`). Brand: semantic surfaced 5/5 TrailMaster — descriptions still leaked the brand verbatim (7/128) and were thematically trail-emphatic. Long flights worked. |
| 2. Drop `footwear` token from query, regen with brand-blind Claude prompt | Pass only `(product_type, category)` to Claude; add `_strip_brand_mentions`. | Brand: semantic dropped to 2/5 TrailMaster ✓. Long flights ✓. Marathon: keyword still surfaced 3/5 running shoes — 14 footwear descriptions (~2%) still contained banned words like `racing`, `mile`, `miles` despite the strict prompt rule. |
| 3. Surgical scrub | Word-boundary regex strip of banned tokens on the 14 leaky descriptions; re-embed only those (one Voyage call). | Marathon: keyword 0/5 ✓. All three assertions pass. |

After the surgical scrub, `_scrub_banned_words` was added to `data/generate.py` so any future regen automatically strips banned tokens at insert time and won't need a manual fixup.

## Final test results

```
[1/3] Marathon racing — query='racing 26.2 miles', category=footwear, max_price=400
      keyword running-shoe hits:  0/5 (expect ≤ 1)  ✓
      semantic running-shoe hits: 5/5 (expect ≥ 3)  ✓

[2/3] Brand search — query='TrailMaster', category=footwear, max_price=500
      keyword TrailMaster hits:  5/5 (expect = 5)  ✓
      semantic TrailMaster hits: 2/5 (expect ≤ 2)  ✓

[3/3] Long flights — query='comfortable for long flights', category=electronics, max_price=800
      keyword headphone/earbud hits:  2/5 (expect ≥ 1)  ✓
      semantic headphone/earbud hits: 5/5 (expect ≥ 1)  ✓

[OK] All three demo queries differentiate as expected.
```

`scripts/verify_catalog`: 2500 docs, all categories balanced (625 each), all 9 required fields present, 1024-dim embeddings; banned-words leakage warning shows 14/625 substring leaks but those are inside compound words (`mileage`, `Ultralight`, `gracefully`) that BM25 tokenises separately and won't match query tokens.

## Cost

Two full regens at ~$0.40 each, plus ~$0.001 in Voyage re-embeds for the 14-doc surgical scrub. Total ~$0.80 + ~25 min wall clock.

## Files modified

```
hybrid-search/
├── app.py                         ← new DEMO_QUERIES, default query value
├── data/generate.py               ← banned-words rule, brand-blind prompt, _strip_brand_mentions, _scrub_banned_words
├── src/embed.py                   ← product_to_text drops title and brand
└── scripts/
    ├── smoke_test.py              ← rewritten: 3 differentiation assertions
    └── verify_catalog.py          ← _product_type now required; check 6b banned-words leakage
plans/02_DigitalNative_Hybrid_Search.md  ← Phase 5 prose + trade-offs section
README.md                                 ← demo flow + smoke-test description
```

## Verification run book

```bash
cd hybrid-search && source venv/bin/activate
python -m scripts.verify_catalog   # 2500 docs, banned-words check
python -m scripts.smoke_test       # 3/3 differentiation assertions pass
streamlit run app.py               # click each sidebar button to confirm visually
```

## Trade-offs that survive into the demo

- Footwear descriptions are deliberately scrubbed of intent vocabulary so the keyword-fail demo lands on a small corpus. In a real catalog you would *want* those words present.
- The Claude prompt does not receive the brand name, so descriptions are brand-agnostic. In production you would normally vary copy per brand for differentiation.
- `product_to_text` excludes the brand from the embedded text. In production you would normally include it so semantic also resolves brand-affiliated queries; the demo strips it to make the keyword-wins case unambiguous.

All three are noted in `plans/02_DigitalNative_Hybrid_Search.md` under "Demo trade-offs to flag if asked" so a presenter can answer the architectural question honestly.
