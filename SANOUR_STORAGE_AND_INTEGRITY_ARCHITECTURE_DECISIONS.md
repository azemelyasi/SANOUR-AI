# SANOUR Storage and Integrity Architecture Decisions

**Document status:** Decision record in draft. The Stage 3 design rules identified in section 4 were approved for the Stage 3 scope, as recorded in the owner-provided project history. Other claimed Stage 3 approvals are not independently verified here. All other choices are proposed or unresolved. No implementation is authorized by this document.
**Date:** 2026-11-10
**Revision:** r3 (documentation-only corrections to the owner-provided r2 text)

---

## 0. How to read this document

**Decision status (exactly one per decision record):**
- **Confirmed historical fact:** a documented or verified statement about what exists or was reported. Not a choice.
- **Proposed decision:** a recommended direction awaiting owner approval. Not approved.
- **Unresolved question:** an open choice or missing evidence. Not decided.
- **Explicitly approved decision:** approved by the project owner for a stated scope. Each one names its approval source.

**Evidence classes (attached to each finding):**
- **[SRC] Source-code observation:** read directly from the accepted Stage 3 archive. Findings inherited from r2 are reported source observations unless separately checked in the r3 review.
- **[TEST] Test evidence:** a repository test that exists, with its name. Reported results are cited as reported, not independently re-run.
- **[ADHOC] Ad hoc experiment:** a script reportedly run during Stage 3 on temporary database files, outside the repository test suite. Not a test. Not repeated in this revision.
- **[DOC] Documented statement:** what a document says, including the owner-provided project history or r2 where identified. Not verified against code unless marked.
- **[INF] Inference:** a conclusion drawn from the above. Must be checked before it is relied on.
- **[UNRESOLVED]:** evidence needed and not yet available.

---

## 1. Scope and source archive identity

- **Accepted Stage 3 working copy:** `SANOUR_AI_10.54-C3.3-stage3-workingcopy-r1.zip`, SHA-256 `b5a30674eaf774a5a04677ab8c638001c5b68c7fd508176166f0cdb296aa540f`, 275 files. [DOC: owner-provided project history; SHA-256 independently computed from the Stage 3 attachment during r3 preparation; 275 ZIP entries observed, file count not independently checked]
- **Reported Stage 3 results:** full suite 2272 tests, 0 failures, 0 errors, 62 skipped; mutation 11/11 detected under the repository tool; storage and WAL regression 146 passed. [DOC: reported results; tests not re-run for r3]
- **Stage 2 reference:** `SANOUR_AI_10.54-C3.3-stage2-workingcopy-r2.zip`, SHA-256 `f3240dc25c48068907e56917cb55762d967adc29ccca60a34450ae954badab42`. [DOC: r2; the subsequently uploaded Stage 2 attachment was independently hashed during r3 preparation and matched this r2 hash; 249 non-directory files observed. The earlier owner-provided project summary instead recorded `f3240dc25c48068907e56917cc55762d967adc29ccca60a34450ae954badab42`; this discrepancy remains unresolved. The extracted-tree comparison reported by r2 was not independently repeated.]
- **Official Baseline 10.7:** the owner reports that it was not present in Claude's previous working environment. No independent verification of Baseline 10.7 is claimed here. This document does not modify or promote it. [DOC: owner-provided project history]
- **Scope:** storage ownership, cross-subsystem consistency, sink backup and recovery, verification and migration separation, Evidence Ledger rollout, usage append-only enforcement. It does not change ErrorCode values, public API, Baseline 10.7, or any database schema.

---

## 2. Current documented architecture

The following are documented statements reported in r2, not independently re-verified against those documents during r3 preparation.

| Topic | Documented statement | Source |
|---|---|---|
| Usage and Governance storage | "Usage Ledger record (same file, own tables)" | [DOC] `docs/GOVERNANCE.md:9` |
| Audit sink location | Default `<governance database>.sink`. Production must place it on durable storage. It must be a separate file from the governance database. | [DOC] `docs/GOVERNANCE.md:79` |
| Sink ownership | Separate migration and retention policy, separate from the governance database | [DOC] `docs/AUDIT_SINK_BOUNDARY.md:26` |
| Cross-database transactions | "two databases, so there is no cross-database transaction. The outbox bridges the gap." Delivery is at-least-once, deduplicated at the sink. | [DOC] `docs/GOVERNANCE.md:83` |
| Reconciliation | Governance vs usage ledger, governance vs sink and others are checked as pairs. The reconciler never writes and never runs migrations. | [DOC] `docs/GOVERNANCE_STATE_MACHINE.md` section 9; reconciler module docstring |
| Settlement | Two transactions: record intent, then apply | [DOC] `docs/GOVERNANCE.md:40-41` |
| Usage storage | Durable local SQLite. `Runtime.build(..., usage_ledger_path=...)` requires it. | [DOC] `docs/USAGE_LEDGER.md:6` |
| Evidence Ledger | "no production module yet constructs a ledger"; readiness and governance "not wired to a live production path yet" | [DOC] `docs/INTEGRATION_10_54C3.md:12, 72` |
| Guarded opening | `open_guarded_ledger` is "the sanctioned way to open a production ledger" | [DOC] `docs/INTEGRATION_10_54C3.md:8` |
| Backup scope | Backup, restore and verify describe one ledger file and its manifest. The sink is not mentioned. | [DOC] `docs/BACKUP_RESTORE_10_54C1.md` |
| Readiness and governance connection | Readiness is not called by any runtime path. Governance has no integrity connection. | [DOC] `docs/ARCHITECTURE_REVIEW_10_54C31.md` section 4 |

