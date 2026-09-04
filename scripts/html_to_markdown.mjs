import { NodeHtmlMarkdown } from 'node-html-markdown';

const chunks = [];
for await (const chunk of process.stdin) chunks.push(chunk);
const html = Buffer.concat(chunks).toString('utf8');
const converter = new NodeHtmlMarkdown();
process.stdout.write(converter.translate(html));
