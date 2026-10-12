import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { NodeHtmlMarkdown } = require(process.argv[2] || 'node-html-markdown');

const chunks = [];
for await (const chunk of process.stdin) chunks.push(chunk);
const html = Buffer.concat(chunks).toString('utf8');
// Literal angle brackets in prose must not become active HTML in Markdown.
const converter = new NodeHtmlMarkdown({ textReplace: [[/[<>]/g, '\\$&']] }, {
  pre: ({node}) => {
    // A highlighted shell variable is literal code, not Markdown emphasis.
    const content = node.textContent;
    const runs = content.match(/`+/g) || [];
    const fence = '`'.repeat(runs.reduce((width, run) => Math.max(width, run.length + 1), 3));
    const language = node.querySelector('code')?.getAttribute('class')?.match(/language-([\w+-]+)/)?.[1] || '';
    return {content, prefix: fence + language + '\n', postfix: '\n' + fence,
      recurse: false, noEscape: true, preserveWhitespace: true, surroundingNewlines: 2};
  },
});
process.stdout.write(converter.translate(html));
