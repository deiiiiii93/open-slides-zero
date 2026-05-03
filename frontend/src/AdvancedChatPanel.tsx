import { FormEvent, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type {
  AdvancedChatChoice,
  AdvancedChatDraft,
  AdvancedChatMessage,
  DeckState,
} from "./api";
import { Markdown } from "./Markdown";

type Props = {
  deck: DeckState;
  busy: boolean;
  streamingText: string;
  onSend: (message: string) => Promise<void>;
  onContinue: (draft: AdvancedChatDraft) => Promise<void>;
};

function advancedMessages(deck: DeckState): AdvancedChatMessage[] {
  const messages = deck.values?.advanced_chat_messages;
  return Array.isArray(messages) ? messages : [];
}

function advancedDraft(deck: DeckState): AdvancedChatDraft {
  const draft = deck.values?.advanced_chat_draft;
  return draft && typeof draft === "object" ? draft : {};
}

function isDraftReady(draft: AdvancedChatDraft): boolean {
  return Boolean(
    draft.scenario_id &&
      draft.structure_id &&
      Array.isArray(draft.outline_slides) &&
      draft.outline_slides.length > 0 &&
      draft.visual_style &&
      Object.keys(draft.visual_style).length > 0,
  );
}

function draftTitle(draft: AdvancedChatDraft): string {
  const slides = draft.outline_slides?.length ?? 0;
  const parts = [
    draft.scenario_id || "scenario pending",
    draft.structure_id || "structure pending",
    slides ? `${slides} slides` : "outline pending",
  ];
  return parts.join(" · ");
}

function cleanChoiceText(value: string): string {
  return value
    .replace(/\*\*/g, "")
    .replace(/`/g, "")
    .replace(/\s+/g, " ")
    .replace(/^[\s\-–—:]+|[\s\-–—:]+$/g, "");
}

function choicesFromMarkdown(content: string): AdvancedChatChoice[] {
  const choices: AdvancedChatChoice[] = [];
  for (const rawLine of content.split(/\r?\n/)) {
    let line = rawLine.trim();
    while (line.startsWith(">")) {
      line = line.slice(1).trim();
    }
    line = line.replace(/^[-*+]\s+/, "");
    const match = line.match(
      /^(?:\*\*)?\(?([A-Da-d])\)?(?:[.)])?(?:\*\*)?[\s:：\-–—]+(.+)$/,
    );
    if (!match) continue;
    const optionId = match[1].toUpperCase();
    const text = cleanChoiceText(match[2]);
    if (!text) continue;
    choices.push({
      label: optionId,
      description: text.slice(0, 140),
      message: `Choose option ${optionId}: ${text}`,
    });
    if (choices.length >= 4) break;
  }
  return choices.length >= 2 ? choices : [];
}

const STARTER_CHOICES: AdvancedChatChoice[] = [
  {
    label: "Answer-first executive",
    description: "Lead with the recommendation, then prove it.",
    message:
      "Use an answer-first executive structure. Lead with the recommendation, then prove it with evidence and an execution path. Use a restrained strategic visual style.",
  },
  {
    label: "Build the case",
    description: "Use context, tension, answer, proof.",
    message:
      "Use a tension-building structure. Start with context, make the problem or opportunity clear, then reveal the recommendation and proof. Use an editorial analytical style.",
  },
  {
    label: "Compare options",
    description: "Frame alternatives and make a decision.",
    message:
      "Use a comparison-led structure. Show the main options, evaluate tradeoffs, make the decision, and close with next steps. Use a clean analytical style.",
  },
  {
    label: "Visual story",
    description: "Make it narrative and image-led.",
    message:
      "Use a more visual narrative structure. Build the story through scenes, evidence moments, and image-led slides, with an expressive editorial style.",
  },
];

function ChoiceGrid({
  choices,
  queuedMessage,
  onChoose,
}: {
  choices: AdvancedChatChoice[];
  queuedMessage: string | null;
  onChoose: (message: string) => void;
}) {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))",
        gap: 8,
        marginTop: 10,
      }}
    >
      {choices.map((choice) => (
        (() => {
          const isQueued = queuedMessage === choice.message;
          return (
            <button
              key={`${choice.label}-${choice.message}`}
              type="button"
              disabled={isQueued}
              onClick={() => onChoose(choice.message)}
              style={{
                display: "grid",
                gap: 3,
                minHeight: 74,
                padding: "8px 9px",
                border: isQueued ? "1px solid #93c5fd" : "1px solid #cbd5e1",
                borderRadius: 6,
                background: isQueued ? "#eff6ff" : "#f8fafc",
                color: "#0f172a",
                cursor: isQueued ? "default" : "pointer",
                fontFamily: "inherit",
                fontSize: 12,
                textAlign: "left",
              }}
            >
              <span style={{ fontWeight: 700, lineHeight: 1.25 }}>
                {isQueued ? `Queued: ${choice.label}` : choice.label}
              </span>
              {choice.description && (
                <span style={{ color: "#475569", lineHeight: 1.25 }}>
                  {choice.description}
                </span>
              )}
            </button>
          );
        })()
      ))}
    </div>
  );
}

export function AdvancedChatPanel({
  deck,
  busy,
  streamingText,
  onSend,
  onContinue,
}: Props) {
  const [message, setMessage] = useState("");
  const [queuedMessage, setQueuedMessage] = useState<string | null>(null);
  const [continueRequested, setContinueRequested] = useState(false);
  const chatScrollRef = useRef<HTMLDivElement | null>(null);
  const chatEndRef = useRef<HTMLDivElement | null>(null);
  const messages = advancedMessages(deck);
  const draft = advancedDraft(deck);
  const draftReady = isDraftReady(draft);
  const latestAssistantMessageIndex = (() => {
    for (let idx = messages.length - 1; idx >= 0; idx -= 1) {
      const item = messages[idx];
      if (item.role === "assistant") return idx;
    }
    return -1;
  })();
  const draftPreview = useMemo(() => {
    const parts = [
      draft.summary,
      draft.outline_md,
      draft.visual_style_md,
    ].filter((part): part is string => Boolean(part && part.trim()));
    return parts.join("\n\n---\n\n");
  }, [draft.outline_md, draft.summary, draft.visual_style_md]);
  const latestMessage = messages[messages.length - 1];

  useEffect(() => {
    if (busy || !queuedMessage) return;
    const next = queuedMessage;
    setQueuedMessage(null);
    void onSend(next);
  }, [busy, onSend, queuedMessage]);

  useLayoutEffect(() => {
    const el = chatScrollRef.current;
    if (!el) return;
    const scrollToBottom = () => {
      chatEndRef.current?.scrollIntoView({ block: "end" });
      el.scrollTop = el.scrollHeight;
    };
    scrollToBottom();
    const frame = window.requestAnimationFrame(scrollToBottom);
    const lateFrame = window.requestAnimationFrame(() => {
      window.requestAnimationFrame(scrollToBottom);
    });
    const timeout = window.setTimeout(scrollToBottom, 80);
    return () => {
      window.cancelAnimationFrame(frame);
      window.cancelAnimationFrame(lateFrame);
      window.clearTimeout(timeout);
    };
  }, [
    messages.length,
    latestMessage?.content,
    latestMessage?.choices?.length,
    streamingText,
  ]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const text = message.trim();
    if (!text || busy) return;
    setMessage("");
    await onSend(text);
  }

  function chooseOption(nextMessage: string) {
    if (busy) {
      setQueuedMessage(nextMessage);
      return;
    }
    void onSend(nextMessage);
  }

  async function continueToLayout() {
    if (!draftReady || continueRequested) return;
    setContinueRequested(true);
    try {
      await onContinue(draft);
    } finally {
      setContinueRequested(false);
    }
  }

  return (
    <section
      style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))",
        gap: 14,
        alignItems: "start",
      }}
    >
      <div
        style={{
          border: "1px solid #dbe4ef",
          borderRadius: 8,
          background: "#fff",
          overflow: "hidden",
        }}
      >
        <div
          style={{
            padding: "10px 12px",
            borderBottom: "1px solid #e5e7eb",
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            gap: 10,
          }}
        >
          <h3 style={{ margin: 0, fontSize: 16 }}>Advanced planning chat</h3>
          <span style={{ fontSize: 12, color: "#64748b" }}>
            {messages.length} message{messages.length === 1 ? "" : "s"}
          </span>
        </div>

        <div
          ref={chatScrollRef}
          style={{
            height: 460,
            overflowY: "auto",
            padding: 12,
            display: "grid",
            gap: 10,
            background: "#f8fafc",
            overflowAnchor: "none",
          }}
        >
          {messages.length === 0 && !streamingText && (
            <div
              style={{
                justifySelf: "start",
                maxWidth: "84%",
                padding: "9px 11px",
                border: "1px solid #e5e7eb",
                borderRadius: 8,
                background: "#fff",
                color: "#111827",
                fontSize: 13,
              }}
            >
              <Markdown>
                {"## Choose a planning direction\n\nPick a structure/style path below, or type your own preference. I’ll turn your choice into a draft outline and visual direction."}
              </Markdown>
              <ChoiceGrid
                choices={STARTER_CHOICES}
                queuedMessage={queuedMessage}
                onChoose={chooseOption}
              />
            </div>
          )}
          {messages.map((item, idx) => {
            const isUser = item.role === "user";
            const markdownChoices = isUser ? [] : choicesFromMarkdown(item.content);
            const choices =
              !isUser && idx === latestAssistantMessageIndex
                ? (markdownChoices.length ? markdownChoices : item.choices ?? [])
                : [];
            return (
              <div
                key={`${item.role}-${idx}`}
                style={{
                  justifySelf: isUser ? "end" : "start",
                  maxWidth: "84%",
                  padding: "9px 11px",
                  border: isUser ? "1px solid #bfdbfe" : "1px solid #e5e7eb",
                  borderRadius: 8,
                  background: isUser ? "#eff6ff" : "#fff",
                  color: "#111827",
                  fontSize: 13,
                  whiteSpace: isUser ? "pre-wrap" : undefined,
                }}
              >
                {isUser ? item.content : <Markdown>{item.content}</Markdown>}
                {choices.length > 0 && (
                  <ChoiceGrid
                    choices={choices}
                    queuedMessage={queuedMessage}
                    onChoose={chooseOption}
                  />
                )}
              </div>
            );
          })}
          {streamingText && (
            <div
              style={{
                justifySelf: "start",
                maxWidth: "84%",
                padding: "9px 11px",
                border: "1px solid #cbd5e1",
                borderRadius: 8,
                background: "#fff",
                fontSize: 13,
              }}
            >
              <Markdown>{streamingText}</Markdown>
              {choicesFromMarkdown(streamingText).length > 0 && (
                <ChoiceGrid
                  choices={choicesFromMarkdown(streamingText)}
                  queuedMessage={queuedMessage}
                  onChoose={chooseOption}
                />
              )}
            </div>
          )}
          <div ref={chatEndRef} style={{ height: 1 }} />
        </div>

        <form
          onSubmit={submit}
          style={{
            display: "grid",
            gridTemplateColumns: "minmax(0, 1fr) auto",
            gap: 8,
            padding: 10,
            borderTop: "1px solid #e5e7eb",
            background: "#fff",
          }}
        >
          <textarea
            value={message}
            onChange={(event) => setMessage(event.target.value)}
            disabled={busy}
            rows={2}
            placeholder="Reply with audience, structure, style, or tradeoff preferences"
            style={{
              resize: "vertical",
              minHeight: 42,
              maxHeight: 120,
              fontFamily: "inherit",
              fontSize: 14,
              padding: "7px 8px",
              boxSizing: "border-box",
            }}
          />
          <button type="submit" disabled={busy || !message.trim()}>
            {busy ? "Guiding..." : "Reply"}
          </button>
        </form>
      </div>

      <aside
        style={{
          border: "1px solid #e5e7eb",
          borderRadius: 8,
          background: "#fff",
          overflow: "hidden",
        }}
      >
        <div
          style={{
            padding: "10px 12px",
            borderBottom: "1px solid #e5e7eb",
            display: "flex",
            justifyContent: "space-between",
            gap: 10,
            alignItems: "center",
          }}
        >
          <div style={{ minWidth: 0 }}>
            <h3 style={{ margin: 0, fontSize: 16 }}>Current draft</h3>
            <div
              style={{
                color: "#64748b",
                fontSize: 12,
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
                maxWidth: 360,
              }}
            >
              {draftTitle(draft)}
            </div>
          </div>
          <button
            type="button"
            disabled={!draftReady || continueRequested}
            onClick={() => void continueToLayout()}
          >
            {continueRequested ? "Starting layout..." : "Continue to layout"}
          </button>
        </div>
        <div
          style={{
            maxHeight: 574,
            overflowY: "auto",
            padding: 12,
            background: "#fafafa",
            fontSize: 13,
          }}
        >
          {draftPreview ? (
            <Markdown>{draftPreview}</Markdown>
          ) : (
            <div style={{ color: "#64748b" }}>No draft yet.</div>
          )}
        </div>
      </aside>
    </section>
  );
}
