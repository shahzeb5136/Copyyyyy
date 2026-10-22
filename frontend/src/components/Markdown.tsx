import ReactMarkdown from "react-markdown";

/** Markdown from the model is rendered without raw HTML (react-markdown's default), so it is safe. */
export default function Markdown({ text, className }: { text: string; className?: string }) {
  return (
    <div className={`md ${className ?? ""}`}>
      <ReactMarkdown>{text}</ReactMarkdown>
    </div>
  );
}
