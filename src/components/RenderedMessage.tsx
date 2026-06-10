import type { Message } from "../types";
import { formatCost, formatTokens } from "./shared";

export function MessageCost({ message }: { message: Message }) {
  if (!message.total_tokens && !message.total_cost) return null;
  const label = message.role === "user"
    ? `Input ${formatTokens(message.prompt_tokens)} · ${formatCost(message.prompt_cost)}`
    : `Output ${formatTokens(message.completion_tokens || message.total_tokens)} · ${formatCost(message.completion_cost || message.total_cost)}`;
  return <div className="message-cost mono">{label}</div>;
}

export function RenderedMessage({ content }: { content: string }) {
  const blocks = parseMessageBlocks(content);
  return (
    <>
      {blocks.map((block, index) => {
        if (block.type === "code") {
          return <pre className="message-code" key={index}><code>{block.content}</code></pre>;
        }
        if (block.type === "list") {
          return <ul className="message-list" key={index}>{block.items.map((item, itemIndex) => <li key={itemIndex}>{item}</li>)}</ul>;
        }
        return <p key={index}>{block.content}</p>;
      })}
    </>
  );
}

type MessageBlock =
  | { type: "paragraph"; content: string }
  | { type: "code"; content: string }
  | { type: "list"; items: string[] };

function parseMessageBlocks(content: string): MessageBlock[] {
  const lines = content.split("\n");
  const blocks: MessageBlock[] = [];
  let paragraph: string[] = [];
  let list: string[] = [];
  let code: string[] | null = null;

  const flushParagraph = () => {
    if (paragraph.length) {
      blocks.push({ type: "paragraph", content: paragraph.join(" ") });
      paragraph = [];
    }
  };
  const flushList = () => {
    if (list.length) {
      blocks.push({ type: "list", items: list });
      list = [];
    }
  };

  for (const line of lines) {
    if (line.trim().startsWith("```")) {
      if (code) {
        blocks.push({ type: "code", content: code.join("\n") });
        code = null;
      } else {
        flushParagraph();
        flushList();
        code = [];
      }
      continue;
    }
    if (code) {
      code.push(line);
      continue;
    }
    const listMatch = line.match(/^\s*[-*•]\s+(.+)/);
    if (listMatch) {
      flushParagraph();
      list.push(listMatch[1]);
      continue;
    }
    if (!line.trim()) {
      flushParagraph();
      flushList();
      continue;
    }
    flushList();
    paragraph.push(line.trim());
  }
  flushParagraph();
  flushList();
  if (code) blocks.push({ type: "code", content: code.join("\n") });
  return blocks.length ? blocks : [{ type: "paragraph", content }];
}