**Prior document search:** r2 reported finding no formal requirement for four physically independent storage files in the accepted archive's repository documents. This is a missing formal source record, not proof that the conversation-level decision never existed or lacked force. Its precise terms and formal source remain unresolved (D-00). [DOC: r2 search report; not repeated in r3]

**Prior label search:** r2 reported not finding the label "C3.3" in repository documents. [DOC: r2 search report; not repeated in r3]

---

## 3. Current implementation facts

Items below are source observations reported in r2 from the accepted Stage 3 tree, unless marked otherwise. The r3 preparation separately inspected the relevant `Runtime.build`, backup, first-open lock and Ledger constructor source excerpts; it did not execute them or open any database.

**Runtime composition**
- `Runtime.build()` passes one `usage_ledger_path` to `SqliteUsageRepository` and to `GovernanceStore`. Thus, **if invoked**, this code configures Usage and Governance to use the same physical file F; the sink defaults to F.sink. [SRC `composition/runtime.py`; sink default reported from `governance/admission.py` in r2]
- `Runtime.build()` has no internal caller in `src/` or `tools/`, according to the prior source review. External deployment use is unknown. [DOC: r2 source-review report; deployment use UNRESOLVED]

**Transactions**
- Governance settle: its own connection and transaction (`governance/store.py`). [SRC, reported in r2]
- Usage append: its own connection and its own `BEGIN IMMEDIATE` per append (`ledger/sqlite_repository.py`), called after the core result (`composition/runtime.py`). [SRC, reported in r2]
- Sink delivery: its own connection and transaction (`governance/sink.py`). [SRC, reported in r2]
- Consequence: even when sharing F, the current separate transactions provide no cross-subsystem atomicity. [INF from reported source observations]

**Backup, restore, verify**
- `create_backup(source_path, dest_dir, ...)` takes its source path from the caller, snapshots that one database file, names the backup member `ledger.sqlite`, and records one file in the manifest. Its verification uses the Evidence Ledger `Ledger` class and ledger-specific schema, stream and integrity checks. [SRC `backup/archive.py`, inspected for r3]
- The name `ledger.sqlite` identifies the backup member, not the identity of the source supplied by deployment. The code is Ledger-oriented, but the actual caller-supplied deployment source is unverified. It must not be equated with the Evidence Ledger, Usage/Governance F or Audit Sink F.sink without explicit caller evidence. [SRC/INF; deployment source UNRESOLVED]
- `restore_backup` restores one file. `verify_backup` delegates to restore into a private temporary directory. [SRC `backup/archive.py`, inspected for r3]
- No code path in backup, restore, verify, readiness, integrity or persistence references the sink, according to the prior source review. The existing backup is not a coordinated primary-and-sink backup. [SRC, reported in r2; broader search not repeated]

**Verification side effects**
- `_verify_db` constructs `Ledger(path)` before its integrity checks. Depending on file state, the constructor can run WAL switching, `quick_check`, bootstrap `CREATE TABLE IF NOT EXISTS`, pending migrations, and `user_version` changes. This is source-derived and was not executed in this review. [SRC; not executed]
- For `verify_backup`, the source checks the stored backup member's hash, copies it to a private temporary file, checks the copy's hash, then calls `_verify_db` on that temporary copy. The source does not establish that `verify_backup` migrates the stored backup member itself; no runtime behavior was tested. [SRC `backup/archive.py`, inspected for r3]
- `recover()` does the same Ledger construction, then a WAL checkpoint and a `BEGIN IMMEDIATE` probe. [SRC, reported in r2; not executed]
- `_finalize` sets `journal_mode=DELETE` on the finalized copy. [SRC, reported in r2; not executed]

**Raw SQLite access**
- 16 `sqlite3.connect` call sites in `src/`. [SRC, counted in prior review; not recounted]
- Read-only (`mode=ro`): identity read, legacy inventory, reconcile, online-copy source, owner-backup final validation. [SRC, reported in r2]
- Read-write maintenance paths: `Ledger._connect`, `_verify_db` read transaction, `_finalize`, owner-backup staging, `_stream_facts`, online-copy target. [SRC, reported in r2]
- Owned production repositories: usage repository, governance store, sink. [SRC, reported in r2; actual external deployment use unknown]
- Read-write connections without `mode=ro` can create a missing file. Whether each path can be reached with a missing file is [UNRESOLVED].

