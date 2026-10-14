# Demo media

Two captures of the running system, made on 2026-10-13 from commit
`00d306e` with synthetic data: a posting on a reserved `.example` host
mapped by a throwaway host policy, no operator profile, no real policy
values and no job-search history.

- [case-detail.jpg](case-detail.jpg): the case detail page awaiting
  sign-off, with provenance, official verification, a versioned assessment
  and the pending human decision. The reading and evaluation came from the
  deterministic fake model; its `fake-model` label is visible.
- [worker-recovery.gif](worker-recovery.gif): a worker crash and recovery
  in the Temporal UI, 22 seconds in five frames. The worker is killed
  (SIGKILL) after the snapshot Activity commits and before Temporal hears
  of it; attempt 2 on a fresh worker returns the same snapshot, and the run
  completes with one record of each kind and each audit action once. Inputs
  and model output are synthetic; the workflow, Activities, PostgreSQL and
  Temporal server are real. The wait for the real 180-second Activity
  timeout is cut and labelled; the frames are otherwise unedited.

The capture scripts and evidence exports are kept outside the repository;
this directory publishes only the media. The crash-recovery demo can be
repeated by hand against the Compose stack, as described in
[development.md](../development.md#running-locally). Raw personal
job-search history never enters this directory.
