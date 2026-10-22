const STEPS = ["Upload", "Check data", "Describe columns", "Dashboard"] as const;

export default function Stepper({ current }: { current: number }) {
  return (
    <ol className="stepper" aria-label="Progress">
      {STEPS.map((label, i) => (
        <li key={label} className={i === current ? "active" : i < current ? "done" : ""}>
          <span className="step-num">{i < current ? "✓" : i + 1}</span>
          <span className="step-label">{label}</span>
        </li>
      ))}
    </ol>
  );
}
