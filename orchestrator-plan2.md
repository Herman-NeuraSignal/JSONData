# `ds-nvsim-orchestrator` Technical Architecture and Milestone Issues

- Status: Draft for team-lead review
- Last updated: 2026-08-14
- Audience: Developer team lead assuming near-term ownership of
  `ds-nvsim-orchestrator`

## How to Read This Document

This is the technical companion to
[`ds-nvsim-orchestrator-overview-and-milestones.md`](ds-nvsim-orchestrator-overview-and-milestones.md).
That overview should be read first. It describes four experiments of increasing
value:

1. one reviewed S020 Collision experiment;
2. an ordered multi-command S020 Collision batch;
3. the same batch with finite Collision-to-Doppler realizations; and
4. a packet-stepped experiment with explicit simulation-time command onset.

This document explains the architecture beneath those experiments and proposes
larger GitHub **milestone issues** that can deliver it. Here, a milestone issue
means a coherent engineering outcome with internal implementation gates,
evidence, and review checkpoints. It is larger than a small technical ticket,
but should still end in one useful, demonstrable repository capability.

The proposed issue sequence intentionally does not mirror the four experiment
milestone numbers. Experimental milestones describe increasing research value;
implementation issues describe coherent bodies of engineering work. Their
mapping is made explicit below.

These are planning recommendations, not pre-approved implementation
specifications. The team lead should refine scope and checkpoints when opening
live issues, especially where Collision or Doppler contracts are still
evolving.

The canonical architectural authority is
[`ds-nvsim-core/docs/multi-service-nvsim-stack-architecture.md`](../ds-nvsim-core/docs/multi-service-nvsim-stack-architecture.md),
currently awaiting team review. This companion applies that architecture to
Orchestrator; it does not replace it.

## Architectural Outcome

`ds-nvsim-orchestrator` should become the authority for a **stack run**: one
declared, bounded, reproducible execution involving independently owned NVSIM
services.

Orchestrator should know:

- what run was requested and which run policies were selected;
- which service endpoints and versions participated;
- which service-local sessions were opened and how they were associated;
- what configuration each service actually resolved;
- whether the required services, consumers, and recorders were ready;
- which command source was authoritative;
- which commands were admitted, in what order, and eventually at what
  simulation times;
- which reports, executions, realizations, and packets resulted;
- whether the run completed, failed, or was only partially cleaned up; and
- where its portable, reviewable evidence was finalized.

Orchestrator should not calculate collisions, resolve vascular physiology,
generate stochastic I/Q, run Doppler DSP, judge anatomical correctness, own a
visualization session, or select a therapeutic action. Recording a service's
scientific output does not transfer ownership of that science.

The intended topology is central lifecycle with decentralized scientific data:

```text
                           run control
                    +-----------------------+
                    |     Orchestrator      |
                    | stack run + recorder  |
                    +-----------------------+
                       | open/close/readiness
                       v
command source --> Collision ---------> Doppler
                      | report events      | packet events
                      +---------+-----------+
                                |
                         authorized observers
                    Orchestrator / Vis / Search
```

Orchestrator establishes the run, associates sessions, admits commands, and
records evidence. Collision reports should still flow directly to Doppler, and
Doppler packets should flow directly to authorized consumers. Orchestrator is
not a mandatory scientific relay or a replacement for the broker.

For the initial headless experiments, the authoritative command source is
Orchestrator's own deterministic batch scheduler. The command-source role is
shown separately because later runs may assign it to Vis, Search, or another
authorized participant without changing stack-run ownership.

## Current Baseline and Immediate Gaps

The current repository is a useful first integration slice. It can inspect and
open Collision sessions over gRPC, select a scene, subscribe before publishing
a correlated MQTT `ProbeCommand`, collect the matching `CollisionReport`, and
save canonical JSON. Controlled tests and opt-in real-Collision tests already
exercise the boundary.

That baseline should be evolved rather than discarded. Its principal gaps are:

- the workflow represents one command, not an owned stack run;
- the vendored Collision contract predates the accepted compact-beam and typed
  relative-hemodynamic fields from Collision Issue #78;
- requested and resolved configuration are not preserved as a complete run
  record;
- there is no versioned experiment specification or ordered command batch;
- Collision lacks the explicit `CloseSession` RPC needed for authoritative
  cleanup;