**Evidence Ledger**
- Not constructed in the repository's production runtime; the guarded factory has no production caller in the reviewed code. External deployment use is unknown. [SRC, reported in r2]
- The Ledger's append-only triggers and hash chain apply to any instance. Plain `Ledger` construction can bypass the guarded tail hook. [SRC, reported in project history]
- The constructor-location scan forbids `Ledger(` outside `persistence`, `integrity` and `backup`. It does not cover other storage constructors. [SRC; `tests/unit/test_stage1054c3_final.py`, test `test_ledgers_are_constructed_only_inside_the_allowed_packages`] [TEST, existence reported in r2; not re-run]

**Usage append-only**
- API-level only: no update or delete method in the usage repository. [SRC, reported in r2]
- No database triggers on `usage_records`. [SRC, reported in r2]

**Concurrency and storage limits (Stage 3 lock, changed in Stage 3)**
- The first-open lock uses `flock` on the canonical parent directory of each database. [SRC `persistence/first_open_lock.py`, inspected for r3]
- Approved design elements and observations are separated in section 4.

---

## 4. Stage 3 approved design and separately recorded limitations

**Owner-approved design, Stage 3 scope only.** The owner-provided project history identifies these Stage 3 design elements as approved. The underlying original approval messages were not independently available in this review; the archive itself is not proof of approval. No approval is extended beyond Stage 3. [DOC: owner-provided project history]

| # | Rule | Scope and limit |
|---|---|---|
| S3-3 | **Nested acquisition refusal.** A thread may hold at most one first-open lock. Any nested call fails immediately with `LockUnavailable`, for the same or a different path. | Enforced in `first_open_lock.py` [SRC]. Coverage by `test_stage1057_reentry.py` reported in r2 [TEST; not re-run]. |
| S3-4 | **Ledger first-open lock scope.** The Ledger holds the lock for initialization and migration only, released when `__init__` returns or raises. Normal transactions and the Ledger lifetime are not locked. | Source implementation inspected for r3 [SRC]. Coverage by `test_stage1057_lock_release.py` reported in r2 [TEST; not re-run]. |
| S3-5 | **Directory-lock behavior.** The lock target is the canonical parent directory. No lock sidecar file is created. | Source implementation inspected for r3 [SRC]. Test `test_lock_target_is_canonical_parent_directory_and_no_file_is_created` reported in r2 [TEST; not re-run]. |

**Reported operational rules, observations and unresolved risks.** Their inclusion is not an approval. r2 attributed some to earlier owner approvals, but the original messages are not available for independent checking here. Reconfirm their approval status before relying on an attribution.

| # | Item | Classification |
|---|---|---|
| S3-1 | **Mixed-version opening restriction.** Only one code version may open a given store at a time; older code can bypass the new lock. | Operational restriction reported in r2; original explicit approval evidence not available here. [DOC/UNRESOLVED] |
| S3-2 | **Owner-backup separation.** Owner backups must not overlap first-open initialization or schema migration. Stage 2 backup code is unchanged. | Operational restriction reported in r2; original explicit approval evidence not available here. [DOC/UNRESOLVED] |
| S3-6 | **Directory-lock trade-offs.** First opens in one directory serialize unrelated databases; a Ledger busy-wait under the lock can delay others. | Technical observation reported in r2; separate owner acceptance not independently established. [SRC as reported/UNRESOLVED] |
| S3-7 | **Hardlinks.** Hardlinks in the same directory share one directory lock; hardlinks in different directories are not coordinated. | Limitation; characterization test `test_hardlink_alias_in_other_directory_is_not_coordinated_characterization` reported in r2 [TEST; not re-run]. Not classified as an approved decision. |
| S3-8 | **Symlinks.** `realpath` is applied before the lock directory is opened; a symlink replacement between resolution and open is not covered. | Source observation and unresolved race [SRC/UNRESOLVED]. Not classified as an approved decision. |
| S3-9 | **Platform scope.** Linux exercised; macOS unverified; Windows refusal-only; network filesystem locking unreliable or unsupported. | Reported platform evidence and limitations [DOC/UNRESOLVED], not an approved decision or a cross-platform guarantee. |
| S3-10 | **Unbounded wait.** The blocking `flock` wait is unbounded and described as temporary Option A. | Source observation [SRC] and reported temporary limitation [DOC]. r2 attributes acceptance to the owner, but original approval evidence is unavailable here. No timeout, retry, polling, helper thread or cancellation is authorized by this record. |
| S3-11 | **Protocol scope.** The protocol addresses first-open locking only; it does not provide a bound on SQLite busy waits or database deadlocks. | Reported technical scope and limitation [DOC]; not classified as an approved decision. Existing bounded SQLite busy-wait is distinct from the unbounded `flock` wait. |

---

## 5. Decision records

### D-00. Evidence record for the C3.3 storage decision

- **Status:** Unresolved question.
- **Documented record:** r2 reported none in the accepted archive or repository documents. [DOC: prior search report; not repeated]
- **Conversation record:** the project owner referred to an earlier C3.3 architecture decision concerning independent physical SQLite files. Its exact wording and formal source are not available in the accepted Stage 3 archive. [DOC: owner-provided project history; UNRESOLVED]
- **Position of this document:** preserve the conversation-level decision reference. A missing formal source record does not mean the decision never existed or was disproven.
- **Decision requested:** the owner provides the original C3.3 decision text, or confirms that no formal text exists.
- **Effect until resolved:** D-01 is a provisional proposed reference model, not an approved requirement. It is not presented as overriding or having been checked against the earlier decision.

