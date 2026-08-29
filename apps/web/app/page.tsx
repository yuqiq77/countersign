const principles = [
  "Every recommendation cites source evidence.",
  "Every long-running action is recoverable.",
  "Every approval records the versions reviewed.",
];

export default function HomePage() {
  return (
    <main className="mx-auto flex min-h-screen max-w-4xl flex-col justify-center gap-10 px-6 py-20">
      <div className="space-y-4">
        <p className="text-sm font-semibold uppercase tracking-[0.2em] text-emerald-400">
          Slice 0 · Architecture baseline
        </p>
        <h1 className="text-4xl font-semibold tracking-tight sm:text-6xl">
          Countersign
        </h1>
        <p className="max-w-2xl text-lg leading-8 text-neutral-300">
          A durable, evidence-driven workflow for collecting, verifying, evaluating, and
          approving external job intelligence.
        </p>
      </div>

      <ul className="grid gap-3 sm:grid-cols-3">
        {principles.map((principle) => (
          <li key={principle} className="rounded-2xl border border-neutral-800 p-5 text-neutral-200">
            {principle}
          </li>
        ))}
      </ul>

      <p className="text-sm text-neutral-500">
        Runtime workflows and case screens begin in Slice 1. This page intentionally does not
        imply they already exist.
      </p>
    </main>
  );
}
