# Document AI (Full setup opt-in)

The default reader is Gemini multimodal with a JSON response schema at
temperature 0: no processor to train, works across document types, returns
page + verbatim quote + confidence per field.

[Document AI](https://cloud.google.com/document-ai) is a good fit when
volumes are high, layouts are stable, and you want processor-native
confidence scores and bounding boxes (Form Parser, Custom Extractor, or
specialized processors such as Invoice / Bank Statement / Lending).

## How to plug it in

There is one swap point: `scripts/extraction.py::extract_fields(data, mime,
doc_type, spec)`. It must return

```python
[{"name": str, "value": str, "page": int,  # 1-based within `data`
  "quote": str, "confidence": float}]
```

for the fields listed in the pack's `schema.json`. Everything downstream
(quote verification, lanes, checks, review, approval, output) is unchanged.

`extractor: documentai` in `design-spec.md` (→ `EXTRACTOR=documentai`) is a
stub that raises `NotImplementedError` until you implement it, e.g.:

1. Create a processor (ask first; it is a cloud resource) and note its
   `projects/<p>/locations/<loc>/processors/<id>`.
2. Add `google-cloud-documentai` to `pyproject.toml`.
3. In `extract_fields`, call `DocumentProcessorServiceClient().process_document`
   with the sliced PDF, map entities to the pack field names
   (`entity.type_` → field, `entity.mention_text` → value/quote,
   `entity.confidence`, `page_anchor.page_refs[0].page + 1` → page).
4. Keep splitting on Gemini, or use a Document AI splitter/classifier
   processor and adapt `classify_and_split` the same way.

Document AI processors are regional (`us`, `eu`): align with
`GOOGLE_CLOUD_LOCATION` for data residency.