### D-01. Usage and Governance storage model

- **Status:** Proposed decision (provisional reference model).
- **Documented policy:** "same file, own tables" [DOC `GOVERNANCE.md:9`, reported in r2]. Sink separation is documented separately [DOC `GOVERNANCE.md:79`, reported in r2].
- **Implementation:** if `Runtime.build()` is invoked, it configures one file F for Usage and Governance; the sink defaults to F.sink. External deployment use is unknown. [SRC; sink default reported in r2]
- **Proposed direction:** keep the documented shared-file model as the provisional reference. Do not split the files unless a documented technical requirement proves that shared ownership cannot be made safe. This is the provisional reference for other proposals in this document. It is not an approved requirement and must be reconciled with D-00.

**Evidence bearing on shared ownership.**
- The Ledger and Governance schemas conflict on the same file, in both open orders. [TEST] `KnownSchemaCollisionCharacterization` in `tests/integration/test_stage1057_first_open.py`, with exact expected exceptions, as reported in r2. This characterizes the conflict and does not repair it. [ADHOC earlier probes on temporary files reportedly confirmed the same outcome during Stage 3; not repeated]
- The prior review reported no observed Usage and Governance conflict. The `Runtime.build()` open order (usage, then governance, then sink) on a fresh file has not been tested in this review. [DOC/UNRESOLVED]
- Whether the Ledger-Governance conflict proves shared ownership unsafe is [UNRESOLVED]. Evidence Ledger is not wired into the repository's production runtime, so the conflict alone does not establish that the Usage/Governance pair is unsafe in deployment.

**Conditions for keeping the provisional model (proposed):**
- A table-owner registry naming the owner of every table in F.
- One migration owner for F; no cross-subsystem migrations.
- Verification is read-only and never migrates (D-04).
- A test of the `Runtime.build()` open order on fresh temporary files, subject to G1 and G6, and G8 if undertaken as an implementation phase.

**Triggers for reconsidering the split (any one):**
- The `Runtime.build()` open order fails on a fresh file.
- A documented requirement for independent backup, retention or restore of usage and governance is adopted.
- A second writer that must share F is added.

**Alternatives and trade-offs.**

| Option | Consequences |
|---|---|
| Shared file F (provisional reference) | Matches documented policy and `Runtime.build()` configuration, if invoked. Deployment use remains unknown. Shared migration and corruption blast radius. Schema conflict class remains structural. Cross-table atomicity possible only with a shared connection (not built). |
| Split: F_usage, F_governance, F.sink | Removes the shared-owner conflict class. Requires configuration change, migration tooling, a three-source reconciler, and three-file backup (D-03). Loses single-file atomicity. Requires amending `GOVERNANCE.md:9`. |
| Shared file with a stronger ownership contract | Documentation and enforcement work; no configuration change. Does not remove the shared blast radius. |

**Security and recovery implications.** Under the proposed shared model, one file failure removes accounting and governance state together.

**Approvals required:** owner choice of model; amendment of `GOVERNANCE.md:9` if the split is chosen; resolution of D-00. G8 and other applicable gates apply to implementation.

### D-02. Cross-subsystem transaction guarantees

- **Status:** Proposed decision (reconciled eventual consistency). The atomicity requirement remains unresolved.
- **Documented policy:** no cross-database transaction; at-least-once delivery with sink deduplication; the reconciler detects disagreement [DOC `GOVERNANCE.md:83`; `GOVERNANCE_STATE_MACHINE.md` section 9, reported in r2].
- **Implementation:** separate connections and transactions for governance, usage and sink; no atomicity across them [SRC, reported in r2].
- **Gap in guarantees:** a settled request can lack its usage row after a crash between the governance commit and the usage append [INF from reported source write order; not experimentally demonstrated here]. The reconciler reports this as `LEDGER_MISSING_FOR_COMPLETION` [DOC, reported in r2]. Resolution is operator-driven.

**Failure windows (operator procedure to be approved):**

| Window | State after crash | Detection | Procedure |
|---|---|---|---|
| W1: governance settle committed, usage append not | Settled, no usage row | `LEDGER_MISSING_FOR_COMPLETION` | To be defined. Must not be automatic. Unresolved. |
| W2: governance committed, sink delivery not started | Outbox row pending | pending state; `AUDIT_DELIVERED_NO_RECEIPT` | Recovery redelivers under lease [DOC, reported in r2]. |
| W3: sink receipt committed, outbox confirm not recorded | Attempted lease; receipt exists | Lease expiry | Redelivery deduplicated by the sink [DOC, reported in r2]. |
| W4: crash during provider call | Exposure | `exposure.open` | Operator resolves exposure [DOC, reported in r2]. |

**Guarantees.**

| Guarantee | Status |
|---|---|
| Each write is atomic within its own subsystem | Implemented [SRC, reported in r2] |
| Usage duplicates rejected by primary key | Implemented [SRC, reported in r2] |
| Governance settle idempotent per intent | Documented [DOC]; not re-verified here |
| Sink delivery at-least-once, deduplicated at the sink | Documented [DOC] |
| Atomicity across usage and governance | Not implemented [SRC, reported in r2] |
| Atomicity across governance and sink | Not provided by design [DOC] |
| Automatic repair of missing usage rows | Not implemented [SRC, reported in r2] |

