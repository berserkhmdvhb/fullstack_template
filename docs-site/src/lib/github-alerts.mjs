// Remark plugin: a GitHub alert (`> [!NOTE]`, `> [!WARNING]`, ...) in a Markdown file becomes the `:::` directive
// Starlight renders as an aside, so a loaded wiki file keeps plain GitHub Markdown and still gets the site's asides.
// It runs before Starlight's own remark plugins (astro.config.mjs), which turn the directive into the aside.
const VARIANTS = {
  NOTE: ['note', 'Note'],
  TIP: ['tip', 'Tip'],
  IMPORTANT: ['note', 'Important'],
  WARNING: ['caution', 'Warning'],
  CAUTION: ['danger', 'Caution'],
};
const MARKER = /^\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\][ \t]*(?:\r?\n|$)/;

export function remarkGithubAlerts() {
  const visit = (node) => {
    node.children?.forEach((child, i) => {
      const first = child.type === 'blockquote' && child.children[0];
      const text = first?.type === 'paragraph' && first.children[0];
      const found = text?.type === 'text' && MARKER.exec(text.value);
      if (!found) return visit(child);
      const [name, label] = VARIANTS[found[1]];
      text.value = text.value.slice(found[0].length);
      if (!text.value) first.children.shift();
      if (first.children[0]?.type === 'break') first.children.shift();
      const body = first.children.length ? child.children : child.children.slice(1);
      node.children[i] = {
        type: 'containerDirective',
        name,
        attributes: {},
        children: [{ type: 'paragraph', data: { directiveLabel: true }, children: [{ type: 'text', value: label }] }, ...body],
      };
      visit(node.children[i]);
    });
  };
  return visit;
}
