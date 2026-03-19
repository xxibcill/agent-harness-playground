const surfaces = [
  {
    name: "Control Plane",
    detail: "Trigger runs, inspect history, and fetch read models from the Python API.",
  },
  {
    name: "Execution Plane",
    detail: "Track LangGraph node progress, model calls, and tool activity from worker events.",
  },
  {
    name: "Observability Plane",
    detail: "Render traces, metrics, and failures with a shared run identifier across systems.",
  },
];

export default function HomePage() {
  return (
    <main className="shell">
      <section className="hero">
        <p className="eyebrow">Task 1 foundation</p>
        <h1>Agent Harness monorepo scaffold</h1>
        <p className="lede">
          This Next.js app is the future monitoring client. Agent orchestration remains in the
          Python API and worker services.
        </p>
      </section>
      <section className="grid">
        {surfaces.map((surface) => (
          <article className="card" key={surface.name}>
            <h2>{surface.name}</h2>
            <p>{surface.detail}</p>
          </article>
        ))}
      </section>
    </main>
  );
}