**Approvals required:** owner decision on atomicity vs reconciled consistency; approval of the procedure for each window; G8 and other applicable gates for implementation.

### D-03. Sink backup, restore and recovery

- **Status:** Proposed decision (coordinated backup set, Policy A). Policy B and retention periods remain unresolved.
- **Documented policy:** the sink is a separate file with its own retention policy [DOC `AUDIT_SINK_BOUNDARY.md:26`]. Backup documents are silent on the sink [DOC, reported in r2].
- **Implementation:** backup, restore and verify are Ledger-oriented and cover one caller-supplied database file; no code path covers the sink according to the prior review. The actual deployment source supplied to `create_backup` is unknown; `ledger.sqlite` is only the internal backup filename. Do not equate Evidence Ledger, Usage/Governance F and Audit Sink F.sink. [SRC `backup/archive.py`; broader sink coverage search reported in r2; deployment UNRESOLVED]
- **Coverage gap:** a restore of the primary without the sink could leave governance receipts without matching sink receipts, which the reconciler reports as `SINK_RECEIPT_MISSING` [SRC for the reconciler codes, reported in r2; the scenario is INF, not executed]. This is a gap relative to the proposed requirement. No document requiring the sink to be backed up was identified in the prior review.

**Backup lifecycle: offline backup (the proposal in this section).**
- Policy A requires the runtime to reach SHUTDOWN before the copies are taken. [PROPOSED]
- SHUTDOWN is terminal: the runtime does not return to READY, and the ledger is closed at that point [SRC `composition/runtime.py`, runtime lifecycle docstring, reported in r2].
- Therefore Policy A is an **offline backup**. It requires a full shutdown, the backup, and a restart by creating a new runtime instance with `Runtime.build`. The runtime cannot serve requests during the backup.
- The proposed cutoff marker is the governance outbox head, the sink's highest receipt id, the usage head, and a hash of each file, all recorded after SHUTDOWN. Feasibility and consistency require a separately approved design and validation.

**Online backup is not proposed in this document.** Any approach that copies files while the runtime is READY or DRAINING is a future proposal. It would need a separate design, its own consistency argument, and its own approval. It is an unresolved question, not part of Policy A.

**Copying two files alone does not guarantee consistency.** Two files copied at different times can disagree. SQLite offers no atomic snapshot across two files.

**Policy A steps (proposed, offline).**
1. Shutdown to SHUTDOWN, so no write is in flight.
2. Record the cutoff marker.
3. Create one manifest listing both files with sizes, SHA-256 hashes, schema versions, migration checksums, and the cutoff marker.
4. Verify with the read-only cross-file checker (D-04): every confirmed outbox row has a sink receipt; every sink receipt has an outbox row, or is flagged as an orphan within retention; no outbox row is attempted with a live lease at the cutoff; usage-to-governance completions are counted, and `LEDGER_MISSING_FOR_COMPLETION` cases are listed.
5. Partial-restore refusal: restore refuses unless both members are present, hashes match, the manifest is complete, and all checks pass. Restore never publishes one member alone.
6. Two-phase publish: stage both files under private temporary names; write the publish marker only after both are staged and verified. A crash mid-publish leaves no marker, and the next restore refuses the partial set.
7. Reconciliation: run the read-only reconciler before accepting requests. Operators review every orphan and missing record. Nothing is auto-deleted.
8. Retention: sink retention must be at least the backup retention. Backups older than sink retention restore only after an orphan review. Periods are an unresolved question.

**Policy B (not recommended).** Back up the primary only, then redeliver outbox rows not confirmed in the restored primary, relying on sink deduplication. Receipts that existed only in the sink cannot be recovered and become permanent orphans. Acceptable only if the owner downgrades the sink's evidence status (unresolved). These effects are proposed analysis, not an experiment.

**Approvals required:** choice of Policy A or B; the offline lifecycle decision; retention periods; cutoff marker format; G8 for each implementation phase and all applicable action gates, including G1 for database creation, G3 for migration and G4 for any backup or restore on real data.

### D-04. Verification and migration separation

- **Status:** Proposed decision.
- **Documented policy:** none explicit. The reconciler documents "never runs migrations" [DOC]. Backup documents describe verification without stating whether it may migrate [DOC; partial, reported in r2].
- **Implementation:** `_verify_db` and `recover()` construct `Ledger(path)`, which can migrate and change journal mode depending on the database state [SRC; not executed]. In the inspected `verify_backup` path, the stored member's hash is checked before it is copied, the temporary copy's hash is checked, and `_verify_db` then opens that temporary copy. This source observation does not establish a write to the stored artifact; it was not executed. [SRC `backup/archive.py`]
- **Classification:** this is a **design gap**, not a confirmed defect. No document requires read-only verification. It becomes a defect only if the owner adopts the proposed requirement below.

