import { useEffect, useRef, useState } from "react";
import type { ChatTurn } from "../api/types";
import Markdown from "./Markdown";

type Props = {
  turns: ChatTurn[];
  onSend: (message: string) => void;
  disabled?: boolean;
  placeholder: string;
  suggestions?: string[];
  title: string;
  intro?: string;
};

export default function ChatPanel({ turns, onSend, disabled, placeholder, suggestions = [], title, intro }: Props) {
  const [text, setText] = useState("");
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth", block: "nearest" }); }, [turns.length]);
  const send = (msg: string) => {
    const m = msg.trim();
    if (!m || disabled) return;
    setText("");
    onSend(m);
  };
  return (
    <section className="chat card">
      <header className="card-head"><h3>{title}</h3></header>
      <div className="chat-log">
        {intro && <div className="chat-turn assistant"><Markdown text={intro} /></div>}
        {turns.map((t, i) => (
          <div key={i} className={`chat-turn ${t.role}`}>
            {t.role === "user" ? <div>{t.content}</div> : <Markdown text={t.content ?? ""} />}
          </div>
        ))}
        <div ref={end} />
      </div>
      {suggestions.length > 0 && (
        <div className="chips">
          {suggestions.map((s) => <button key={s} className="chip" disabled={disabled} onClick={() => send(s)}>{s}</button>)}
        </div>
      )}
      <form className="chat-input" onSubmit={(e) => { e.preventDefault(); send(text); }}>
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={placeholder}
          rows={2}
          disabled={disabled}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(text); } }}
        />
        <button type="submit" className="btn primary" disabled={disabled || !text.trim()}>Send</button>
      </form>
    </section>
  );
}
