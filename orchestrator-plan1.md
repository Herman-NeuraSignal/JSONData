# `ds-nvsim-orchestrator` Overview and Milestones

- Status: Draft for team-lead review
- Last updated: 2026-08-13
- Audience: Developer team lead assuming near-term ownership of
  `ds-nvsim-orchestrator`

## Why We Need Orchestrator Now

The Collision and Doppler repositories are becoming capable scientific
services in their own right.

Collision can load a vascular scene, resolve a probe and beam against that
anatomy, and return a detailed `CollisionReport`. Doppler is gaining the
session-owned physiology, stochastic simulation, DSP, and packet-stepped
execution needed to turn those reports into simulated TCD data.

The next project need is no longer just to prove that each service works by
itself. We need to run useful, repeatable experiments **across** them.

That is the job of `ds-nvsim-orchestrator`.

In the near to medium term, Orchestrator should be the authority for headless
NVSIM experiments: experiments that can run without a user interface, use an
explicit configuration and command sequence, save their complete results, and
be repeated or reviewed later.

Orchestrator should answer practical questions such as:

- Which anatomical scene and scientific configurations did this experiment
  request?
- Which Collision and Doppler sessions actually ran?
- Which probe commands were issued, and in what order or at what simulation
  times?
- Which Collision reports and Doppler packets resulted from each command?
- Did all expected work finish, and were the sessions closed cleanly?
- Where is the complete evidence needed for numerical and human review?

Orchestrator does **not** need to know how to calculate a collision or generate
a Doppler signal. Collision and Doppler remain authoritative for their own
science. Orchestrator makes their work compose into a coherent experiment.

## The Core Mental Model: One Stack Run

The most useful way to think about Orchestrator is as the owner of a **stack
run**.

A stack run begins with a declared experiment and ends with a durable record
of what happened. For the first full-chain experiments, it will look roughly
like this:

```text
Orchestrator
  -> creates a stack run and records the requested experiment
  -> opens a configured Collision session
  -> records the Collision session ID and resolved configuration
  -> opens a configured Doppler session linked to that Collision session
  -> records the Doppler session ID and resolved configuration
  -> confirms that the required services and recorders are ready
  -> issues an ordered schedule of ProbeCommands

For each ProbeCommand:
  Collision
    -> produces a correlated CollisionReport

  Doppler
    -> consumes that report
    -> produces the requested simulated TCD realization
    -> emits or exposes the resulting Doppler packets

  Orchestrator
    -> records the command, report, packets, identities, and timing

At the end:
  -> Orchestrator closes the service sessions
  -> finalizes the stack-run record
  -> leaves a reviewable experiment bundle
```

This model allows the experiment to grow without changing who owns what.
Early runs can stop after Collision. Later runs can include finite Doppler
realizations, precisely timed packet streams, visualization, or therapeutic
Search. Orchestrator remains the run authority while the scientific data can
flow directly between the services that produce and consume it.

## What Makes a Headless Experiment Useful

A successful command-line script is not yet a complete experiment. A useful
headless run should have four properties:

1. **Declared inputs.** The scene, service configuration, command sequence,
   seeds, and run policies are explicit rather than hidden in source code.
2. **Resolved truth.** The run saves the configurations and identities the
   services actually accepted, not only what the caller requested.
3. **Complete lineage.** Every report and packet can be traced back to its
   session, command, and stack run.
4. **Reviewable output.** Numerical records and appropriate visual evidence
   are collected in one durable bundle, including failures and limitations.

This does not require Orchestrator to become a scientific validator. It should
prove that the intended inputs and outputs are complete, correlated, ordered,
and reproducible. Collision, Doppler, and later analysis workflows remain
responsible for deciding whether the scientific values are correct.

## Starting Point

The current Orchestrator is already a useful first slice. It can:

- open a Collision session over gRPC;
- select a scene explicitly;
- publish a probe command over MQTT;
- wait for the matching Collision report; and
- save a canonical JSON result.

It also has controlled tests and opt-in real-Collision integration evidence.
This means the team does not need to redesign the repository from nothing.
The next milestones should generalize this proven one-command workflow into a
reusable stack-run model.

The accepted TopCoW S020 scene provides the natural first anatomical target.
Collision already has a reviewed S020 L-MCA known-answer pose, deterministic
geometric and hemodynamic evidence, and bounded pose-perturbation evidence.
Orchestrator does not yet run that complete path as an owned experiment, but it
is close.

## Milestone 1: Run One S020 Collision Experiment

### Human goal

Prove that Orchestrator can own one useful anatomical experiment from
configuration through saved evidence.

### Experiment

1. Select the immutable `topcow-s020-mr-v1` scene.
2. Open a Collision session with the intended scene, beam, collision, and
   hemodynamic configuration.
