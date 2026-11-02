"use client";

import { FormEvent, useState } from "react";

export interface ChatMessage {
  id: number;
  role: "user" | "assistant";
  text: string;
  isError: boolean;
}

const MAX_MESSAGE_LENGTH = 2000;

export default function Chat({
  messages,
  progress,
  busy,
  hasPlan,
  onSend,
  onStartOver,
}: {
  messages: ChatMessage[];
  progress: string | null;
  busy: boolean;
  hasPlan: boolean;
  onSend: (text: string) => void;
  onStartOver?: () => void;
}) {
  const [input, setInput] = useState("");

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    const text = input.trim();
    if (!text || busy) return;
    setInput("");
    onSend(text);
  }

  return (
    <section aria-label="Chat with the planner" className="space-y-3 rounded-2xl border border-ink/10 bg-white/70 p-4 shadow-sm sm:p-6">
      {messages.length === 0 && (
        <p className="text-sm text-ink/70">Try: &quot;3 days in Paris with 300 euros, I love art.&quot;</p>
      )}

      {messages.length > 0 && (
        <ul className="max-h-96 space-y-3 overflow-y-auto">
          {messages.map((message) => (
            <li key={message.id} className={`flex ${message.role === "user" ? "justify-end" : "justify-start"}`}>
              <span
                className={`max-w-[85%] break-words rounded-2xl px-4 py-2.5 text-sm ${
                  message.role === "user" ? "bg-ink text-paper" : message.isError ? "bg-red-50 text-red-900" : "bg-clay/10 text-ink"
                }`}
              >
                {message.text}
              </span>
            </li>
          ))}
        </ul>
      )}

      <form onSubmit={handleSubmit} className="flex flex-col gap-3 sm:flex-row">
        <label htmlFor="trip-message" className="sr-only">
          {hasPlan ? "Ask for a change" : "Describe your trip"}
        </label>
        <input
          id="trip-message"
          type="text"
          value={input}
          maxLength={MAX_MESSAGE_LENGTH}
          onChange={(event) => setInput(event.target.value)}
          placeholder={hasPlan ? "Ask for a change, like replacing a stop" : "Where do you want to go?"}
          disabled={busy}
          className="min-w-0 flex-1 rounded-lg border border-ink/20 bg-paper px-4 py-2.5 text-ink placeholder:text-ink/60 disabled:opacity-60"
        />
        <button
          type="submit"
          disabled={busy || !input.trim()}
          className="shrink-0 rounded-lg bg-ink px-5 py-2.5 font-medium text-paper transition hover:bg-ink/90 disabled:cursor-not-allowed disabled:opacity-60"
        >
          Send
        </button>
      </form>

      <div aria-live="polite">
        {progress && (
          <p className="flex items-center gap-2 text-sm text-ink/80">
            <span className="h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-clay" />
            <span>{progress}. A trip can take a few minutes.</span>
          </p>
        )}
      </div>

      {onStartOver && (
        <button onClick={onStartOver} disabled={busy} className="text-sm text-clay underline underline-offset-4 hover:text-ink disabled:opacity-60">
          Start a new trip
        </button>
      )}
    </section>
  );
}
