# MVP Capacity Estimate (Models + Parts Only)

Scope of this estimate:
- Includes only `models`, `parts`, and `model_parts`.
- Excludes comments/Q&A/docs/chunks/embeddings.
- Based on crawled samples in `sample/requested/`.

## 1) Sample Basis
Observed from sampled model pages:
- Dishwasher models: `n=29`
  - average parts/model: `136.5`
  - median: `152`
  - range: `7` to `155`
- Refrigerator models: `n=49`
  - average parts/model: `220.7`
  - median: `236`
  - range: `24` to `325`
- Combined working average for planning: **`~189 parts/model`**

Sample files:
- `sample/requested/model_parts_sample_strict.json`
- `sample/requested/refrigerator_model_parts_sample.json`

## 2) Storage Assumptions
Practical per-row storage (data + indexes, rounded):
- `models`: `~0.5 KB/row`
- `parts`: `~0.8 KB/row`
- `model_parts`: `~0.2 KB/row`

Part reuse assumption for MVP:
- Each unique part is linked to roughly `5-8` models on average.

## 3) Count and Size Estimates

| MVP Models | `model_parts` Rows (`models * 189`) | Unique Parts (range) | Approx DB Size |
|---|---:|---:|---:|
| 2,000 | 378,800 | 47k-76k | 150-190 MB |
| 5,000 | 947,000 | 118k-189k | 370-500 MB |
| 10,000 | 1,894,000 | 237k-379k | 740 MB-1.0 GB |

## 4) Recommended MVP Target
Use **5,000 models** (dishwasher + refrigerator):
- about `~1M` `model_parts` mappings,
- about `~120k-190k` unique parts,
- about **`0.4-0.5 GB`** DB footprint (without vector/comment data).

## 5) Notes
- This is a sample-driven estimate, not a full-site census.
- Real size will vary with:
  - model mix (refrigerator pages have higher part counts),
  - index strategy,
  - text field lengths and update history.
- Recompute after first full ingestion batch for tighter planning.