- there is no Doppler lifecycle client or linked-session model;
- readiness is not separated into lifecycle, scientific execution,
  publication, recorder, and required-backend dimensions;
- no Doppler execution/packet boundary or full-chain recorder is present;
- no explicit command-source or simulation-time policy exists; and
- local evidence commonly assumes sibling checkouts and `localhost`, although
  independently hosted services are the architectural target.

These gaps do not call for a general workflow engine. They call for a small,
typed orchestration core around the transport adapters already proven.

## Suggested Internal Architecture

In this document, the **domain core** means the small body of Orchestrator code
that expresses what a stack run is and how one proceeds. It owns run state,
service-session association, readiness admission, command sequencing,
correlation, failure handling, cleanup policy, and the final run record. It
does not know how gRPC calls, MQTT subscriptions, protobuf messages, or a
particular scientific algorithm are implemented. Those details sit behind
adapters and ports, allowing the same run behavior to be exercised with
controlled fakes in unit tests and with independently hosted Collision and
Doppler services in real experiments.

### Keep the domain core transport-independent

The stack-run lifecycle should be testable without gRPC, MQTT, generated
protobuf classes, a broker, or sibling repositories. A useful package shape
might eventually resemble:

```text
nvsim_orchestrator/
  run/             # stack-run specifications, records, states, coordinator
  ports/           # service lifecycle, command, observation, readiness, store
  adapters/        # Collision/Doppler gRPC and MQTT implementations
  evidence/        # portable bundle assembly and integrity checks
  cli.py           # composition root and human-facing commands
```

The exact names are not important. Generated protobuf messages should remain
at adapter boundaries. Domain objects should carry the identities and
normalized evidence Orchestrator needs without becoming alternate copies of
service-owned scientific schemas.

### Use one explicit stack-run state machine

The first coordinator should support one active Collision session and, when
required, one linked Doppler session. A run should move through explicit states
such as:

```text
REQUESTED (sequentially for now , no backward for now)
  -> OPENING_COLLISION
  -> OPENING_DOPPLER          # omitted for Collision-only runs
  -> AWAITING_READINESS
  -> RUNNING
  -> CLEANING_UP
  -> FINALIZING
  -> COMPLETED

Any active state
  -> FAILING
  -> CLEANING_UP
  -> FINALIZING
  -> FAILED
```

The domain core can express that lifecycle as one coordinator-owned control
flow. For example:

```python
# Conceptual pseudocode—not a proposed final API.
def run(spec: StackRunSpec) -> StackRunRecord:
    active = begin_run(spec)

    try:
        active.transition_to(OPENING_COLLISION)
        active.collision = collision_sessions.open(spec.collision)

        if spec.requires_doppler:
            active.transition_to(OPENING_DOPPLER)
            active.doppler = doppler_sessions.open(
                spec.doppler,
                upstream_collision_session_id=active.collision.session_id,
            )

        active.transition_to(AWAITING_READINESS)
        require_readiness(
            readiness.snapshot(active),
            spec.required_readiness,
        )

        active.transition_to(RUNNING)
        execute_schedule(active, spec.commands)

    except Exception as error:
        active.record_primary_failure(error)
        active.transition_to(FAILING)

    active.transition_to(CLEANING_UP)
    try:
        cleanup_in_reverse_order(active)
    except Exception as cleanup_error:
        active.record_cleanup_failure(cleanup_error)

    active.transition_to(FINALIZING)
    return evidence.finalize(active)
```

`StackRunCoordinator` would own this control flow. Operations such as opening
sessions, inspecting readiness, executing transport-facing work, and
finalizing evidence go through ports implemented by real adapters or
controlled fakes. The primary failure is recorded before cleanup so that a
later cleanup error cannot erase it. Finalization derives the terminal
`COMPLETED` or `FAILED` status from the run outcome and cleanup result. The
names and signatures above are deliberately non-normative.

Exact enum names may change. The important behavior is that the run record can
explain the last successful stage, primary failure, cleanup attempts, and any
cleanup failure. Partial evidence must survive a failed run.

Resources should be released in reverse acquisition order. For a linked run,
that normally means closing Doppler before Collision and then stopping event
consumers and transport clients owned by the run. The evidence recorder must
remain available through `FINALIZING` so it can preserve cleanup diagnostics
and commit the terminal run record before its own resources are released.
Until Collision supports explicit closure, the record should expose that
cleanup limitation rather than report a fully clean completion.

