# Architecture notes

The pipeline accepts local JSON and RSS/XML fixtures through a narrow `SignalSource` interface. It never fetches a feed. Source-specific records are validated and normalized to the `Signal` model, which keeps a bounded metadata mapping and a source reference rather than transporting raw source payloads into every stage.

Normalization collapses whitespace, normalizes source type and tags, and converts parseable timestamps to UTC. Eligibility is explicit: short text, unsupported source types, and missing attribution yield reason records. Deduplication is intentionally conservative. It recognizes a matching source reference or the exact normalized title/body hash and records the retained signal, duplicate and comparison key. It makes no semantic similarity claim.

Classification comes from the typed `ContentProvider` interface. The offline provider returns bounded category and score fields. The fixed ranking formula is 35% relevance, 30% content potential, 25% evidence quality and 10% novelty; ties sort by stable signal ID. The brief and script are typed and retain source references. The fixture provider derives its content only from signal material and explicitly marks limits.

`DEPENDENCIES` is the stage graph. Each stage fingerprint is SHA-256 over canonical JSON containing its name/version, relevant configuration, and upstream artifact IDs and hashes. Artifacts use canonical JSON or deterministic media bytes and have SHA-256, byte size, type, path and producer metadata in `manifest.json`. The hashes provide integrity and cache provenance; they are not signatures or authentication.

Reuse requires prior success, a matching fingerprint, and all output files passing existence and SHA-256 checks. Failed outputs are not trusted. Downstream fingerprints include only direct stage artifacts, so a render-template change affects render and package; ranking changes flow from rank onward; source changes flow through the graph. Writes use a temporary sibling file, flush and atomic replace.

Failures preserve upstream artifacts and record bounded error type/message. Dependencies become blocked. Resume reloads the manifest, recomputes expected fingerprints, verifies files, reuses valid successful work and retries invalid or failed stages. Execution timestamps are manifest metadata and do not enter semantic fingerprints.

Mock TTS writes a mono 8 kHz WAV tone pattern whose duration follows script estimate. Captions are deterministic SRT built from bounded text chunks with sequential intervals. The mock renderer emits a structured placeholder. The optional FFmpeg renderer runs via an argument array, checks availability and output, and records bounded process details. It is not needed by tests or the demo. Package records references and hashes at the local artifact boundary; there is no publishing capability.

The filesystem manifest is the persistence model, not a transactional distributed scheduler. A process crash between artifact and manifest replacement can leave an orphan file, which is harmless and overwritten by a retry. This demonstration uses synthetic data and deterministic mock classification; it does not establish factuality, scale, model behavior, or production media quality.
