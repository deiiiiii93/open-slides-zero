// Minimal SSE reader using fetch + ReadableStream (EventSource doesn't support
// POST requests with JSON bodies, which is what our streaming endpoints need).

import { runtimeConfigHeaders } from "./runtimeConfig";

export type StreamEvent =
  | { type: "thread"; thread_id: string; parent_thread_id?: string; lane_id?: string }
  | { type: "lane"; lane: any }
  | { type: "token"; tag: string | null; text: string }
  | {
      type: "event";
      node: string;
      state: "started" | "finished" | "error";
      slide_idx?: number;
      error?: string;
      model?: string;
      reasoning_effort?: string;
    }
  | { type: "update"; node: string; patch: Record<string, any> }
  | { type: "interrupt"; payload: any }
  | { type: "done"; state: any; result?: any }
  | { type: "comment_saved"; slide_idx: number; text: string }
  | { type: "error"; message: string };

export async function* streamSSE(
  url: string,
  body: unknown | FormData,
  signal?: AbortSignal,
): AsyncGenerator<StreamEvent> {
  const isFormData = typeof FormData !== "undefined" && body instanceof FormData;
  const headers = runtimeConfigHeaders();
  const res = await fetch(url, {
    method: "POST",
    headers: isFormData
      ? { Accept: "text/event-stream", ...headers }
      : { "Content-Type": "application/json", Accept: "text/event-stream", ...headers },
    credentials: "same-origin",
    body: isFormData ? body : JSON.stringify(body),
    signal,
  });
  if (!res.ok || !res.body) {
    const text = res.body ? await res.text() : "";
    throw new Error(`SSE failed: ${res.status} ${text}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // SSE frames are separated by blank lines.
    let sepIdx: number;
    while ((sepIdx = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, sepIdx);
      buffer = buffer.slice(sepIdx + 2);
      for (const line of frame.split("\n")) {
        if (!line.startsWith("data:")) continue;
        const jsonStr = line.slice(5).trim();
        if (!jsonStr) continue;
        try {
          yield JSON.parse(jsonStr) as StreamEvent;
        } catch {
          // ignore malformed frames; backend shouldn't emit them
        }
      }
    }
  }
}