### Separate specification, active state, and final record

Three related objects serve different purposes:

- `StackRunSpec` describes requested participants, endpoints, service
  configuration, command source, command sequence, evidence policy, and run
  policy.
- active run state contains acquired session references, readiness, cursors,
  deadlines, and in-progress observations. It is internal and mutable.
- `StackRunRecord` is the finalized durable account of requested and resolved
  configuration, identities, commands, results, timing, diagnostics, and
  completion.

For example, an early Collision-only batch might be authored in YAML like the
following. This is illustrative rather than a proposed final schema; the names
and nesting should be refined from implementation evidence.

```yaml
schema_version: draft-v1

run:
  mode: collision_batch
  command_source: orchestrator_batch
  timeline_policy: ordered_unpaced

participants:
  collision:
    endpoint: collision.example.internal:50051
    requested:
      scene_id: topcow-s020-mr-v1
      hemodynamic_state_model_id: nominal_axis_hemodynamic_state_v1

commands:
  - command_id: s020-lmca-known-answer
    probe_pose: {...}  # Full typed pose abbreviated in this example.
  - command_id: s020-lmca-perturbation-1
    probe_pose: {...}  # Full typed pose abbreviated in this example.

evidence:
  output_directory: ./runs
  collision_report_format: canonical_json
  preserve_partial_run: true

timeouts:
  service_ready_s: 10
  command_result_s: 60
```

The endpoint, requested service configuration, command sequence, evidence
policy, and timeouts are authored inputs. Session IDs, resolved service
configuration, reports, diagnostics, and completion status are absent because
they are produced during execution and belong in `StackRunRecord`. Credentials
must not be embedded in this file; deployment configuration should refer to an
approved environment or secret-provider mechanism when authentication is
introduced.

The spec should be serializable and versioned, but YAML should not become the
domain contract. A CLI may load YAML or JSON into a typed `StackRunSpec`.
Service-specific requested configuration should remain typed according to the
owning service, and each service's returned resolved configuration remains
authoritative. This authoring style also leaves room for a future
`ds-nvsim-experiments` layer, similar in spirit to `ds-experiment-template`, to
create or submit one or more typed stack-run specifications. That optional
layer would remain a client above Orchestrator rather than defining its runtime
contract or internal architecture.

`stack_run_id` belongs to Orchestrator. Collision and Doppler retain their own
session and scientific identities; the final record maps them without
requiring either service to understand `stack_run_id`.

### Define narrow ports around owned behavior

The coordinator will likely need ports equivalent to:

- Collision service inspection, session open, and session close;
- Doppler service inspection, session open, and session close;
- command publication or submission;
- Collision report observation;
- Doppler execution/packet observation;
- participant and consumer readiness;
- clock/identity generation where deterministic tests require them; and
- incremental evidence recording plus atomic finalization.

Ports should represent behavior Orchestrator owns or invokes, not mirror every
RPC or event type. Controlled fakes should prove lifecycle, correlation,
failure, timeout, and cleanup policy. Real adapters should then demonstrate
that accepted service contracts satisfy those ports.

In concrete Python terms, a port may be a small illustrative `Protocol` that
the coordinator accepts as a dependency:

```python
class CollisionSessionPort(Protocol):
    def open(self, request: CollisionSessionRequest) -> OpenedSession: ...
    def close(self, session_id: str) -> None: ...


class StackRunCoordinator:
    def __init__(self, collision_sessions: CollisionSessionPort) -> None:
        self._collision_sessions = collision_sessions
```

The coordinator knows only the required session behavior. A
`GrpcCollisionSessionAdapter` can translate those calls to protobuf/gRPC for a
real experiment, while a `FakeCollisionSession` can provide deterministic
responses and failures in unit tests. Both implement the same port, so
transport details can change without rewriting stack-run policy. The names and
signatures are non-normative and should be refined from implementation
evidence.

### Treat readiness as a set of claims

One boolean `ready` cannot safely admit a full-chain run. Orchestrator may need
to distinguish:

- service API/lifecycle readiness;
- session-open readiness;
- scientific-execution readiness;
- report-consumer and packet-consumer subscription readiness;
- packet-publication readiness;
- recorder readiness; and
- availability of required components such as native DSP.

An illustrative readiness snapshot and admission check might look like:

