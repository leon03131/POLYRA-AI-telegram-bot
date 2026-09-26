// web5: рендер markdown-ответов модели (assistant-сообщения).
//
// GFM (таблицы/списки) + math ($…$ и $$…$$ через remark-math → rehype-katex).
// Стили — секция /* === web5 chat === */ в styles.css (.web5-md), включая
// моноширинные код-блоки с горизонтальным скроллом и скролл таблиц.

import { memo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import "katex/dist/katex.min.css";

interface MarkdownProps {
  text: string;
}

/**
 * Memo: во время стриминга страница перерисовывается на каждую дельту —
 * история не должна парситься заново; стримящееся сообщение парсится каждый
 * раз (текст растёт), это дёшево для обычных объёмов ответа.
 */
export const Markdown = memo(function Markdown({ text }: MarkdownProps) {
  return (
    <div className="web5-md">
      <ReactMarkdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeKatex]}>
        {text}
      </ReactMarkdown>
    </div>
  );
});