**Proposed direction.**
- Read-only verifier: opens with a `mode=ro` URI. No journal-mode change, no migration, no `user_version` change, no WAL pragma, no write probe.
- Ownership check first: identify which subsystem's tables are present; refuse unknown or mixed schemas before any other check.
- Migration-capable opener: used only for explicit maintenance on a private copy, or for a restore target that does not yet exist. Each use requires G3 and any other applicable approval, including G1 for creation and G8 for implementation.
- Backup verification uses hashing and read-only inspection. No writer is opened on a stored artifact.
- Every verify report records the pre-state: schema version, table list and file hash.

**Trade-off.** A read-only verifier reports "needs migration" and stops. This is appropriate for an audit system, but it adds an operator step.

**Approvals required:** approval of the two-path split; decision on whether migrations on backup artifacts are ever allowed; G8 and applicable action gates for implementation.

### D-05. Evidence Ledger rollout

- **Status:** Unresolved question (whether the Evidence Ledger is required for production, and in which stage). The documented staging facts below are not decisions; prerequisites are proposed.
- **Documented intent [DOC]:** `open_guarded_ledger` is the sanctioned production path (`INTEGRATION_10_54C3.md:8`); the integrity module docstring says "every production ledger" uses it, as reported in r2.
- **Documented status [DOC]:** "no production module yet constructs a ledger"; "not wired to a live production path yet" (`INTEGRATION_10_54C3.md:12, 72`), as reported in r2.
- **Implementation [SRC]:** not constructed in the repository's production runtime; the guarded factory has no production caller in the reviewed code. External deployment use is unknown. [Reported in r2]
- **Classification of the docstring wording:** documentation wording that reads as enforced production behavior. Corrected by this record. It is not a verified production defect.

**Status statement for any production-facing document:** the Evidence Ledger is not wired into the repository's production runtime. Production integrity claims about evidence are not supported by the reviewed code.

**Prerequisites before any production integrity claim (proposed):**
1. Storage location and ownership decided (D-01).
2. One production constructor path through the guarded factory, enforced by a test that covers all storage constructors.
3. A defined list of runtime events that write evidence, and a test that a `handle()` call produces them. The event design does not exist yet (R7a).
4. Backup, restore and verify covering the evidence file (D-03). Existing Ledger-oriented code's deployment source is unverified and must not be assumed to provide this coverage in deployment.
5. Verification and migration separated (D-04).
6. Reconciler coverage of evidence records.
7. Documented limits: the tail hook is a window check, and only `full_integrity` supports integrity claims.

**Approvals required:** owner decision on whether the Evidence Ledger is required for production, and in which stage; G8 for each implementation phase, G5 for production integration, and other applicable action gates.

### D-06. Usage append-only enforcement

- **Status:** Unresolved question (security decision). No triggers or migrations are proposed for adoption.
- **Documented policy:** `USAGE_LEDGER.md` covers duplicates and failures, not database-level append-only [DOC]. The evidence Ledger documents triggers [DOC].
- **Implementation:** API-level append-only only; no triggers on `usage_records` [SRC, reported in r2].
- **Risk [INF; not tested]:** a writer with sufficient file/database access may alter accounting rows. This is not a demonstrated exploit. Whether this matters for cost integrity depends on the owner's threat model.

**Options (not decided):**
- Add database triggers through a migration: a schema change. Requires G8 for the implementation phase and G2 and G3, plus other applicable gates.
- Accept API-level enforcement and record the residual risk.
- Add external integrity over usage records, for example a hash chain kept in a separate file. Adds a third file and a new owner.

**Approvals required:** owner decision on whether database-level append-only is required, and which option; G8 and applicable action gates for implementation.

---

## 6. Security and recovery summary

| Area | Current exposure | Direction (not approved) | Status |
|---|---|---|---|
| Accounting integrity | Direct alteration by a sufficiently privileged file/database writer is an untested inference and risk, not a demonstrated exploit [INF; not tested] | Owner decision on triggers or external integrity | D-06 unresolved |
| Audit evidence | Sink not in backup, restore or verify paths according to the prior review [SRC reported in r2]; deployment backup target unknown | Coordinated offline backup set | D-03 proposed |
| Verification | `_verify_db` opens a Ledger and may modify its input depending on state [SRC; not executed]. The inspected `verify_backup` path opens a checked temporary copy, not the stored member, through `_verify_db` [SRC; not executed]. | Read-only verifier | D-04 proposed |
| Cross-subsystem disagreement | Possible crash window [INF from reported SRC]; operator-driven resolution [DOC] | Defined operator procedure | D-02 proposed; procedure unresolved |
| Evidence claims | Docstring wording overstates enforcement in the reviewed runtime [DOC, reported in r2] | Corrected status; prerequisites | D-05 unresolved |
| Shared-file blast radius | If `Runtime.build()` is invoked, accounting and governance are configured to share one file [SRC]; deployment use unknown | Owner decision on model | D-01 proposed |
| Ledger-Governance conflict | Characterization test and earlier probes reported; not repaired [TEST/ADHOC, not repeated] | Separate stage with approval | R9; D-01 proposed |
| Stage 3 lock limits | See S3-6 to S3-11 | Recorded; not changed | Observations and risks, not automatically owner-approved |
| Direct SQLite access | 16 call sites; some read-write maintenance paths [SRC, reported in r2] | Scan extension and reviewed allow-list | R5 not started |