```python
@dataclass(frozen=True)
class ReadinessSnapshot:
    lifecycle: ReadinessState
    scientific_execution: ReadinessState
    report_ingress: ReadinessState
    packet_publication: ReadinessState
    packet_observation: ReadinessState
    recorder: ReadinessState
    native_backend: ReadinessState

    def unsatisfied(self, required: tuple[str, ...]) -> tuple[str, ...]: ...


required = (
    "lifecycle",
    "scientific_execution",
    "report_ingress",
    "packet_publication",
    "packet_observation",
    "recorder",
    "native_backend",
)

def require_readiness(
    snapshot: ReadinessSnapshot,
    required: tuple[str, ...],
) -> None:
    missing = snapshot.unsatisfied(required)
    if missing:
        raise RunAdmissionError(missing)
```

The required claims depend on the run mode. A Collision-only experiment may
require lifecycle, report observation, and recorder readiness. A full-chain
run additionally requires Doppler scientific execution, Collision-report
ingress, packet publication, and packet recording. The snapshot preserves
which claims are unavailable or not yet implemented instead of collapsing them
into one ambiguous boolean. The names and exact representation are
non-normative.

Readiness should therefore be evaluated against the selected run mode before
the first command is admitted, not treated as one universal service state.

Recorder readiness means that each evidence sink required by the run has
opened its destination, accepted the selected schema or binary format, and is
prepared to persist records before commands are admitted. It does not mean
that the recorder has validated the scientific content it will receive.

Readiness is not scientific validity. A service can be operational but reject
a particular valid-yet-unsupported request, and one failed execution need not
make the entire service globally unready.

### Make correlation and ordering first-class

Each admitted command should have a stable request identity and correlation
identity. The run record should preserve the available chain:

```text
stack run
  -> service sessions
  -> admitted command
  -> Collision report
  -> Doppler execution / finite realization
  -> ordered Doppler packets
```

The observer and coordinator should keep mismatched events out of the admitted
scientific sequence, classify duplicate or missing observations, and never
make arrival order stand in for scientific sequence. The recorder should
preserve any quarantined event and related diagnostic as run evidence without
treating it as an accepted result. Transport publication metadata and
scientific record identity must remain distinct.

### Keep time domains explicit

The architecture uses several different kinds of time:

- simulation time represented by commands and scientific records;
- event wall time used for operational traceability;
- process-local monotonic duration used for latency measurements; and
- explicit sequence/order used where timing alone is insufficient.

Milestones 1 and 2 need deterministic order, not a sophisticated clock.
Milestone 3 uses finite four-second realizations but still does not require
real-time pacing. Milestone 4 introduces a named simulation-time and
command-onset policy.

An 8 ms Doppler packet interval is a scientific grid, not an 8 ms wall-clock
service-level guarantee. Search decision latency, robot motion, late data,
command quantization, and realization-transition behavior remain explicit
future policies.

The exact policy by which Orchestrator establishes and advances simulation
time is therefore one of the central open questions of its technical
architecture. Before timed execution is implemented, the selected policy must
be named, versioned, documented, and preserved in the run specification and
record. It must not emerge implicitly from event arrival order, processing
speed, wall-clock delays, or whichever transport happens to be used first.

### Produce a portable, incrementally safe evidence bundle

Here, a **recorder** is an Orchestrator-controlled evidence sink that durably
preserves an observed run record without becoming its scientific processor.
Different evidence types need not use the same representation. Run manifests,
requested and resolved configuration, commands, Collision reports, and
diagnostics can initially use canonical JSON. Doppler packet streams should
ultimately use a compact binary representation compatible with the canonical
native `TCDLoader` in `ds-utilities`, which reads fixed-size packed `.tcd` and
`.tcdx`-style `TcdData` packet streams and exposes their envelope, spectrum,
M-mode, and packet metadata. Exact compatibility must be designed and proven
against the accepted Doppler packet contract; it should not be inferred merely
from similar fields. Its current Python entry point is
`ds_utils.load.binary.tcd_loader.TCDLoader`.
The run manifest should identify each recorded artifact's schema or binary
format, lineage, size, checksum, and loading requirements.

The final stack-run bundle should be useful without Vis and portable across
hosts. A suggested shape is:

```text
stack-run-<id>/
  manifest.json
  requested-run-spec.json
  participants/
  commands/
  collision-reports/
  doppler-executions/       # when present
  doppler-packets/          # when present
  diagnostics/
  artifacts/                # or portable references for large evidence
```