3. Issue the reviewed S020 L-MCA probe command.
4. Receive the correlated Collision report.
5. Save the requested and resolved configuration, command, report, relevant
   identities, warnings, and completion status as one run bundle.

### Why it matters

This is the smallest experiment that demonstrates Orchestrator's intended
role. It moves the existing S020 proof out of a Collision-owned evidence
workflow and proves that another repository can run and record it through the
real service boundary.

The resulting report should be suitable for later anatomical review: for
example, confirming that the beam intersects the intended L-MCA region and
that its depth coverage and hemodynamic handoff agree with the reviewed
Collision evidence. That scientific review may occur after the run and does
not need to be implemented inside Orchestrator.

### Milestone evidence

- a repeatable command or Make target;
- one portable stack-run bundle;
- the full current Collision report, including compact beam and typed
  hemodynamic state;
- agreement with the reviewed S020 case at the contract and expected-value
  level; and
- clear diagnostics and preserved partial evidence if the run fails.

## Milestone 2: Run a Multi-Command S020 Collision Batch

### Human goal

Turn a single known pose into a small, reproducible anatomical experiment over
multiple probe locations or orientations.

### Experiment

1. Open one configured S020 Collision session.
2. Load an ordered list of probe commands from an experiment specification.
3. Begin at the reviewed known-answer pose.
4. Move through a small set of nearby or otherwise purposeful poses, initially
   informed by Collision's existing S020 perturbation and evidence cases.
5. Record the command and corresponding Collision report for every step.
6. Close the session and finalize one batch-run bundle.

The first command set should be scientifically purposeful and reviewed, not an
arbitrary grid search. Reusing or adapting the existing S020 evidence cases
gives the experiment known landmarks while still demonstrating that one
session can process changing commands.

### Why it matters

This is the first genuinely useful headless batch mode. It creates an ordered
record of how simulated beam-vessel interactions change as the probe moves.
The run can then be inspected concurrently or afterward to answer questions
such as:

- Do interactions change in the expected direction as the pose changes?
- Do intended vessels appear and disappear at anatomically plausible points?
- Do depths, coverage, projection, and quality evidence remain coherent?
- Can the same experiment be repeated without hidden state or process restart?

Orchestrator's responsibility is to make that analysis possible by preserving
the complete sequence and lineage. The anatomical interpretation remains a
Collision and human-review concern.

### Milestone evidence

- a versioned multi-command experiment specification;
- one Collision session reused across the ordered sequence;
- a complete one-to-one command/report record with stable request and
  correlation identities;
- explicit command ordering and run completion state;
- summary tables or lightweight visual review aids; and
- repeatable results for the same scene, configuration, and command set.

Precise command timing is not required yet. At this milestone, order is the
important experimental fact.

## Milestone 3: Run the Full Collision-to-Doppler Batch

### Human goal

For the same probe experiment, record both what the beam collided with and the
simulated TCD data that collision produced.

### Experiment

1. Open the configured Collision session.
2. Open a Doppler session linked to the returned Collision session ID.
3. Record the resolved scientific configuration of both services.
4. Use the same reviewed probe-command set introduced in Milestone 2.
5. For each command:
   - record the resulting Collision report;
   - allow Doppler to run one finite four-second scientific realization from
     that report; and
   - record the complete ordered Doppler packet result and its lineage.
6. Close both sessions and finalize one full-chain run bundle.

The initial four-second realizations are finite experiments, not loop-smoothed
streams. They should preserve their natural beginning and end. Reusing the
same S020 command sequence makes the new Doppler evidence directly comparable
with the already understood Collision evidence.

### Why it matters

This is the first complete NVSIM scientific chain:

```text
probe command
  -> anatomical collision and relative flow state
  -> physiology and stochastic Doppler simulation
  -> DSP output and TCD packets
  -> recorded evidence for review
```

For each pose, reviewers should be able to inspect the Collision report next
to the four-second Doppler result and ask whether the signal is plausible for
the reported vessel interactions. The most useful review evidence will include
the packet data plus Doppler-owned spectrogram, envelope, and M-mode
visualizations where available.

Orchestrator should confirm completeness, linkage, order, shapes, identifiers,
and run status. Doppler remains responsible for the numerical and visual
scientific known answers.

### Milestone evidence

- one linked Collision/Doppler session pair;
- resolved configuration and readiness evidence from both services;
- the same ordered S020 command set used by the Collision-only batch;
- one attributable Collision report and one complete four-second Doppler
  realization per command;
- exact command-to-report-to-execution-to-packet lineage;
- human-reviewable Collision and Doppler evidence grouped by command; and
- clean two-service shutdown after success or preserved failure evidence after
  a partial run.

This milestone depends on the Doppler session-first service work, Issue #13
scientific execution, and the first accepted packet-output boundary. Those
dependencies should not prevent Orchestrator from designing and testing the
run lifecycle with controlled fakes now.