---

## 7. Outstanding questions

1. Does the original C3.3 decision text exist? (D-00)
2. Which storage model: shared (provisional proposed reference), split, or stronger contract? (D-01)
3. Is cross-subsystem atomicity required? (D-02)
4. Which backup policy, and is the offline lifecycle accepted? (D-03)
5. What retention periods apply to the primary and the sink? (D-03)
6. What quiesce procedure applies to operators who copy files directly? (D-03; offline procedure only, online is out of scope)
7. Does the `Runtime.build()` open order (usage, governance, sink) succeed on a fresh file? [UNRESOLVED; G1 and G6; G8 if an implementation phase]
8. Which database does an actual deployment supply to `create_backup`, and is any deployment using it? Evidence Ledger, Usage/Governance F and Audit Sink F.sink must remain distinct. [UNRESOLVED; D-03]
9. Is `Runtime.build()` called by any external deployment? [UNRESOLVED; not answerable from the repository]
10. Is the Evidence Ledger required for production, and in which stage? (D-05)
11. Is database-level append-only required for usage? (D-06)
12. Who runs each operator procedure? (D-02)
13. Can any read-write connection without `mode=ro` be reached with a missing path? [UNRESOLVED]
14. Does the audit path's `<audit>.lock` file get created in any production path? [UNRESOLVED; not traced]
15. What explains the mismatch between the Stage 2 SHA-256 in the owner-provided project summary and the uploaded attachment matching r2? [UNRESOLVED; section 1]
16. Can original Stage 3 approval evidence substantiate the r2 approval claims for S3-1, S3-2, S3-6 and S3-10? S3-7, S3-8, S3-9 and S3-11 are observations or limitations, not automatically approved decisions. [UNRESOLVED; section 4]
17. The inspected `verify_backup` source hashes the stored member and temporary copy before Ledger-based verification of the temporary copy; actual behavior on any database state was not executed. Any remaining side-effect question outside that inspected path needs separate evidence and applicable approval. [SRC/UNRESOLVED; D-04]

---

## 8. Approval gates

No work may proceed past these gates without explicit project-owner approval. Each approval is scoped to the named action or phase. Approval of one gate or stage does not authorize any other gate or later stage. **G8 applies to every implementation phase, including read-only and test-only code, in addition to every applicable action gate.**

| Gate | Action | Required approval |
|---|---|---|
| G1 | Creating any database file, including a disposable temporary database | Explicit, per action |
| G2 | Any schema modification, including on temporary databases or copies | Explicit, per change |
| G3 | Any migration, including on temporary databases or copies | Explicit, per migration |
| G4 | Any backup or restore on real data | Explicit, per operation |
| G5 | Any production integration (runtime wiring, evidence writes, production configuration) | Explicit, per integration |
| G6 | The disposable open-order test (R0b), touching temporary SQLite files only | Explicit for R0b, in addition to G1 and any other applicable gates |
| G7 | Any baseline promotion or change | Explicit, for the specified action; no promotion until an integrated releasable version is built and reviewed |
| G8 | Stage 4, C4, or any implementation phase, including read-only and test-only work | Separate explicit owner approval per stage or implementation phase, in addition to all other applicable gates |

**R0b:** G1 and G6 are both required before the disposable test. If its execution creates or modifies a schema or runs migrations, G2 and/or G3 also apply as appropriate. If R0b includes an implementation phase, G8 also applies to that phase. No approval is granted by this document. Opening, inspecting, backing up, restoring, adopting, stamping or modifying any database also requires applicable explicit owner approval; the gates above do not grant blanket permission for those actions.

---

## 9. Explicit approvals required before implementation

Recorded state for each item. None of these is approved unless marked.

1. C3.3 record provided or confirmed absent (D-00): **Unresolved question**
2. Storage model choice (D-01): **Proposed decision**; amendment of `GOVERNANCE.md:9` if split: **Unresolved question**
3. Atomicity requirement, or acceptance of reconciled consistency (D-02): **Unresolved question** (reconciled consistency: **Proposed decision**)
4. Backup policy A or B (D-03): **Proposed decision** (A); **Unresolved question** (B)
5. Offline backup lifecycle accepted, or an online design opened (D-03): **Unresolved question**
6. Retention periods (D-03): **Unresolved question**
7. Quiesce procedure for operators (D-03): **Unresolved question**
8. Verifier split and migration rule for backup artifacts (D-04): **Proposed decision** (split); **Unresolved question** (artifact migration)
9. Evidence Ledger production requirement and stage (D-05): **Unresolved question**
10. Usage append-only decision (D-06): **Unresolved question**
11. Disposable open-order test (R0b): G1 and G6, plus G2/G3 and G8 where applicable; **not approved or started**
12. Ledger-Governance conflict resolution (R9): **Unresolved question**
13. Every implementation phase: separate stage-specific owner approval under G8, plus G1 to G7 and other approvals where applicable. No Stage 4 or C4 approval is implied.