The exact layout should be proven by implementation. The durable requirements
are:

- the manifest identifies its schema and run status;
- requested and resolved configurations remain distinguishable;
- records are attributable and ordered;
- checksums protect material evidence where appropriate;
- no server-local paths are presented as portable references;
- failures preserve whatever evidence was safely observed; and
- finalization is atomic enough that an incomplete bundle cannot masquerade as
  a successful one.

Orchestrator may produce summaries and arrange service-produced visual
artifacts beside their inputs. It should not regenerate Collision or Doppler
scientific truth or become a notebook/reporting framework. A future experiment
layer may analyze and publish one or more finalized stack runs.

### Preserve independent deployment

Endpoint, broker, timeout, and topic configuration must be explicit. Tests may
use sibling repositories and locally started services, but domain behavior may
not depend on shared Python objects, shared filesystems, repository-relative
paths, or `localhost`.

Orchestrator coordinates already reachable services. Provisioning hosts,
starting production containers, configuring networks, and supervising
deployments are separate responsibilities. Development test harnesses may
optionally launch local dependencies without turning that convenience into the
runtime architecture.

## Mapping Architecture to the Experiment Milestones

| Experiment milestone | New architectural capability | Important external dependency |
| --- | --- | --- |
| 1. One S020 Collision experiment | Current Collision contract, typed run spec/record, Collision-only coordinator, portable evidence | Accepted Collision Issue #78 contract and local S020 package |
| 2. Multi-command S020 batch | Reusable session, ordered command schedule, command/report correlation, batch completion | Collision session reuse; explicit close remains desirable |
| 3. Full Collision-to-Doppler batch | Linked lifecycle, granular readiness, finite-execution and packet recording, reverse cleanup | Doppler Issues #16 and #13 plus accepted packet-output boundary; Collision close |
| 4. Timed packet-stepped experiment | Simulation timeline, onset policy, persistent packet stream, transition lineage | Doppler streaming/event semantics and selected transition policy |

The table shows why implementation issues do not map one-to-one to experiment
milestones. Linked-session lifecycle can be designed with fakes before real
Doppler science exists. Conversely, Experiment Milestone 3 needs several
independently owned service contracts before it can be demonstrated end to
end.

## External Dependency and Contract Strategy

Orchestrator should advance behind ports while respecting contract ownership:

- Collision Issue #78 is merged and available. Its compact beam and typed
  depth-resolved relative-hemodynamic fields should be synchronized first.
- Collision still needs an explicit `CloseSession` operation. Orchestrator can
  model and test the port now but cannot claim complete real cleanup until the
  service supplies it.
- Doppler Issue #16 defines the session-first lifecycle foundation:
  `GetServiceInfo`, `GetServiceState`, `OpenSession`, and `CloseSession`, with
  one active process-local session initially.
- Doppler Issue #13 defines session-owned packet-stepped scientific execution
  from normalized Collision evidence through stochastic I/Q and persistent
  DSP state to packet-ready records.
- Doppler's public scientific execution and packet-event boundaries remain to
  be proven. Orchestrator should not invent temporary public contracts for
  them.
- Doppler Issue #15 is a separate batch-kernel optimization. Orchestrator may
  record backend identity but should not assume that work provides a stateful
  packet engine.

Temporary vendored protobuf copies remain acceptable if their upstream source
revision is recorded and freshness is reproducibly checked. A stable shared
distribution mechanism can move to Core only after real cross-repository use
proves the contract and ownership.

## Proposed GitHub Milestone Issues

The five issues below form a suggested dependency progression. Each is a
milestone issue: it may contain internal gates or smaller follow-up tickets,
but it should finish with a useful demonstrated capability. Issues B and C can
proceed independently after Issue A and then converge in Issue D. Titles and
issue numbers should be chosen when the team lead promotes them to GitHub.

### Issue A: Own and record one S020 Collision stack run

**Experiment milestone enabled:** Milestone 1.

**Purpose**

Turn the current one-command integration client into the smallest real
Orchestrator-owned anatomical experiment.

**Suggested gates**

1. Synchronize the vendored Collision protobuf to the accepted Issue #78
   revision, record its provenance, regenerate bindings, and add a freshness
   check.
2. Preserve compact beam, report hemodynamic summary, and every typed
   depth-state variant through canonical conversion.
3. Introduce the minimal versioned `StackRunSpec`, `StackRunRecord`, run
   status, participant record, and Collision-only coordinator needed for one
   command.
