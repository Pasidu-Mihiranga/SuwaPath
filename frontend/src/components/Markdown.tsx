/**
 * A small, deliberately limited markdown renderer.
 *
 * Why not `react-markdown`? It pulls in remark, micromark and a dozen
 * transitive packages to render text that a language model produced — text we
 * do not fully control. The smaller the surface that touches model output, the
 * better. This renderer supports exactly the syntax the assistant is told to
 * emit and treats everything else as literal text.
 *
 * Nothing here can inject HTML: every value ends up as a React text child, so
 * a model that emits `<script>` renders those characters and nothing happens.
 * Links are the one exception and are restricted to http(s), rendered with
 * `rel="noopener noreferrer"`.
 */

interface MarkdownProps {
  content: string;
  className?: string;
}

type Block =
  | { kind: "heading"; text: string }
  | { kind: "paragraph"; text: string }
  | { kind: "list"; ordered: boolean; items: string[] }
  | { kind: "quote"; text: string };

const BULLET = /^\s*[-*•]\s+(.*)$/;
const ORDERED = /^\s*\d+[.)]\s+(.*)$/;
const QUOTE = /^\s*>\s?(.*)$/;
// A line that is only bold text is a section heading in everything the
// assistant writes ("**What I'd do**").
const BOLD_ONLY = /^\s*\*\*(.+?)\*\*[:：]?\s*$/;
const ATX_HEADING = /^\s*#{1,6}\s+(.*)$/;

function parse(source: string): Block[] {
  const blocks: Block[] = [];
  const lines = (source || "").replace(/\r\n/g, "\n").split("\n");

  let paragraph: string[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;

  const flushParagraph = () => {
    if (paragraph.length) {
      blocks.push({ kind: "paragraph", text: paragraph.join(" ") });
      paragraph = [];
    }
  };
  const flushList = () => {
    if (list) {
      blocks.push({ kind: "list", ...list });
      list = null;
    }
  };
  const flushAll = () => {
    flushParagraph();
    flushList();
  };

  for (const line of lines) {
    if (!line.trim()) {
      flushAll();
      continue;
    }

    const atx = ATX_HEADING.exec(line);
    const boldOnly = BOLD_ONLY.exec(line);
    if (atx || boldOnly) {
      flushAll();
      blocks.push({ kind: "heading", text: (atx?.[1] ?? boldOnly![1]).trim() });
      continue;
    }

    const quote = QUOTE.exec(line);
    if (quote) {
      flushAll();
      blocks.push({ kind: "quote", text: quote[1].trim() });
      continue;
    }

    const bullet = BULLET.exec(line);
    const ordered = ORDERED.exec(line);
    if (bullet || ordered) {
      flushParagraph();
      const isOrdered = Boolean(ordered);
      // A change of list type starts a new list rather than mixing markers.
      if (!list || list.ordered !== isOrdered) {
        flushList();
        list = { ordered: isOrdered, items: [] };
      }
      list.items.push((bullet?.[1] ?? ordered![1]).trim());
      continue;
    }

    flushList();
    paragraph.push(line.trim());
  }

  flushAll();
  return blocks;
}

/** Inline `**bold**`, `_italic_`, `` `code` `` and bare links. */
function inline(text: string, keyPrefix: string): React.ReactNode[] {
  const nodes: React.ReactNode[] = [];
  // One pass over an alternation keeps the markers from nesting incorrectly.
  const pattern =
    /(\*\*[^*]+\*\*)|(__[^_]+__)|(`[^`]+`)|(_[^_\n]+_)|(\*[^*\n]+\*)|(https?:\/\/[^\s<>)]+)/g;

  let last = 0;
  let match: RegExpExecArray | null;
  let index = 0;

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > last) {
      nodes.push(text.slice(last, match.index));
    }
    const token = match[0];
    const key = `${keyPrefix}-${index++}`;

    if (token.startsWith("**") || token.startsWith("__")) {
      nodes.push(
        <strong key={key} className="font-semibold text-ink-900">
          {token.slice(2, -2)}
        </strong>,
      );
    } else if (token.startsWith("`")) {
      nodes.push(
        <code
          key={key}
          className="rounded bg-canvas border border-line px-1 py-0.5 text-[0.85em] font-mono"
        >
          {token.slice(1, -1)}
        </code>,
      );
    } else if (token.startsWith("http")) {
      nodes.push(
        <a
          key={key}
          href={token}
          target="_blank"
          rel="noopener noreferrer"
          className="text-brand-700 underline underline-offset-2 break-all"
        >
          {token}
        </a>,
      );
    } else {
      nodes.push(
        <em key={key} className="italic">
          {token.slice(1, -1)}
        </em>,
      );
    }
    last = match.index + token.length;
  }

  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

export default function Markdown({ content, className = "" }: MarkdownProps) {
  const blocks = parse(content);

  return (
    <div className={`space-y-2.5 leading-relaxed ${className}`}>
      {blocks.map((block, index) => {
        const key = `b${index}`;
        switch (block.kind) {
          case "heading":
            return (
              <p
                key={key}
                className="font-semibold text-ink-900 text-[0.95rem] pt-1 first:pt-0"
              >
                {inline(block.text, key)}
              </p>
            );
          case "quote":
            return (
              <p
                key={key}
                className="border-l-2 border-brand-300 pl-3 text-ink-600 text-sm"
              >
                {inline(block.text, key)}
              </p>
            );
          case "list":
            return block.ordered ? (
              <ol key={key} className="list-decimal pl-5 space-y-1 marker:text-ink-400">
                {block.items.map((item, i) => (
                  <li key={i}>{inline(item, `${key}-${i}`)}</li>
                ))}
              </ol>
            ) : (
              <ul key={key} className="space-y-1">
                {block.items.map((item, i) => (
                  <li key={i} className="flex gap-2">
                    <span
                      aria-hidden
                      className="mt-[0.55em] h-1.5 w-1.5 shrink-0 rounded-full bg-brand-400"
                    />
                    <span className="min-w-0">{inline(item, `${key}-${i}`)}</span>
                  </li>
                ))}
              </ul>
            );
          default:
            return <p key={key}>{inline(block.text, key)}</p>;
        }
      })}
    </div>
  );
}
