import { Remarkable } from 'remarkable'

interface Props {
  content: string
  isUser: boolean
}

const markdown = new Remarkable('full', {
  html: false,
  breaks: true,
  linkTarget: '_blank',
  typographer: true,
  langPrefix: 'language-',
})

function renderMarkdown(content: string): string {
  return markdown
    .render(content)
    .replaceAll('<a ', '<a rel="noopener noreferrer" ')
}

export default function MarkdownMessage({ content, isUser }: Props) {
  const html = renderMarkdown(content)

  return (
    <div
      className={`markdown-message ${isUser ? 'markdown-message-user' : 'markdown-message-assistant'}`}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  )
}