4. Define narrow ports for Collision session access, command publication,
   report observation, recorder readiness, and evidence persistence. Keep the
   single-command coordinator independent of gRPC, MQTT, protobuf, and local
   storage details.
5. Express the reviewed S020 L-MCA command and Collision configuration as a
   declared experiment, assign its request/correlation identities, and admit
   only the one matching report.
6. Write a portable run bundle containing requested and resolved state,
   identities, command, report, diagnostics, and completion.
7. Prove the same coordinator path with controlled fakes and with the existing
   adapters in an opt-in real S020 run.

**Acceptance evidence**

- one repeatable command or Make target creates one finalized S020 stack-run
  bundle;
- the bundle contains the full current Collision report without protobuf-field
  loss;
- the recorded session, request, correlation, scene, component, contract, and
  package identities are attributable;
- mismatched, absent, and duplicate reports fail within a declared bound and
  preserve useful diagnostics;
- fake and real adapters satisfy the same domain ports without changing
  coordinator policy;
- the S020 result agrees at the contract and expected-value level with the
  reviewed Collision case; and
- injected failures produce a failed bundle with bounded diagnostics and no
  false completion.

**Dependencies**

- Current Collision contract and reviewed S020 fixture/package.

**Non-goals**

- Doppler integration, a generic workflow engine, scientific revalidation of
  Collision, required private data in default CI, or shared-contract packaging.

### Issue B: Run and record an ordered multi-command Collision experiment

**Experiment milestone enabled:** Milestone 2.

**Purpose**

Generalize the transport-independent single-command foundation into a
coordinator that reuses one Collision session for a purposeful ordered command
sequence.

**Suggested gates**

1. Extend `StackRunSpec`, active state, and `StackRunRecord` from one command
   to a versioned ordered schedule derived from reviewed S020 known-answer and
   perturbation poses.
2. Define a deterministic identity policy across the sequence and enforce
   unique, one-to-one command/report admission, including explicit handling of
   stale, mismatched, duplicate, missing, and out-of-order observations.
3. Reuse one Collision session and one ready report recorder across the whole
   schedule without reopening service state between commands.
4. Add bounded per-command and run-level timeouts. On partial failure, preserve
   every completed command/report pair and the terminal diagnostic; do not
   retry or resume implicitly.
5. Record declared schedule order separately from simulation time, event wall
   time, and measured duration. Precise simulation-time onset remains deferred.
6. Extend finalization and reverse-order cleanup across the batch, preserving
   the real-service limitation if Collision closure is still unavailable.
7. Produce batch summaries or lightweight review aids and prove the expanded
   behavior with controlled fakes before running the opt-in real S020 batch.

**Acceptance evidence**

- controlled fakes prove session reuse, ordered admission, timeout, stale,
  mismatch, duplicate, missing, out-of-order, partial-failure, and cleanup
  paths;
- the real S020 batch preserves exactly one admitted report per command under
  one session;
- repeated runs preserve command order, identity policy, and expected
  Collision evidence;
- partial failure records all completed commands and the terminal failure;
- the single-command path from Issue A remains a one-entry schedule rather than
  a separate execution architecture; and
- transport adapters remain replaceable without changing batch policy.

**Dependencies**

- Issue A.
- Collision `CloseSession` is strongly preferred for complete cleanup but need
  not block the controlled coordinator design.

**Non-goals**

- Precise simulation-time onset, arbitrary parameter sweeps, anatomical
  acceptance logic, interactive Vis control, Search, or real-time pacing.

### Issue C: Add linked Collision/Doppler session lifecycle and readiness

**Experiment milestone enabled:** Architectural foundation for Milestone 3.

**Purpose**

Make one stack run own a linked Collision/Doppler session pair, distinguish the
readiness claims later scientific work will require, and prove that the pair
can be opened and cleaned up without yet running Doppler science.

**Suggested gates**

1. Add service-neutral Doppler lifecycle and readiness ports plus controlled
   fakes before depending on a real Doppler schema.
2. Open Collision first, pass its opaque session ID into Doppler session open,
   and preserve both service-resolved configurations.
3. Define granular readiness claims for lifecycle, scientific execution,
   ingress/subscription, packet publication, native backend, and recording.
   Require only the lifecycle claims for this issue's smoke path, and preserve
   the expected `NOT_IMPLEMENTED` scientific/publication claims truthfully.