**Stage 3 scope:** S3-3 to S3-5 are design elements identified as approved in owner-provided project history. Other entries in section 4 are operational rules, observations or limitations; their approval is not independently established here. No later-stage implementation is approved.

---

## 10. Roadmap

**Not started.** Each implementation phase requires its own separate, stage-specific G8 approval in addition to the gates listed below. Non-implementation decisions or documentation do not authorize implementation. All database actions require their applicable explicit approvals. Phases are ordered by dependency; the roadmap is a proposal, not authorization.

| Order | Phase | Scope | Preconditions | Gates | Rollback |
|---|---|---|---|---|---|
| 1 | R0a | Owner decisions in section 9 | None | Owner decisions; G8 if implementation is included | Decision reverts to proposed |
| 2 | R0b | Disposable open-order test on fresh temporary files | R0a; owner approval | G1 and G6; G2/G3 if applicable; G8 for any implementation phase | Discard temporary files only with applicable approval |
| 3 | R9 | Ledger-Governance conflict resolution plan | R0a | G8 for any implementation; G2/G3 and other gates for an applicable fix | Revert to characterized state subject to applicable approvals |
| 4 | R1a | Read-only primitives (read-only open, pre-state record, hash, table list) | R0a | G8; database inspection/opening requires applicable explicit approval | Remove module |
| 5 | R4a | Read-only cross-file checker (sink, outbox, receipts, usage) | R0a | G8; database inspection/opening requires applicable explicit approval | Remove checker |
| 6 | R2 | Ownership contract for the shared file (only if D-01 keeps it) | R0a, R9 | G8 for implementation; G2/G3 and other gates if applicable | Revert contract |
| 7 | R1b | Ownership-aware verifier | R1a, R2 | G8; database inspection/opening requires applicable explicit approval | Verifier falls back to refuse-only |
| 8 | R5 | Constructor and raw-connection scan extension | R2 allow-list | G8 for test-only implementation; other gates if applicable | Revert test |
| 9 | R3 | Coordinated offline backup set | R1b, R4a, R0a lifecycle decision | G8; G1 for database creation, G4 for backup/restore on real data, other gates as applicable | Proposed: no marker means no published set; staged-file removal requires applicable approval |
| 10 | R4b | Reconciler extension for backup sets | R3 | G8; database inspection/opening requires applicable explicit approval | Operator uses previous set, subject to applicable approvals |
| 11 | R6 | Operator recovery procedures | R4b | Owner approval of procedures; G8 if implementation is included; applicable operational gates | Procedures revert |
| 12 | R8a | Migration tooling with pre-migration backup | R1b, R3 | G8; G1/G2/G3/G4 as applicable | Proposed restore from pre-migration backup, separately approved |
| 13 | R8 | Usage triggers | R8a, R0a, R9 | G8, G2 and G3; G1/G4 and other gates as applicable | Migration rollback subject to separate approval |
| 14 | R7a | Evidence event design | R0a, R2 | Owner approval of design; G8 if implementation is included | Design revert |
| 15 | R7 | Evidence Ledger rollout | R2, R3, R5, R6, R9, R7a | G8 and G5; G1/G2/G3/G4 and other gates as applicable | Proposed disable evidence path; no integrity claim, subject to applicable approvals |

**Principle:** aim for each phase to be revertible without touching existing databases, subject to feasibility and separately approved procedures. Phases that change storage require a pre-change backup and a documented restore path, approved separately. No rollback authorizes database actions or real-data backup/restore in advance.

**Not authorized by this roadmap:** Stage 4, C4, any implementation phase, baseline promotion, or production activation.

---

## 11. Approval record

| Item | Status | Approver | Date |
|---|---|---|---|
| D-00 C3.3 record | Unresolved question | pending | pending |
| D-01 storage model | Proposed decision | pending | pending |
| D-02 transaction guarantees | Proposed decision; atomicity requirement unresolved | pending | pending |
| D-03 sink backup policy and offline lifecycle | Proposed decision; policy choice and lifecycle unresolved | pending | pending |
| D-04 verification separation | Proposed decision | pending | pending |
| D-05 Evidence Ledger rollout | Unresolved question; staging facts documented | pending | pending |
| D-06 usage append-only | Unresolved question | pending | pending |
| Stage 3 design S3-3 to S3-5 | Explicitly approved for Stage 3 according to owner-provided project history | project owner, as reported | original approval date not available |
| Stage 3 items S3-1, S3-2, S3-6, S3-10 | Operational rules or limitations; original approval evidence not independently available | unresolved | unresolved |
| Stage 3 items S3-7, S3-8, S3-9, S3-11 | Observations or limitations, not automatically approved decisions | not applicable | not applicable |
| Roadmap R0a to R9 | Not started; no implementation approved | pending | pending |

**Constraints preserved by this revision:** no ErrorCode, public API, Baseline 10.7, database schema or database constraint is changed. In r3 preparation no database was created, opened, inspected or modified; no code, tests or configuration were changed or executed; no backup or restore was performed; no archive was modified or promoted. Source files were read from the Stage 3 archive without executing them. No Stage 4, C4 or implementation was started.