## Milestone 4: Run a Precisely Timed Packet-Stepped Experiment

### Human goal

Move from a sequence of independent four-second realizations to an experiment
whose probe commands and Doppler output share an explicit simulation timeline.

### Experiment

1. Open linked Collision and Doppler sessions under one run timeline policy.
2. Start packet-stepped Doppler execution.
3. Admit probe commands at declared simulation-time onsets.
4. Record the Collision report and Doppler execution transition caused by each
   command.
5. Record the continuous ordered packet history with explicit simulation
   intervals.
6. Verify that command onsets, realization transitions, and packet timestamps
   obey the selected policy.

The Doppler state machine is important here because packet production must
retain stochastic and DSP state between packets. Orchestrator should coordinate
the experiment and record its timing; it should not implement that state
machine.

### Why it matters

This milestone begins to resemble an evolving simulation rather than a list of
independent batch calculations. It provides the temporal foundation needed
for later interactive Vis control and closed-loop therapeutic Search.

It also lets the team test the distinction between:

- when a command becomes effective in simulation time;
- which Collision state caused a packet realization;
- the simulated interval represented by each packet; and
- when computation or publication happened in wall-clock time.

### Milestone evidence

- a declared run timeline and command-onset policy;
- a scheduled command sequence with precise simulation-time onsets;
- ordered packet records with explicit simulation intervals and realization
  identity;
- review showing that commands and resulting signal changes occur at the
  intended simulated times; and
- separate measurements of processing and publication latency.

This milestone does **not** automatically claim real-time performance. The
current 8 ms reference packet interval is a scientific packet cadence, not a
promise that every packet will be computed and delivered within 8 ms of wall
time. Real-time pacing requires a separate measured service level and explicit
late-data policy.

Several transition details remain design decisions, including whether commands
must align to packet boundaries and whether a new report supersedes or queues
behind an active realization. The milestone should select and document a
simple first policy rather than hide these choices.

## What These Milestones Enable Later

Once Orchestrator can run and record timed full-chain experiments, the stack
can add new command sources without changing scientific ownership:

- Vis can become the selected interactive command source for an operator-run
  experiment.
- Search can consume a declared packet window and propose or issue the next
  probe command.
- A human-in-the-loop mode can allow Search to propose a command and an
  operator to approve or modify it.
- Real-time pacing can be introduced after performance and late-data behavior
  are measured.

These are important future modes, but they should build on the same headless
stack-run lifecycle and evidence model rather than replace them.

## The Minimum Architecture Underneath the Milestones

The milestone story is intentionally experiment-first, but several technical
concepts are necessary to make the experiments trustworthy:

| Experimental need | Supporting Orchestrator concept | First needed |
| --- | --- | --- |
| Repeat and identify a complete experiment | Stack-run specification, ID, status, and final record | Milestone 1 |
| Preserve what services actually ran | Requested versus resolved configuration | Milestone 1 |
| Relate every result to its cause | Session, request, correlation, execution, and packet lineage | Milestones 1–3 |
| Reuse one session for an ordered command set | Transport-independent run coordinator | Milestone 2 |
| Avoid issuing work before consumers are available | Granular service and recorder readiness | Milestone 3 |
| Recover from a partially opened run | Deterministic reverse-order cleanup and failure evidence | Milestone 3 |
| Run services on different hosts | Configurable endpoints and network-only contracts | All milestones |
| Admit commands reproducibly | Command-source and simulation-timeline policy | Milestone 4 |
| Review a run without Vis | Authoritative headless experiment bundle | All milestones |

These concepts should generally live in a transport-independent Orchestrator
core with gRPC and MQTT adapters around it. That keeps the experiment lifecycle
testable with controlled fakes while Collision and Doppler contracts continue
to mature.

Orchestrator should remain a stack-run coordinator, not a deployment platform
or data relay. It may connect to independently hosted services and observe
their events without provisioning their machines or forcing every scientific
message through its own process.

## Relationship to the Detailed Issue Plan

This document is the recommended starting point for understanding **why** the
Orchestrator work matters and **which experiments** should become possible.

The companion
[`ds-nvsim-orchestrator-overview-and-issues.md`](ds-nvsim-orchestrator-overview-and-issues.md)
contains the more detailed engineering context: contract synchronization,
transport-independent ports, readiness, Doppler adapters, packet recording,
acceptance direction, dependencies, and explicit non-goals.

The two documents should eventually be reviewed together:

- this milestone document should remain the accessible product and research
  narrative;
- the issue document should remain the technical planning source; and
- GitHub issues should connect a bounded implementation slice to the milestone
  evidence it unlocks.

The immediate review question is therefore not whether every technical issue
is already written perfectly. It is whether these four experiments describe
the right progression of value for Orchestrator ownership. Once that sequence
is agreed, the detailed issue candidates can be regrouped beneath the
milestones and promoted into implementation work deliberately.