4. Implement bounded startup, partial-open failure handling, and deterministic
   reverse-order closure.
5. Consume the accepted Doppler Issue #16 protobuf and implement the real
   lifecycle client once available.
6. Add a focused real-service lifecycle smoke that opens and closes the linked
   pair without claiming scientific execution.

**Acceptance evidence**

- fakes prove open order, identity propagation, readiness admission, timeout,
  primary failure preservation, and close order;
- a Doppler-open or readiness failure admits no command and attempts cleanup
  of every acquired resource;
- the real Doppler session records its upstream Collision-session association
  and complete resolved configuration;
- Doppler's one-active-session rejection is surfaced as a structured run
  diagnostic; and
- successful real closure is demonstrated once both services expose it.

**Dependencies**

- Issue A's coordinator and port vocabulary. Issue C can proceed in parallel
  with the Collision-batch work in Issue B.
- Doppler Issue #16 for the real adapter.
- Collision `CloseSession` for complete real paired cleanup.

**Non-goals**

- Doppler scientific execution, packet observation, MQTT lifecycle RPC
  emulation, service deployment, or speculative multi-session policy.

### Issue D: Run and archive the full finite Collision-to-Doppler batch

**Experiment milestone enabled:** Milestone 3.

**Purpose**

Use the Milestone 2 S020 command set under one linked session pair and preserve
one complete finite Doppler realization for each Collision report. Informally,
a finite realization is a bounded **Doppler snapshot**: for the initial
experiment, one contiguous four-second interval of simulated stochastic data
with a defined beginning, end, packet sequence, and scientific lineage. Four
seconds is the first experimental choice, not a universal definition of a
finite realization.

**Suggested gates**

1. Integrate the accepted Doppler scientific execution/ingress boundary
   without duplicating Collision normalization or Doppler science.
2. Configure the report and packet recorders, including the selected compact
   binary packet representation and its proven `ds-utilities` loading
   compatibility or explicitly documented compatibility gap.
3. Establish report-consumer, packet-consumer, publisher, and recorder
   readiness before admitting the first command.
4. Correlate each command, Collision report, Doppler execution, finite
   realization, and packet sequence.
5. Collect bounded packet sequences and explicit completion for each
   four-second realization.
6. Detect stale, mismatched, duplicate, missing, or reordered events while
   retaining transport diagnostics separately from scientific identity.
7. Extend the bundle with service versions, model/backend identities, seeds,
   packet intervals, warnings, quality, and portable Doppler-produced visual
   evidence.
8. Prove success, mid-execution failure, terminal-timeout, and two-service
   cleanup paths with fakes and then the real stack.

**Acceptance evidence**

- the same ordered S020 commands used in Milestone 2 each yield one attributable
  Collision report and one complete four-second Doppler realization;
- every packet has an unambiguous session, execution, realization, sequence,
  interval, and source-Collision lineage;
- the recorded packet artifact declares its binary format and loading
  requirements; any claimed `TCDLoader` compatibility is exercised by an
  explicit known-answer reload test;
- the bundle groups human-reviewable Collision and Doppler evidence by command;
- controlled failure tests prove bounded finalization and preserved partial
  evidence; and
- an opt-in independently hosted full-stack known answer completes without
  shared filesystem or sibling-import assumptions.

**Dependencies**

- Issues B and C.
- Doppler Issue #13 and an accepted execution/packet-output boundary.
- Collision report ingress routing to the active Doppler session.

**Non-goals**

- Orchestrator-owned signal validation, continuous streaming, interactive Vis,
  Search, production QoS policy, or an MLflow/reporting framework.

### Issue E: Coordinate a timed packet-stepped experiment

**Experiment milestone enabled:** Milestone 4.

**Purpose**

Move from independent finite realizations to one explicit simulation timeline
where scheduled command onsets and packet intervals can be reviewed together.

**Suggested gates**

1. Review and accept the first named, versioned simulation-time,
   command-onset, and deterministic batch command-source policy, with exactly
   one authoritative source for the initial run phase.
2. Assign declared simulation-time onsets independently of wall-clock
   publication and processing duration.
3. Select and document a simple first command-boundary and report-supersession
   policy rather than allowing transport timing to decide implicitly.
4. Integrate the accepted persistent packet-stepped Doppler operation and
   record state/realization transitions caused by new Collision evidence under
   that policy.
