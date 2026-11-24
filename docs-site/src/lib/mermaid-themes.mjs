// Rehype plugin, run before rehype-mermaid: each ```mermaid block becomes a light and a dark copy, each
// rendered at build time to inline SVG; src/styles/custom.css shows the one that matches the site's theme.
const THEMES = { light: 'default', dark: 'dark' };

export function rehypeMermaidThemes() {
  const visit = (node) => {
    node.children?.forEach((child, i) => {
      const code = child.tagName === 'pre' && child.children?.[0];
      if (code?.tagName === 'code' && code.properties?.className?.includes('language-mermaid')) {
        const source = code.children.map((c) => c.value ?? '').join('');
        node.children[i] = {
          type: 'element',
          tagName: 'div',
          properties: { className: ['diagram'] },
          children: Object.entries(THEMES).map(([mode, theme]) => ({
            type: 'element',
            tagName: 'div',
            properties: { className: [`diagram-${mode}`] },
            children: [{
              ...child,
              children: [{ ...code, children: [{ type: 'text', value: `%%{init: {"theme": "${theme}"}}%%\n${source}` }] }],
            }],
          })),
        };
      } else visit(child);
    });
  };
  return visit;
}