5. Preserve continuous packet order, simulation intervals, command/report
   lineage, transition identity, and operational latency measurements.
6. Add deterministic unpaced evidence first; measure paced execution
   separately without claiming an unsupported real-time guarantee.
7. Preserve seams for later Vis or Search command-source roles without
   implementing either integration in this issue.

**Acceptance evidence**

- repeating the schedule produces the same admitted commands, onset times,
  transition policy, and scientific sequence independent of computation speed;
- packet intervals cover the declared simulation timeline according to the
  selected policy;
- signal-state changes are attributable to the intended command and Collision
  report boundary;
- wall-clock latency is measured separately from simulation time; and
- the run makes no real-time claim unless a separately defined performance
  criterion is actually met.

**Dependencies**

- Issue D.
- Accepted Doppler persistent-execution and packet-event semantics.
- Agreement to resolve and review the first simulation-time and command-onset
  policy at Gate 1 before implementing timed execution.
- Agreement to resolve and review the first transition/supersession policy at
  Gate 3 before integrating persistent execution.

**Non-goals**

- Final Search decision latency, robotic motion, human approval, Vis UI
  integration, universal command quantization, or production late-data policy.

## Suggested Delivery and Review Sequence

```text
Issue A: one S020 run -> Experiment Milestone 1
  |
  +-> Issue B: ordered Collision batch -> Experiment Milestone 2 --+
  |                                                               |
  +-> Issue C: linked lifecycle/readiness -------------------------+
                                                                  |
                                                                  v
                                      Issue D: finite full-chain archive
                                        -> Experiment Milestone 3
                                        -> Issue E: timed packet-stepped run
                                             -> Experiment Milestone 4
```

Issue C can begin behind fakes after Issue A while both Issue B and Doppler
Issue #16 are being implemented. Likewise, record-layout and failure-path work
for Issue D can begin before the real packet event contract lands. Neither C
nor D should invent public service contracts merely to remove the dependency.

Each milestone issue should use internal gates that end in inspectable evidence
and human review. Small defects, upstream contract changes, or isolated tooling
may become separate supporting tickets without fragmenting the milestone's
coherent outcome.

## Decisions Deliberately Left Open

The proposed issues should preserve explicit seams for these choices rather
than settle them accidentally:

- final shared protobuf packaging and release ownership;
- Collision multi-session policy beyond adding explicit closure;
- exact Doppler scientific execution and packet-event transport contracts;
- whether a new Collision report supersedes, queues, or transitions an active
  realization;
- command alignment to packet boundaries;
- Search computation latency and terminal admitted-packet semantics;
- simulated robotic motion and travel time;
- paced/real-time buffering, late-data, and degradation policy;
- durable storage backend and optional external lineage systems;
- authentication, TLS, service discovery, and production deployment; and
- the shape of a possible future experiment-analysis repository above
  Orchestrator.

The first implementations should choose the smallest named policy required by
their experiment and record that policy in the run evidence.

## Guardrails for Issue Authoring

Before promoting a milestone issue to GitHub, verify that it:

- names the experiment milestone or architectural dependency it unlocks;
- ends in one demonstrable run or lifecycle capability;
- separates Orchestrator coordination from service-owned science;
- identifies upstream contract gates and does not invent around them;
- remains testable with controlled fakes at the domain boundary;
- includes explicit known-answer tests for the orchestration behavior and real
  integration outcome it claims, while leaving service-owned scientific
  correctness to the owning service's known-answer evidence;
- includes failure, timeout, partial-evidence, and cleanup acceptance paths;
- preserves requested and service-resolved configuration separately;
- records identities, versions, order, and relevant time domains;
- supports independently hosted services without filesystem coupling;
- distinguishes required acceptance evidence from optional local evidence; and
- states what scientific, transport, deployment, or future-product behavior is
  deliberately out of scope.

## Near-Term Success Definition

The first meaningful success is not a large generic orchestration platform. It
is a small, readable system that can repeat the reviewed S020 experiment,
preserve what actually happened, and grow through the same domain core into an
ordered Collision batch and then a linked Collision-to-Doppler run.

If another developer can inspect a failed or successful bundle and determine
which services ran, what they resolved, which commands were admitted, which
scientific outputs followed, how they correlate, and whether cleanup
completed—without reading implementation logs or relying on Vis—Orchestrator
is fulfilling its intended role.